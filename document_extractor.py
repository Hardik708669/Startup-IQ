"""Document text extraction service for Startup IQ.

Safely extracts textual and structured information from uploaded founder
attachments: PDF, DOCX, TXT, CSV, and XLSX.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from docx import Document
import openpyxl
from pypdf import PdfReader
from pypdf.errors import PdfReadError

DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB limit
DEFAULT_MAX_TEXT_CHARS = 50_000        # 50k characters limit for analysis
DEFAULT_MAX_TABLE_ROWS = 100           # Max rows to extract per spreadsheet/table
DEFAULT_MAX_TABLE_COLS = 20            # Max columns per row

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv", ".xlsx"}


class ExtractionResult:
    """Encapsulates the result of a document text extraction."""

    def __init__(
        self,
        success: bool,
        text: str = "",
        error: str | None = None,
        file_type: str = "",
        char_count: int = 0,
        metadata: dict[str, Any] | None = None,
        is_scanned_or_empty: bool = False,
    ):
        self.success = success
        self.text = text
        self.error = error
        self.file_type = file_type
        self.char_count = char_count
        self.metadata = metadata or {}
        self.is_scanned_or_empty = is_scanned_or_empty

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "text": self.text,
            "error": self.error,
            "file_type": self.file_type,
            "char_count": self.char_count,
            "metadata": self.metadata,
            "is_scanned_or_empty": self.is_scanned_or_empty,
        }


def extract_document_text(
    file_bytes: bytes,
    filename: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    max_rows: int = DEFAULT_MAX_TABLE_ROWS,
    max_cols: int = DEFAULT_MAX_TABLE_COLS,
) -> ExtractionResult:
    """Extract selectable text and structured tabular data safely from document bytes."""
    # 1. Basic size & emptiness validation
    if not file_bytes:
        return ExtractionResult(
            success=False,
            error="The uploaded document is empty (0 bytes).",
            is_scanned_or_empty=True,
        )

    if len(file_bytes) > max_bytes:
        return ExtractionResult(
            success=False,
            error=f"File exceeds maximum allowed size of {max_bytes // (1024 * 1024)} MB.",
        )

    # 2. Path & extension sanitization
    safe_name = Path(filename).name
    ext = Path(safe_name).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        return ExtractionResult(
            success=False,
            error=f"Unsupported file format '{ext}'. Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}.",
            file_type=ext,
        )

    # 3. Format-specific parsers
    try:
        if ext == ".pdf":
            return _extract_pdf(file_bytes, max_text_chars)
        elif ext == ".docx":
            return _extract_docx(file_bytes, max_text_chars, max_rows, max_cols)
        elif ext == ".txt":
            return _extract_txt(file_bytes, max_text_chars)
        elif ext == ".csv":
            return _extract_csv(file_bytes, max_text_chars, max_rows, max_cols)
        elif ext == ".xlsx":
            return _extract_xlsx(file_bytes, max_text_chars, max_rows, max_cols)
        else:
            return ExtractionResult(success=False, error=f"Unhandled format {ext}", file_type=ext)
    except Exception as exc:
        return ExtractionResult(
            success=False,
            error=f"Failed to extract document contents: {str(exc)}",
            file_type=ext,
        )


def _extract_pdf(file_bytes: bytes, max_chars: int) -> ExtractionResult:
    """Extract text from text-based PDF using pypdf."""
    stream = io.BytesIO(file_bytes)
    try:
        reader = PdfReader(stream)
        if reader.is_encrypted:
            try:
                # Try empty password decrypt
                decrypt_success = reader.decrypt("")
                if not decrypt_success:
                    return ExtractionResult(
                        success=False,
                        error="PDF is password-protected and cannot be read without credentials.",
                        file_type="pdf",
                    )
            except Exception:
                return ExtractionResult(
                    success=False,
                    error="PDF is password-protected and cannot be parsed.",
                    file_type="pdf",
                )

        num_pages = len(reader.pages)
        extracted_chunks: list[str] = []
        total_len = 0

        for page_idx, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            if page_text.strip():
                chunk = f"--- Page {page_idx + 1} ---\n{page_text.strip()}"
                extracted_chunks.append(chunk)
                total_len += len(chunk)
                if total_len >= max_chars:
                    break

        full_text = "\n\n".join(extracted_chunks).strip()
        if not full_text:
            return ExtractionResult(
                success=True,
                text="",
                file_type=".pdf",
                char_count=0,
                metadata={"pages": num_pages},
                is_scanned_or_empty=True,
                error="No selectable text found. Scanned image-only PDFs require OCR which is not enabled.",
            )

        truncated_text = full_text[:max_chars]
        return ExtractionResult(
            success=True,
            text=truncated_text,
            file_type=".pdf",
            char_count=len(truncated_text),
            metadata={"pages": num_pages, "truncated": len(full_text) > max_chars},
        )
    except PdfReadError as err:
        return ExtractionResult(success=False, error=f"Corrupted or invalid PDF: {err}", file_type=".pdf")


def _extract_docx(file_bytes: bytes, max_chars: int, max_rows: int, max_cols: int) -> ExtractionResult:
    """Extract paragraphs and table texts from Microsoft Word DOCX."""
    stream = io.BytesIO(file_bytes)
    try:
        doc = Document(stream)
        lines: list[str] = []
        current_len = 0

        for p in doc.paragraphs:
            text = p.text.strip()
            if text:
                lines.append(text)
                current_len += len(text)
                if current_len >= max_chars:
                    break

        # Also extract table text
        if current_len < max_chars and doc.tables:
            for table_idx, table in enumerate(doc.tables[:5]):  # Up to 5 tables
                lines.append(f"\n[Table {table_idx + 1}]")
                for row in table.rows[:max_rows]:
                    row_cells = [cell.text.strip() for cell in row.cells[:max_cols]]
                    row_line = " | ".join(row_cells)
                    if row_line.strip():
                        lines.append(row_line)
                        current_len += len(row_line)
                        if current_len >= max_chars:
                            break
                if current_len >= max_chars:
                    break

        full_text = "\n".join(lines).strip()
        if not full_text:
            return ExtractionResult(
                success=True,
                text="",
                file_type="docx",
                is_scanned_or_empty=True,
                error="Document contains no text paragraphs or tables.",
            )

        truncated = full_text[:max_chars]
        return ExtractionResult(
            success=True,
            text=truncated,
            file_type=".docx",
            char_count=len(truncated),
            metadata={"paragraphs": len(doc.paragraphs), "tables": len(doc.tables)},
        )
    except Exception as err:
        return ExtractionResult(success=False, error=f"Corrupted or invalid Word document: {err}", file_type=".docx")


def _extract_txt(file_bytes: bytes, max_chars: int) -> ExtractionResult:
    """Extract plain text handling UTF-8, latin-1, and utf-16 encodings."""
    decoded_text: str | None = None
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252", "utf-16"):
        try:
            decoded_text = file_bytes.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if decoded_text is None:
        return ExtractionResult(
            success=False,
            error="Unable to decode text file. Unsupported character encoding.",
            file_type="txt",
        )

    clean_text = decoded_text.strip()
    if not clean_text:
        return ExtractionResult(
            success=True,
            text="",
            file_type="txt",
            is_scanned_or_empty=True,
            error="The text file is empty.",
        )

    truncated = clean_text[:max_chars]
    return ExtractionResult(
        success=True,
        text=truncated,
        file_type=".txt",
        char_count=len(truncated),
        metadata={"encoding": encoding},
    )


def _extract_csv(file_bytes: bytes, max_chars: int, max_rows: int, max_cols: int) -> ExtractionResult:
    """Extract structured data from CSV files."""
    text_content = None
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            text_content = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue

    if text_content is None:
        return ExtractionResult(success=False, error="Unable to decode CSV file.", file_type="csv")

    try:
        reader = csv.reader(io.StringIO(text_content))
        lines: list[str] = []
        row_count = 0
        current_len = 0

        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            row_count += 1
            if row_count > max_rows:
                lines.append(f"... [Truncated at {max_rows} rows]")
                break
            cells = [cell.strip() for cell in row[:max_cols]]
            line_str = " | ".join(cells)
            lines.append(line_str)
            current_len += len(line_str)
            if current_len >= max_chars:
                break

        full_text = "\n".join(lines).strip()
        if not full_text:
            return ExtractionResult(
                success=True,
                text="",
                file_type="csv",
                is_scanned_or_empty=True,
                error="CSV contains no records.",
            )

        truncated = full_text[:max_chars]
        return ExtractionResult(
            success=True,
            text=truncated,
            file_type=".csv",
            char_count=len(truncated),
            metadata={"extracted_rows": row_count},
        )
    except Exception as err:
        return ExtractionResult(success=False, error=f"Malformed CSV data: {err}", file_type=".csv")


def _extract_xlsx(file_bytes: bytes, max_chars: int, max_rows: int, max_cols: int) -> ExtractionResult:
    """Extract worksheet contents from Excel spreadsheets using openpyxl."""
    stream = io.BytesIO(file_bytes)
    try:
        # data_only=True evaluates formulas to cached values
        wb = openpyxl.load_workbook(stream, read_only=True, data_only=True)
        sheet_chunks: list[str] = []
        current_len = 0

        for sheetname in wb.sheetnames[:5]:  # Limit to first 5 sheets
            ws = wb[sheetname]
            sheet_lines: list[str] = [f"=== Sheet: {sheetname} ==="]
            row_idx = 0

            for row in ws.iter_rows(values_only=True):
                if not row or not any(v is not None and str(v).strip() for v in row):
                    continue
                row_idx += 1
                if row_idx > max_rows:
                    sheet_lines.append(f"... [Sheet truncated at {max_rows} rows]")
                    break
                row_vals = [str(c).strip() if c is not None else "" for c in row[:max_cols]]
                row_str = " | ".join(row_vals)
                sheet_lines.append(row_str)
                current_len += len(row_str)
                if current_len >= max_chars:
                    break

            if len(sheet_lines) > 1:
                sheet_chunks.append("\n".join(sheet_lines))
            if current_len >= max_chars:
                break

        wb.close()
        full_text = "\n\n".join(sheet_chunks).strip()
        if not full_text:
            return ExtractionResult(
                success=True,
                text="",
                file_type="xlsx",
                is_scanned_or_empty=True,
                error="Spreadsheet contains no text or numeric data in worksheets.",
            )

        truncated = full_text[:max_chars]
        return ExtractionResult(
            success=True,
            text=truncated,
            file_type=".xlsx",
            char_count=len(truncated),
            metadata={"sheets": wb.sheetnames},
        )
    except Exception as err:
        return ExtractionResult(success=False, error=f"Corrupted or invalid Excel spreadsheet: {err}", file_type=".xlsx")
