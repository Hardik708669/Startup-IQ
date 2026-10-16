# Startup IQ

Startup IQ is an analytical indicator for evaluating startup ideas—not a magic prediction, funding guarantee, or substitute for professional investment advice. The Day 1 foundation supports a planned MVP for structured intake, evidence-aware assessment, explainable scoring, recommendations, and reports.

## Current stage

The foundation scaffold is complete; the full assessment workflow is not yet implemented.

## Rubric

Market Potential 20%, Problem & Solution Clarity 15%, Competitive Position 15%, Business Model 15%, Financial Readiness 15%, Risk Management 10%, Pitch Readiness 10%. Ratings will be 0–100 and weighted deterministically. AI may explain or summarize, but never determines the final score.

## Stack

Python 3.13.5, Streamlit, Plotly, SQLite (`sqlite3`), `python-dotenv`, and pytest.

## Structure

`app.py` is the UI entry point; `scoring.py` owns the canonical rubric; the other root modules are focused future boundaries. `docs/` contains scope, assessment specification, and plan. `sample_data/` contains fictional demonstration data. `tests/` contains rubric checks.

## Windows PowerShell setup

```powershell
python --version
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the scaffold:

```powershell
python -m streamlit run app.py
```

Run tests:

```powershell
python -m pytest -q
```

Copy `.env.example` to `.env` only when optional future AI configuration is needed. No credentials are required for the current app.

## Limitations and next steps

Intake, scoring calculations, dashboard, recommendations, AI adapter, reports, and persistence are planned for 17–25 October 2026 according to `docs/DEVELOPMENT_PLAN.md`; they are not falsely represented as complete here. Live data, authentication, collaboration, complex forecasting, and cloud infrastructure are outside this MVP.
