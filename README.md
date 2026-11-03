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

## Document Text Extraction & AI Evidence Analysis

Founders can upload supporting validation documents to extract textual evidence and identify validation gaps:
- **Supported Formats**: Text-based PDF (`.pdf` via `pypdf`), Word (`.docx` via `python-docx`), Plain Text (`.txt` supporting UTF-8, Latin-1, and UTF-16), CSV spreadsheets (`.csv`), and Excel workbooks (`.xlsx` via `openpyxl`).
- **Configurable Safety Limits**: Max 10 MB per file, max 50,000 characters per document, max 100 rows per spreadsheet/table.
- **Candidate Claim Identification**: Automatically identifies customer interview quotes, survey metrics, LOIs and pilot contracts, financial and pricing assumptions, and competitor benchmarks.
- **Supporting Excerpts**: Every extracted claim maintains an exact quotation/excerpt from the uploaded document to prevent fabrication.
- **Human-in-the-Loop Confirmation**: Founders inspect the extracted text preview and individual claims. Each claim can be approved or rejected. Approved claims are automatically saved into the Evidence Registry with the uploaded file associated as an attachment.
- **Zero-Dependency Resilience**: Works out of the box using a local heuristic pattern matching engine without requiring external LLM API keys.

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
python -m playwright install chromium
python run_browser_verification.py
```

The browser verification suite uses Playwright-managed Chromium by default. Set
`PLAYWRIGHT_BROWSER=msedge` to use an installed Microsoft Edge channel instead.
Screenshots are saved to the temporary verification workspace and are not part
of the repository.

## Rubric

Market Potential 20%, Problem & Solution Clarity 15%, Competitive Position 15%, Business Model 15%, Financial Readiness 15%, Risk Management 10%, Pitch Readiness 10%. Ratings are 0–100 and weighted deterministically. AI, when added later, must never determine the final score.

## Stack and structure

The project uses Python, Streamlit, Plotly, SQLite (`sqlite3`), `python-dotenv`, and pytest. `app.py` is the UI entry point; `scoring.py` owns the canonical rubric; `assessment.py` validates inputs; `dashboard.py` formats result data; `recommendations.py` provides deterministic guidance; `report_generator.py` creates HTML reports; and `database.py` stores local history. `docs/` contains scope and assessment specifications, `sample_data/` contains fictional data, and `tests/` contains automated tests.

## Development setup

Startup IQ supports Python 3.11 or newer; Python 3.13 is currently exercised
in the development environment. Use a project-local virtual environment for
normal work, or create one outside the repository for a clean validation run.

### Windows PowerShell

```powershell
py --version
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
```

### Git Bash

```bash
python --version
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
```

`requirements.txt` contains application/runtime dependencies. `requirements-dev.txt`
includes those dependencies plus pytest and the pinned Playwright test runner.

Optional AI and authentication configuration can be created without secrets in
the repository by copying `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

For production, set a strong `AUTH_JWT_SECRET`; do not use the development
fallback. AI provider keys are optional because the local heuristic fallback is
available.

Run the application:

```powershell
python -m streamlit run app.py
```

Run tests:

```powershell
python -m pytest -q
```

Run browser verification:

```powershell
python run_browser_verification.py
```

To select Microsoft Edge instead of managed Chromium:

```powershell
$env:PLAYWRIGHT_BROWSER = "msedge"
python run_browser_verification.py
```

## Local persistence and history

When a valid assessment is submitted, Startup IQ initializes and writes to `data/startup_iq.db`. It stores an immutable startup profile snapshot, assessment inputs, score, rubric version, all seven dimension results, evidence statuses, and recommendations. Identical profile and assessment inputs are idempotent, so Streamlit reruns do not create duplicate records.

Use the **Assessment history** selector in the sidebar to open saved assessments and download historical reports. Selecting history does not overwrite the active assessment. History is local to this project and is not multi-user storage, cloud backup, or secure account isolation. The database contains user-generated startup information and must not be committed to Git.

After submitting a startup profile, use **Evidence Inbox / Registry** to register, search, filter, update, or archive evidence. Evidence associated with the startup is captured when an assessment is saved and is then available in its historical report.

Copy `.env.example` to `.env` only when optional future AI configuration is needed. No credentials are required for the current app.

## Backup and recovery

Startup IQ stores its SQLite database in `data/startup_iq.db`, uploaded attachments
in `data/attachments/`, and generated DOCX/PPTX files in `data/documents/`.
Backups contain sensitive founder, startup, evidence, and generated-document data;
store them encrypted and restrict access appropriately. Do not place a backup inside
the project's `data/` directory or commit one to Git.

Stop Streamlit before creating a backup so the database and file storage represent
the same offline maintenance point. From the repository root, use a destination
outside the project data directory:

Replace `BACKUP_ID` with the directory name printed by the backup command:

```powershell
python backup_recovery.py backup --destination C:\Startup-IQ-Backups --offline
python backup_recovery.py verify --backup C:\Startup-IQ-Backups\BACKUP_DIRECTORY
python backup_recovery.py restore --backup C:\Startup-IQ-Backups\BACKUP_DIRECTORY --target C:\Startup-IQ-Recovery\staging
```

