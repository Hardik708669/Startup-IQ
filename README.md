# Startup IQ

Startup IQ is an analytical indicator for evaluating startup ideas—not a magic prediction, funding guarantee, or substitute for professional investment advice.

## Current stage

The local MVP supports startup intake, AI-powered idea understanding and structured extraction, seven-dimension assessment, deterministic scoring, evidence-gap flags, rule-based recommendations, downloadable HTML reports, and local SQLite assessment history.

## AI Idea Understanding & Structured Information Extraction (Phase 5)

Startup IQ provides an optional AI-assisted natural-language understanding layer:
- **Founder Natural-Language Input**: Founders can describe their startup idea in raw, unstructured prose.
- **Structured Field Extraction**: The AI extracts 25 candidate attributes across 8 core categories (Startup identity, Problem, Solution, Customer segment, Business model, Market/competition, Financial assumptions, and Risks).
- **Directly Stated vs. Inferred**: The system identifies whether a suggestion is explicitly present in the text (with source excerpts) or inferred, alongside a confidence rating.
- **Human-in-the-Loop Confirmation**: Suggestions start as `Pending review`. Founders can edit values (`Edited by founder`), approve (`Approved`), or reject (`Rejected`) suggestions. No field is applied to the startup profile or written to the database without explicit founder confirmation.
- **Missing Information Detection**: Analyzes the description to detect critical gaps (e.g., undefined pricing, vague target customer, missing cost structure) and pairs them with why it matters and recommended evidence.
- **Evidence Registry Integration**: When validation claims are mentioned (e.g., customer interviews, surveys, LOIs, pilots), the AI formats candidate evidence records with source excerpts. Upon approval, they are registered directly into the Evidence Registry.
- **Strict Separation from Scoring**: AI **never** computes or alters numeric scores, nor does it modify dimension weights. Numerical readiness remains 100% deterministic.
- **Zero-Dependency Resilience**: External providers (OpenAI, Gemini) are supported via `AI_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY`. If keys are absent or network errors/timeouts occur, Startup IQ gracefully uses a high-performance local heuristic extraction engine without failing. Manual profile entry remains 100% functional without AI.

## Evidence Inbox / Registry

The Evidence Inbox stores structured evidence separately from assessment ratings. Records include a title, claim/description, category, related rubric dimension, source details, evidence date, founder notes, and a verification status. Supported categories include customer interviews, surveys, market and competitor research, financial assumptions, pricing validation, letters of intent, pilots, benchmarks, legal/regulatory research, technical documentation, and custom categories.

Verification statuses are **Not reviewed**, **Founder submitted**, **Reviewed by founder**, **Independently verified**, and **Needs follow-up**. These labels describe the submitted review state; Startup IQ does not independently verify claims. They are separate from the scoring classifications Supported information, Estimate, Unvalidated hypothesis, and Not yet known.

Attachments may be PDF, DOCX, XLSX, CSV, TXT, PNG, JPG, or JPEG up to 10 MB. They are stored with UUID-generated filenames under the ignored local `data/attachments/` directory. Original filenames are metadata only, and attachment contents and private paths are not included in reports. Historical assessments store an immutable evidence snapshot; later edits or archiving do not rewrite history.

## Founder Document Generation (Phase 6)

After completing an assessment, founders can generate professional, editable documents directly from validated profile data, scores, and registered evidence.

### Business Plan (DOCX)

Generates a 22-section Microsoft Word document covering:

Executive Summary, Company Overview, Problem Statement, Proposed Solution, Product / Service Description, Value Proposition, Target Market, Market Opportunity, Competitive Landscape, Competitive Advantage, Business Model, Revenue Model, Pricing Strategy, Go-To-Market Strategy, Operational Plan, Technology Plan, Team & Management, Financial Plan, Funding Requirements, Risk Assessment, Milestones & Roadmap, and an Investor-Readiness Appendix.

Sections that require founder input (e.g., exact market size figures, team bios) are flagged with amber `[Founder input required: ...]` prompts. The document includes a professional disclaimer on the cover page.

### Investor Pitch Deck (PPTX)

Generates a 12-slide 16:9 widescreen PowerPoint presentation covering:

Cover, Problem Statement, Our Solution, Product / Technology, Market Opportunity, Business Model, Traction & Validation, Competitive Position, Team, Financial Snapshot, Funding Ask, and Contact.

Slides use a dark professional colour scheme with a cover gradient, white body slides, and per-slide evidence-count footers.

### Generating Documents

1. Complete and save an assessment.
2. Scroll below the Recommendations panel to **Founder Document Generator (DOCX & PPTX)**.
3. Select **Business Plan (Word DOCX)** or **Investor Pitch Deck (PowerPoint PPTX)**.
4. Optionally expand **Preview & Edit** to review or customise sections before export.
5. Click **Generate Business Plan (DOCX)** or **Generate Pitch Deck (PPTX)**.
6. Click the **Download** button to save the file.

Generated files are stored under `data/documents/` (gitignored). Version metadata is recorded in the local SQLite database and shown in the **Previously Generated Document Versions** panel.

Historical assessments also support document generation from their immutable saved snapshots.

### Required dependencies

```
python-docx >= 1.1.0
python-pptx >= 1.0.0
```

These are included in `requirements.txt`.

### Running browser verification tests

```powershell
python run_browser_verification.py
```

Requires Microsoft Edge installed and `playwright` (`pip install playwright && playwright install`).
Screenshots are saved to the `reports/` directory.

## Rubric

Market Potential 20%, Problem & Solution Clarity 15%, Competitive Position 15%, Business Model 15%, Financial Readiness 15%, Risk Management 10%, Pitch Readiness 10%. Ratings are 0–100 and weighted deterministically. AI, when added later, must never determine the final score.

## Stack and structure

The project uses Python, Streamlit, Plotly, SQLite (`sqlite3`), `python-dotenv`, and pytest. `app.py` is the UI entry point; `scoring.py` owns the canonical rubric; `assessment.py` validates inputs; `dashboard.py` formats result data; `recommendations.py` provides deterministic guidance; `report_generator.py` creates HTML reports; and `database.py` stores local history. `docs/` contains scope and assessment specifications, `sample_data/` contains fictional data, and `tests/` contains automated tests.

## Windows PowerShell setup

```powershell
python --version
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the application:

```powershell
python -m streamlit run app.py
```

Run tests:

```powershell
python -m pytest -q
```

## Local persistence and history

When a valid assessment is submitted, Startup IQ initializes and writes to `data/startup_iq.db`. It stores an immutable startup profile snapshot, assessment inputs, score, rubric version, all seven dimension results, evidence statuses, and recommendations. Identical profile and assessment inputs are idempotent, so Streamlit reruns do not create duplicate records.

Use the **Assessment history** selector in the sidebar to open saved assessments and download historical reports. Selecting history does not overwrite the active assessment. History is local to this project and is not multi-user storage, cloud backup, or secure account isolation. The database contains user-generated startup information and must not be committed to Git.

After submitting a startup profile, use **Evidence Inbox / Registry** to register, search, filter, update, or archive evidence. Evidence associated with the startup is captured when an assessment is saved and is then available in its historical report.

Copy `.env.example` to `.env` only when optional future AI configuration is needed. No credentials are required for the current app.

## Limitations and next steps

Authentication, multi-user collaboration, complex forecasting, cloud infrastructure, MySQL migration, and OCR / PDF content parsing remain outside the current MVP. SQLite is an intentional local-MVP deviation from the original MySQL blueprint; it does not provide multi-user isolation or cloud backups. The next improvements should focus on integration coverage, UI polish, and deployment preparation.
