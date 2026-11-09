"""Pure presentation helpers for the deterministic assessment result."""

from collections import Counter
from typing import Any

from scoring import AssessmentResult


DISCLAIMER = (
    "Startup IQ provides a structured assessment based on user-submitted information "
    "and the defined scoring rubric. The score is an analytical indicator, not a "
    "prediction of startup success, funding, profitability, or investment returns. "
    "Supporting evidence and assumptions should be independently reviewed."
)


def dimension_rows(result: AssessmentResult) -> list[dict[str, Any]]:
    """Convert the engine result to rows for tables and charts without recalculation."""
    return [
        {
            "dimension": item.name,
            "identifier": item.identifier,
            "rating": item.rating,
            "weight": item.weight,
            "contribution": item.contribution,
            "evidence_status": item.evidence_status,
            "rationale": item.rationale,
            "missing_evidence": item.missing_evidence,
        }
        for item in result.dimensions
    ]


def evidence_status_counts(result: AssessmentResult) -> dict[str, int]:
    """Count the statuses supplied with the assessment, without inventing coverage."""
    return dict(Counter(item.evidence_status for item in result.dimensions))