The utility uses SQLite's backup API, records SHA-256 checksums and file sizes,
independently compares the expected attachment/document inventory with the copied
inventory before generating the manifest, validates SQLite integrity and foreign
keys, and atomically finalizes completed backup directories on the same filesystem
as the destination. This is not a live
application snapshot: `--offline` is an operator confirmation, not automatic
process detection. Symlinks, junctions, and Windows reparse points are rejected.
Restore inspects existing parent components before creating missing directories,
rejects symlinked or reparse-point parents and existing targets, stages into a
temporary directory, validates it, and never replaces active application data or
activates a recovered dataset.
An operator must inspect restored records and generated-document references before
deliberately activating any recovered dataset through a separate procedure.

The unsigned manifest's SHA-256 checksums detect accidental corruption when the
manifest is trusted; they do not authenticate a backup against an attacker who can
modify both files and the manifest. Use a separately protected signature or
checksum for authenticity. The CLI currently uses the configured default paths
from `database.py` and does not expose custom storage-path options. Legacy migration
backup files are not part of the active backup unless explicitly included by a
future implementation.

Finalization uses a same-filesystem rename and Windows no-replace behavior where
available. No universal race-free no-clobber guarantee is claimed for every
operating system or filesystem; an operator should treat the destination directory
as exclusively owned during recovery. The `--offline` flag is operator confirmation
that Streamlit has genuinely been stopped, not automatic process detection.

Historical evidence snapshots are stored in SQLite. Assessment HTML reports are
generated for download rather than persisted as a report directory; persisted
generated documents under `data/documents/` are included. The current design is
offline and single-instance: it does not provide an atomic live backup while
Streamlit is writing. A daily backup plus an additional backup before major data
changes is recommended, with periodic restore drills to an isolated staging area.

## User Authentication, Workspace Management & Multi-User Data Security (Phase 6)

Startup IQ features account management, workspace isolation, and object-level data security:

- **User Registration & Password Security**: Secure user registration requiring full name, valid email, and strong password enforcement (minimum 8 characters with uppercase, lowercase, and numeric requirements). Passwords are hashed using the state-of-the-art **Argon2id** algorithm (`time_cost=3`, `memory_cost=64MB`, `parallelism=4`). Plaintext passwords and hashes are never logged.
- **Cryptographic JWT Session Tokens**: Authentication uses cryptographically signed JSON Web Tokens (PyJWT, HMAC-SHA256) storing `user_id`, `email`, issue timestamp, expiration (24 hours), and a unique `jti`. The secret key is loaded from the environment variable `AUTH_JWT_SECRET` (falling back to a development key with production warnings).
- **Workspaces & Multi-Tenancy**: Users can create, switch between, and manage multiple workspaces. Each workspace encapsulates its own startups, assessments, evidence records, and founder documents.
- **Strict Object-Level Data Isolation**: Enforced in the database/service layer via parameterized queries and transactions, not just UI conditions:
  - Startup intake & profiles are scoped by `workspace_id` and `user_id`.
  - Seven-dimension assessments and scoring history cannot be viewed or queried by unauthorized users.
  - Evidence records, attachments, and updates reject unauthorized modifications.
  - Business plan (DOCX) and pitch deck (PPTX) generations and version histories are completely isolated.
  - ID manipulation attempts by malicious users are rejected at the data layer without leaking information.
- **Legacy Database Migration**: Existing local records are backed up automatically to timestamped files (`data/startup_iq_backup_*.db`) and safely bootstrapped under an isolated legacy administrator workspace (`legacy-admin@startup-iq.local`), ensuring newly registered users never inherit pre-existing data.

### Authentication Environment Configuration

In `.env`:
```bash
AUTH_JWT_SECRET=your-32-char-min-cryptographic-secret-here
```

### Registration & Login Instructions

1. Start the app: `python -m streamlit run app.py`
2. Navigate to the **📝 Create account** tab:
   - Enter Full Name, Email Address, and Password matching security rules.
   - Click **Create account**. A default personal workspace is automatically provisioned.
3. Switch to the **🔑 Log in** tab:
   - Enter registered Email Address and Password.
   - Click **Log in**.
4. Choose or create a workspace to enter the application.
5. In the sidebar, view the active workspace name, switch between workspaces if multiple exist, or click **🚪 Log out** to safely end the session.

## Limitations and next steps

- **Architecture Deviation**: The original conceptual blueprint mentioned React, FastAPI, and MySQL. Startup IQ intentionally implements a Python, Streamlit, and SQLite architecture for the MVP to prioritize rapid validation, determinism, and single-process simplicity.
- **SQLite Concurrency**: SQLite utilizes database-level locking for writes; for high concurrent multi-tenant loads, migrating to PostgreSQL/MySQL via an ORM/service layer is recommended.
- **Co-founder Collaboration**: Workspaces are currently single-owner; multi-user shared access, team invites, and granular RBAC are planned for future enterprise releases.
