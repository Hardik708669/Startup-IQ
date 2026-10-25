# Startup IQ

Startup IQ is an analytical indicator for evaluating startup ideas—not a magic prediction, funding guarantee, or substitute for professional investment advice.

## Current stage

The local MVP supports startup intake, seven-dimension assessment, deterministic scoring, evidence-gap flags, rule-based recommendations, downloadable HTML reports, and local SQLite assessment history.

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

Copy `.env.example` to `.env` only when optional future AI configuration is needed. No credentials are required for the current app.

## Limitations and next steps

AI assistance, live data, authentication, collaboration, complex forecasting, cloud infrastructure, and PDF/DOCX export remain outside the current MVP. The next improvements should focus on integration coverage, UI polish, and deployment preparation.
