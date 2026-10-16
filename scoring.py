"""Canonical Startup IQ rubric configuration.

The numerical scoring engine will be added after the intake interface. Keeping
the rubric here provides one source of truth for all future modules.
"""

from dataclasses import dataclass

RUBRIC_VERSION = "v1.0"


@dataclass(frozen=True)
class Dimension:
    """One weighted assessment dimension."""

    identifier: str
    name: str
    weight: int


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
