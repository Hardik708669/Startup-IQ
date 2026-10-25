"""Standalone HTML assessment report generation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from html import escape
import re

from dashboard import DISCLAIMER, evidence_status_counts
from recommendations import Recommendation
from scoring import AssessmentResult


def _text(value: object) -> str:
    """Safely render a value as escaped HTML text."""
    if value is None or value == "":
        return "Not provided"
    if isinstance(value, (list, tuple)):
        return "<br>".join(escape(str(item)) for item in value) or "Not provided"
    if isinstance(value, Mapping):
        return escape("; ".join(f"{key}: {value}" for key, value in value.items()))
    return escape(str(value))


def report_filename(startup_name: str, assessment_date: str) -> str:
    """Return a safe, predictable filename for a generated HTML report."""
    base = re.sub(r"[^A-Za-z0-9]+", "-", startup_name.strip()).strip("-").lower() or "startup"
    date_part = re.sub(r"[^0-9-]", "", assessment_date) or "assessment"
    return f"startup-iq-{base}-{date_part}.html"


def _recommendation_html(recommendations: Iterable[Recommendation]) -> str:
    items = list(recommendations)
    if not items:
        return "<p>No rule-based recommendations were generated for this assessment.</p>"
    return "".join(
        "<article class='recommendation'>"
        f"<h3>{escape(item.priority)} priority — {escape(item.dimension)}</h3>"
        f"<p><strong>Issue:</strong> {_text(item.issue)}</p>"
        f"<p><strong>Explanation:</strong> {_text(item.explanation)}</p>"
        f"<p><strong>Recommended action:</strong> {_text(item.recommended_action)}</p>"
        f"<p><strong>Suggested evidence or validation:</strong> {_text(item.suggested_evidence)}</p>"
        f"<p><strong>Reason:</strong> {_text(item.reason)}</p>"
        "</article>"
        for item in items
    )


def generate_report(
    profile: Mapping[str, object],
    result: AssessmentResult | None,
    recommendations: Iterable[Recommendation] = (),
    assessment_date: str | None = None,
) -> str:
    """Generate a standalone HTML report from the current assessment result.

    The result's overall score and dimension contributions are displayed as
    supplied by the deterministic scoring engine; this function does not
    recalculate them.
    """
    if result is None:
        raise ValueError("A valid assessment result is required to generate a report.")
    report_date = assessment_date or "Not provided"
    startup_name = str(profile.get("startup_name", "")).strip() or "Not provided"
    overview_fields = (
        ("Startup name", "startup_name"),
        ("Industry", "industry"),
        ("Stage", "stage"),
        ("Startup description", "startup_description"),
        ("Problem statement", "problem_statement"),
        ("Proposed solution", "proposed_solution"),
        ("Target customers", "target_customers"),
        ("Business model", "revenue_model"),
    )
    overview_html = "".join(
        f"<dt>{escape(label)}</dt><dd>{_text(profile.get(key))}</dd>" for label, key in overview_fields
    )
    rows_html = "".join(
        "<tr>"
        f"<td>{escape(item.name)}</td><td>{item.rating}/100</td><td>{item.weight}%</td>"
        f"<td>{item.contribution:.2f}</td><td>{_text(item.rationale)}</td>"
        f"<td>{_text(item.evidence_status)}</td>"
        f"<td>{'Yes' if item.missing_evidence else 'No'}</td>"
        "</tr>"
        for item in result.dimensions
    )
    statuses = evidence_status_counts(result)
    status_html = "".join(
        f"<li><strong>{escape(status)}:</strong> {count} dimension(s)</li>"
        for status, count in statuses.items()
    ) or "<li>No dimension evidence statuses available.</li>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Startup IQ Assessment — {escape(startup_name)}</title>
<style>
body {{ font-family: Arial, sans-serif; color: #203040; line-height: 1.5; margin: 0; background: #f4f7f9; }}
main {{ max-width: 1100px; margin: 0 auto; padding: 32px 22px; }}
header, section {{ background: #fff; padding: 24px; margin-bottom: 20px; border-radius: 10px; box-shadow: 0 1px 5px #ccd5dc; }}
h1 {{ margin: 0 0 6px; color: #15556d; }} h2 {{ color: #15556d; border-bottom: 2px solid #d9e8ed; padding-bottom: 8px; }}
.meta {{ color: #52636d; }} .score {{ font-size: 36px; font-weight: bold; color: #15556d; }}
dl {{ display: grid; grid-template-columns: 220px 1fr; gap: 8px 18px; }} dt {{ font-weight: bold; }} dd {{ margin: 0; }}
.table-wrap {{ overflow-x: auto; }} table {{ border-collapse: collapse; width: 100%; min-width: 900px; }}
th, td {{ border: 1px solid #d5dfe3; padding: 10px; text-align: left; vertical-align: top; }} th {{ background: #e8f1f4; }}
.recommendation {{ border-left: 5px solid #e08a2e; padding: 8px 16px; margin: 14px 0; background: #fff8ed; }}
.disclaimer {{ background: #eef4f6; border-left: 5px solid #15556d; padding: 14px; }}
@media print {{ body {{ background: #fff; }} main {{ max-width: none; padding: 0; }} header, section {{ box-shadow: none; }} }}
</style></head>
<body><main>
<header><h1>Startup IQ</h1><p class="meta">Assessment Report</p>
<p><strong>Startup:</strong> {_text(startup_name)}<br><strong>Assessment date:</strong> {_text(report_date)}<br><strong>Rubric version:</strong> {_text(result.rubric_version)}</p></header>
<section><h2>Startup Overview</h2><dl>{overview_html}</dl></section>
<section><h2>Overall Assessment</h2><p class="score">{result.overall_score:.2f} / 100</p>
<p>This score is a deterministic analytical indicator based on the submitted information and the defined rubric. It is not a prediction of startup success.</p>
<p><strong>Assessment dimensions:</strong> {len(result.dimensions)}</p></section>
<section><h2>Dimension Breakdown</h2><div class="table-wrap"><table><thead><tr><th>Dimension</th><th>Rating</th><th>Weight</th><th>Contribution</th><th>Rationale</th><th>Evidence status</th><th>Missing evidence</th></tr></thead><tbody>{rows_html}</tbody></table></div></section>
<section><h2>Evidence Analysis</h2><p>Statuses below reflect user-submitted classifications and are not independently verified.</p><ul>{status_html}</ul><p>Missing evidence is distinct from a low numerical rating. Estimates, hypotheses, and information not yet known should be validated before being treated as established support.</p></section>
<section><h2>Recommendations</h2>{_recommendation_html(recommendations)}</section>
<section><h2>Assessment Limitations</h2><p class="disclaimer">{escape(DISCLAIMER)}</p><p><strong>Rubric version:</strong> {escape(result.rubric_version)}<br><strong>Assessment date:</strong> {_text(report_date)}</p></section>
</main></body></html>"""
