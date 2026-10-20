"""Canonical rubric configuration and deterministic scoring engine."""

from dataclasses import dataclass
from typing import Any, Mapping

RUBRIC_VERSION = "v1.0"


@dataclass(frozen=True)
class Dimension:
    """One weighted assessment dimension."""

    identifier: str
    name: str
    weight: int


@dataclass(frozen=True)
class DimensionScore:
    """Deterministic result for one rubric dimension."""

    identifier: str
    name: str
    rating: int
    weight: int
    contribution: float
    rationale: str
    evidence_status: str
    missing_evidence: bool


@dataclass(frozen=True)
class AssessmentResult:
    """Complete deterministic assessment result, without AI-generated text."""

    overall_score: float
    rubric_version: str
    dimensions: tuple[DimensionScore, ...]

    def as_dict(self) -> dict[str, Any]:
        """Return a serialization-friendly representation for future UI/report use."""
        return {
            "overall_score": self.overall_score,
            "rubric_version": self.rubric_version,
            "dimensions": [dimension.__dict__.copy() for dimension in self.dimensions],
        }


DIMENSIONS = (
    Dimension("market_potential", "Market Potential", 20),
    Dimension("problem_solution_clarity", "Problem & Solution Clarity", 15),
    Dimension("competitive_position", "Competitive Position", 15),
    Dimension("business_model", "Business Model", 15),
    Dimension("financial_readiness", "Financial Readiness", 15),
    Dimension("risk_management", "Risk Management", 10),
    Dimension("pitch_readiness", "Pitch Readiness", 10),
)


def validate_rubric() -> None:
    """Raise ``ValueError`` when the canonical rubric is internally invalid."""
    if len(DIMENSIONS) != 7:
        raise ValueError("The rubric must contain exactly seven dimensions.")
    identifiers = [dimension.identifier for dimension in DIMENSIONS]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Dimension identifiers must be unique.")
    if any(dimension.weight <= 0 for dimension in DIMENSIONS):
        raise ValueError("All dimension weights must be positive.")
    if sum(dimension.weight for dimension in DIMENSIONS) != 100:
        raise ValueError("Dimension weights must total 100%.")


validate_rubric()


def calculate_assessment(entries: Mapping[str, Mapping[str, Any]]) -> AssessmentResult:
    """Calculate the weighted score from validated, explicit assessment inputs.

    The function uses only the canonical rubric and submitted numeric ratings.
    Evidence status and rationale are carried into the result and used only to
    flag evidence gaps; they never alter the numeric score.
    """
    validate_rubric()
    expected = {dimension.identifier for dimension in DIMENSIONS}
    if set(entries) != expected:
        missing = sorted(expected - set(entries))
        extra = sorted(set(entries) - expected)
        details = []
        if missing:
            details.append(f"missing dimensions: {', '.join(missing)}")
        if extra:
            details.append(f"unknown dimensions: {', '.join(extra)}")
        raise ValueError("Assessment inputs must match the canonical rubric (" + "; ".join(details) + ").")

    results: list[DimensionScore] = []
    for dimension in DIMENSIONS:
        entry = entries[dimension.identifier]
        rating = entry.get("rating")
        if isinstance(rating, bool) or not isinstance(rating, (int, float)) or not 0 <= rating <= 100:
            raise ValueError(f"{dimension.name} rating must be a number between 0 and 100.")
        if entry.get("reviewed") is not True:
            raise ValueError(f"{dimension.name} rating must be explicitly reviewed and confirmed.")
        rationale = str(entry.get("rationale", "")).strip()
        evidence_status = str(entry.get("evidence_status", "Not yet known")).strip() or "Not yet known"
        missing_evidence = evidence_status != "Supported information" or not rationale
        contribution = round(float(rating) * dimension.weight / 100, 2)
        results.append(DimensionScore(
            identifier=dimension.identifier,
            name=dimension.name,
            rating=int(rating) if float(rating).is_integer() else rating,
            weight=dimension.weight,
            contribution=contribution,
            rationale=rationale,
            evidence_status=evidence_status,
            missing_evidence=missing_evidence,
        ))
    return AssessmentResult(
        overall_score=round(sum(result.contribution for result in results), 2),
        rubric_version=RUBRIC_VERSION,
        dimensions=tuple(results),
    )
