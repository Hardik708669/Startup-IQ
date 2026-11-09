"""Deterministic, rule-based recommendations for assessment results."""

from dataclasses import asdict, dataclass

from scoring import AssessmentResult, DimensionScore


LOW_RATING_THRESHOLD = 60
STRONG_RATING_THRESHOLD = 80


@dataclass(frozen=True)
class Recommendation:
    """One actionable recommendation derived from one dimension result."""

    dimension_id: str
    dimension: str
    priority: str
    issue: str
    explanation: str
    recommended_action: str
    suggested_evidence: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        """Return a report-friendly representation."""
        return asdict(self)


_GUIDANCE = {
    "market_potential": (
        "Define the target customer and reachable market more precisely, test urgency with target customers, and document market-size assumptions.",
        "Customer interviews, a documented market-sizing method, and evidence of demand.",
    ),
    "problem_solution_clarity": (
        "Refine the problem statement and solution promise around a specific affected user, then test whether the proposed solution addresses the stated need.",
        "Problem interviews, solution tests, and dated notes linking user feedback to the problem and solution.",
    ),
    "competitive_position": (
        "Map direct competitors, indirect alternatives, and switching considerations, then test whether the proposed differentiation matters to customers.",
        "A competitor/alternative comparison supported by customer feedback or observed usage decisions.",
    ),
    "business_model": (
        "Document the value proposition, pricing, revenue streams, acquisition channels, and delivery model as testable business-model assumptions.",
        "Pricing conversations, willingness-to-pay tests, acquisition experiments, and delivery-cost observations.",
    ),
    "financial_readiness": (
        "Build a consistent assumptions sheet covering pricing, revenue, costs, cash needs, and unit economics where relevant.",
        "A traceable cost estimate, pricing test, cash plan, and unit-economics calculation based on stated assumptions.",
    ),
    "risk_management": (
        "List the material market, operational, technical, legal, security, and execution risks and assign a concrete mitigation or test to each.",
        "A risk register with owners, mitigation steps, and results from relevant technical, operational, or legal checks.",
    ),
    "pitch_readiness": (
        "Clarify the problem-solution narrative and connect evidence, traction, financial assumptions, and limitations into a complete presentation.",
        "A reviewed pitch outline with cited supporting evidence and explicit labels for assumptions and unknowns.",
    ),
}


def _recommendation_for_dimension(item: DimensionScore) -> Recommendation | None:
    """Build at most one non-contradictory recommendation for a dimension."""
    action, evidence = _GUIDANCE[item.identifier]
    weak_evidence = item.missing_evidence
    low_rating = item.rating < LOW_RATING_THRESHOLD
    moderate_rating = LOW_RATING_THRESHOLD <= item.rating < STRONG_RATING_THRESHOLD

    if not low_rating and not moderate_rating and not weak_evidence:
        return None

    if low_rating and weak_evidence:
        priority = "High"
        issue = "Low rating and insufficient supporting evidence"
        explanation = f"{item.name} is rated {item.rating}/100, and the submitted evidence status is '{item.evidence_status}'."
        reason = "The dimension needs both substantive improvement and evidence that can support a future reassessment."
    elif low_rating:
        priority = "High"
        issue = "Low dimension rating"
        explanation = f"{item.name} is rated {item.rating}/100, indicating a material gap under the rubric."
        reason = "A low rating should be addressed before relying on this dimension in planning or communication."
    elif weak_evidence:
        priority = "Medium" if item.rating < STRONG_RATING_THRESHOLD else "High"
        issue = "Supporting evidence is limited"
        explanation = f"{item.name} is rated {item.rating}/100, but the evidence status is '{item.evidence_status}'."
        reason = "A strong or developing rating remains uncertain when its supporting basis is not yet established."
    else:
        priority = "Medium"
        issue = "Dimension can be strengthened"
        explanation = f"{item.name} is rated {item.rating}/100 and has supporting information, but refinement remains useful."
        reason = "A developing dimension benefits from targeted validation and clearer documentation."

    return Recommendation(item.identifier, item.name, priority, issue, explanation, action, evidence, reason)


def generate_recommendations(result: AssessmentResult) -> tuple[Recommendation, ...]:
    """Return reproducible recommendations from the supplied assessment result."""
    return tuple(
        recommendation
        for item in result.dimensions
        if (recommendation := _recommendation_for_dimension(item)) is not None
    )
