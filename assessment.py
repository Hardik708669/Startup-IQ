"""Pure helpers for collecting, but not scoring, dimension assessments."""

from __future__ import annotations

from typing import Any, MutableMapping

from scoring import DIMENSIONS


DIMENSION_QUESTIONS = {
    "market_potential": "How clearly are the target customers, reachable market, urgency, and supporting market evidence described?",
    "problem_solution_clarity": "How specific is the problem, and how convincingly does the proposed solution address it?",
    "competitive_position": "How well are alternatives understood, and how well is the proposed differentiation supported?",
    "business_model": "How clearly are the customer, value proposition, pricing, revenue, acquisition, and delivery model defined?",
    "financial_readiness": "How coherent and supported are the revenue, cost, pricing, cash, and unit-economics assumptions?",
    "risk_management": "How thoroughly are market, operational, technical, legal, security, and execution risks addressed?",
    "pitch_readiness": "How complete and clear are the startup narrative, evidence, traction, and financial story?",
}

# Put the conservative state first so an untouched control cannot imply support.
EVIDENCE_STATUSES = ("Not yet known", "Supported information", "Estimate", "Unvalidated hypothesis")
ASSESSMENT_STATE_PREFIXES = ("rating_", "reviewed_", "status_", "rationale_")


def reset_assessment_state(state: MutableMapping[str, Any]) -> None:
    """Clear assessment inputs so a new startup cannot inherit prior answers."""
    state["assessment_entries"] = None
    state["assessment_result"] = None
    state["recommendations"] = None
    state["saved_assessment_id"] = None
    state["assessment_date"] = None
    state["dashboard_view"] = False
    for key in list(state):
        if key.startswith(ASSESSMENT_STATE_PREFIXES):
            del state[key]


def get_dimension_question(identifier: str) -> str:
    """Return the prompt for a canonical dimension."""
    return DIMENSION_QUESTIONS[identifier]


def validate_assessment_entries(entries: dict[str, dict[str, Any]]) -> list[str]:
    """Validate collected ratings and rationale without calculating a score."""
    errors: list[str] = []
    expected = {dimension.identifier for dimension in DIMENSIONS}
    if set(entries) != expected:
        errors.append("Assessment entries must exist for all seven rubric dimensions.")
    for dimension in DIMENSIONS:
        entry = entries.get(dimension.identifier, {})
        rating = entry.get("rating")
        if not isinstance(rating, int) or not 0 <= rating <= 100:
            errors.append(f"{dimension.name} rating must be between 0 and 100.")
        if entry.get("reviewed") is not True:
            errors.append(f"{dimension.name} rating must be explicitly reviewed and confirmed.")
        if not str(entry.get("rationale", "")).strip():
            errors.append(f"{dimension.name} requires supporting rationale or evidence.")
    return errors
