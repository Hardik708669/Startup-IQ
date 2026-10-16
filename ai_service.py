"""Optional AI-service boundary; no external calls are made on Day 1."""


def summarize_startup(*args, **kwargs):
    """Reserve startup-description summarization for optional AI integration."""
    raise NotImplementedError


def explain_assessment(*args, **kwargs):
    """Reserve natural-language assessment explanations for optional AI integration."""
    raise NotImplementedError


def draft_business_plan_outline(*args, **kwargs):
    """Reserve business-plan outline drafting for optional AI integration."""
    raise NotImplementedError
