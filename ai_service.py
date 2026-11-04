"""AI-powered startup idea understanding and structured extraction service.

Provides robust structured analysis of natural-language founder descriptions,
with support for OpenAI, Gemini, local heuristic/mock extraction, and clean error handling.
Does NOT determine scores or change rubric weights.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
import os
import re
import time
from typing import Any, Mapping
import urllib.error
import urllib.request

from config import AI_API_KEY

logger = logging.getLogger(__name__)
_last_provider_usage: dict[str, int] = {}

# Constants for extraction statuses
STATUS_PENDING = "Pending review"
STATUS_APPROVED = "Approved"
STATUS_EDITED = "Edited by founder"
STATUS_REJECTED = "Rejected"

EXTRACTION_SECTIONS = (
    "Startup identity",
    "Problem analysis",
    "Solution analysis",
    "Customer analysis",
    "Business model",
    "Market and competition",
    "Financial and operational information",
    "Risk analysis",
)

FIELD_DEFINITIONS = [
    # (field_key, display_label, section, target_profile_key)
    ("startup_name", "Startup name", "Startup identity", "startup_name"),
    ("industry", "Industry or sector", "Startup identity", "industry"),
    ("stage", "Startup stage", "Startup identity", "stage"),
    ("geography", "Primary geography", "Startup identity", "geography"),
    ("startup_description", "Short startup description", "Startup identity", "startup_description"),
    ("problem_statement", "Main problem", "Problem analysis", "problem_statement"),
    ("affected_people", "Who experiences the problem", "Problem analysis", "affected_people"),
    ("current_solutions", "Existing alternatives/solutions", "Problem analysis", "problem_evidence"),
    ("proposed_solution", "Proposed solution", "Solution analysis", "proposed_solution"),
    ("value_proposition", "Main value proposition", "Solution analysis", "value_proposition"),
    ("target_customers", "Target customer segment", "Customer analysis", "target_customers"),
    ("target_geography", "Target geography", "Customer analysis", "target_geography"),
    ("market_size", "Market size / claims", "Market and competition", "market_size"),
    ("direct_competitors", "Competitors mentioned", "Market and competition", "direct_competitors"),
    ("differentiators", "Differentiating factors", "Market and competition", "differentiators"),
    ("revenue_model", "Revenue model", "Business model", "revenue_model"),
    ("pricing", "Pricing assumptions", "Business model", "pricing"),
    ("acquisition", "Acquisition channels", "Business model", "acquisition"),
    ("estimated_costs", "Estimated costs", "Financial and operational information", "estimated_costs"),
    ("revenue_assumptions", "Revenue assumptions", "Financial and operational information", "revenue_assumptions"),
    ("cash_requirements", "Funding / cash requirements", "Financial and operational information", "cash_requirements"),
    ("traction", "Traction or progress", "Financial and operational information", "traction"),
    ("business_risks", "Business risks", "Risk analysis", "business_risks"),
    ("technical_operational_risks", "Technical & operational risks", "Risk analysis", "technical_operational_risks"),
    ("mitigations", "Proposed mitigations", "Risk analysis", "mitigations"),
]


@dataclass
class ExtractedField:
    field_key: str
    display_label: str
    section: str
    suggested_value: str
    supporting_text: str = ""
    is_inferred: bool = False
    confidence: float = 0.8
    confirmation_status: str = STATUS_PENDING
    target_profile_key: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MissingInfoItem:
    section: str
    dimension: str
    missing_information: str
    why_it_matters: str
    suggested_question: str
    suggested_evidence: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SuggestedEvidence:
    evidence_title: str
    evidence_description: str
    evidence_type: str
    related_dimension: str
    source_name: str
    verification_status: str = "Founder submitted"
    supporting_excerpt: str = ""
    is_approved: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisResult:
    fields: list[ExtractedField] = field(default_factory=list)
    missing_info: list[MissingInfoItem] = field(default_factory=list)
    suggested_evidence: list[SuggestedEvidence] = field(default_factory=list)
    raw_response: str = ""
    provider_used: str = "heuristic"
    usage_metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "fields": [f.as_dict() for f in self.fields],
            "missing_info": [m.as_dict() for m in self.missing_info],
            "suggested_evidence": [e.as_dict() for e in self.suggested_evidence],
            "provider_used": self.provider_used,
            "usage_metadata": self.usage_metadata,
        }


SYSTEM_PROMPT = """You are an expert startup analyst for Startup IQ.
Analyze the founder's natural-language startup idea and extract structured information.

IMPORTANT RULES:
1. Do NOT fabricate facts. If a detail is missing or not provided in the text, use "" or "Not provided".
2. Preserve exact supporting excerpts from the text where applicable.
3. Distinguish whether an extracted field is directly stated or inferred.
4. Identify critical missing information and what evidence would address it.
5. Identify any concrete validation evidence (surveys, interviews, pilots, letters of intent) mentioned by the founder. Do not mark evidence as independently verified.

Return ONLY a valid JSON object matching this schema:
{
  "fields": {
    "startup_name": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "industry": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "stage": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "geography": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "startup_description": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "problem_statement": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "affected_people": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "current_solutions": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "proposed_solution": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "value_proposition": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "target_customers": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "target_geography": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "market_size": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "direct_competitors": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "differentiators": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "revenue_model": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "pricing": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "acquisition": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "estimated_costs": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "revenue_assumptions": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "cash_requirements": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "traction": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "business_risks": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "technical_operational_risks": {"value": "...", "supporting_text": "...", "is_inferred": false},
    "mitigations": {"value": "...", "supporting_text": "...", "is_inferred": false}
  },
  "missing_info": [
    {
      "section": "Customer analysis",
      "dimension": "Market Potential",
      "missing_information": "Target customer segment is vague.",
      "why_it_matters": "Broad targeting dilutes acquisition focus.",
      "suggested_question": "Who is the exact initial persona willing to pay?",
      "suggested_evidence": "10 customer discovery interviews with specific buyer roles."
    }
  ],
  "suggested_evidence": [
    {
      "evidence_title": "30 Customer Interviews",
      "evidence_description": "Interviewed 30 target customers with 20 LOIs.",
      "evidence_type": "Customer interviews",
      "related_dimension": "Problem & Solution Clarity",
      "source_name": "Founder statement",
      "verification_status": "Founder submitted",
      "supporting_excerpt": "..."
    }
  ]
}
"""


def _clean_json_markdown(text: str) -> str:
    """Strip markdown code blocks if the LLM wrapped JSON in ```json."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
    return text.strip()


def _invoke_provider(callable_, *, provider: str, model: str, timeout: int) -> tuple[str, dict[str, Any]]:
    """Invoke a provider with a small retry budget for transient failures only."""
    started = time.perf_counter()
    attempts = 0
    while True:
        attempts += 1
        try:
            global _last_provider_usage
            _last_provider_usage = {}
            raw = callable_(timeout=timeout)
            metadata = {"provider": provider, "model": model, "success": True,
                        "attempts": attempts, "latency_ms": round((time.perf_counter() - started) * 1000, 1)}
            if _last_provider_usage:
                metadata["token_usage"] = dict(_last_provider_usage)
            logger.info("ai_request_completed", extra={"ai_event": metadata})
            return raw, metadata
        except urllib.error.HTTPError as error:
            transient = error.code == 429 or 500 <= error.code < 600
            if transient and attempts < 3:
                time.sleep(0.2 * attempts)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempts < 3:
                time.sleep(0.2 * attempts)
                continue
            raise


def _ground_analysis(result: AnalysisResult, source_text: str) -> AnalysisResult:
    """Remove supporting excerpts that are not present in the supplied source."""
    for field_item in result.fields:
        if field_item.supporting_text and field_item.supporting_text not in source_text:
            field_item.supporting_text = ""
            field_item.is_inferred = True
    for claim in result.suggested_evidence:
        if claim.supporting_excerpt and claim.supporting_excerpt not in source_text:
            claim.supporting_excerpt = ""
            claim.is_approved = False
    return result


def heuristic_extract_startup_idea(text: str) -> AnalysisResult:
    """High-quality heuristic natural-language parser when external AI API is absent or unavailable."""
    clean = text.strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if s.strip()]
    first_sentence = sentences[0] if sentences else ""

    # Name detection
    name = ""
    name_match = re.search(r"(?:building|called|named|startup(?:\s+is)?)\s+([A-Z][A-Za-z0-9\s&]+?)(?=[,\.\n]|\s+is|\s+that|\s+which|\s+an|\s+a)", clean)
    if name_match:
        name = name_match.group(1).strip()
    elif sentences and len(sentences[0].split()) <= 4 and not sentences[0].lower().startswith("we "):
        name = sentences[0].rstrip(".!?")

    # Industry detection
    industry = "Technology"
    ind_keywords = {
        "health": "HealthTech", "medic": "HealthTech", "clinic": "HealthTech", "hospital": "HealthTech", "patient": "HealthTech",
        "finan": "FinTech", "bank": "FinTech", "payment": "FinTech", "crypto": "FinTech", "invest": "FinTech",
        "learn": "EdTech", "school": "EdTech", "student": "EdTech", "teach": "EdTech", "educat": "EdTech",
        "farm": "AgriTech", "crop": "AgriTech", "retail": "E-Commerce", "shop": "E-Commerce", "store": "E-Commerce",
        "clean": "CleanTech", "solar": "CleanTech", "energy": "CleanTech", "carbon": "CleanTech",
        "ai": "Artificial Intelligence", "machine learning": "Artificial Intelligence", "saas": "B2B SaaS", "software": "Software",
    }
    lower_text = clean.lower()
    for kw, ind in ind_keywords.items():
        if kw in lower_text:
            industry = ind
            break

    # Stage detection
    stage = "Idea"
    if any(k in lower_text for k in ["seed", "pre-seed", "raised", "round"]):
        stage = "Pre-seed"
    elif any(k in lower_text for k in ["paying customer", "revenue", "mrr", "arr", "sales"]):
        stage = "Early validation"
    elif any(k in lower_text for k in ["prototype", "pilot", "mvp", "beta", "testing"]):
        stage = "Early validation"

    # Problem statement
    problem = ""
    prob_match = re.search(r"(?:problem(?: is)?|pain point|struggle with|difficult to|lack of|inefficient|suffer from)\s*[:\s]*([^\n\.]+[.!?]?)", clean, re.I)
    if prob_match:
        problem = prob_match.group(0).strip()
    else:
        # Fallback to second sentence or first sentence if indicative
        problem = sentences[1] if len(sentences) > 1 else first_sentence

    # Solution
    solution = ""
    sol_match = re.search(r"(?:solution(?: is)?|we provide|we offer|we built|we develop|platform that|app that|system that|tool that)\s*[:\s]*([^\n\.]+[.!?]?)", clean, re.I)
    if sol_match:
        solution = sol_match.group(0).strip()
    else:
        solution = first_sentence

    # Target customers
    customers = ""
    cust_match = re.search(r"(?:target(?:ed)? (?:at|to|customers?)|for\s+(?:small businesses|enterprises|hospitals|clinics|students|freelancers|developers|consumers|users|companies))\s*[:\s]*([^\n\.]+[.!?]?)", clean, re.I)
    if cust_match:
        customers = cust_match.group(0).strip()
    else:
        # Infer from keywords
        found_audiences = [w for w in ["hospitals", "clinics", "developers", "freelancers", "students", "smbs", "enterprises", "manufacturers"] if w in lower_text]
        if found_audiences:
            customers = f"Target audience includes {', '.join(found_audiences)}."

    # Competitors
    competitors = ""
    comp_match = re.search(r"(?:competitors?|competing with|alternatives? like|vs\.?)\s*[:\s]*([^\n\.]+[.!?]?)", clean, re.I)
    if comp_match:
        competitors = comp_match.group(0).strip()

    # Revenue model
    revenue = ""
    rev_match = re.search(r"(?:revenue|monetiz|subscription|charge|pricing|fee|freemium|\$\d+)\s*[:\s]*([^\n\.]+[.!?]?)", clean, re.I)
    if rev_match:
        revenue = rev_match.group(0).strip()

    # Cash requirements extraction (conservative; only when explicitly stated as capital/funding needed)
    cash_req = ""
    curr_amount_pattern = r"(?:[\$€£₹¥]|(?:USD|EUR|GBP|INR|CAD|AUD)\s*)\s*\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:million|billion|lakh|crore|mn|k|m|b))\b|\d+(?:,\d{3})*(?:\.\d+)?\s*(?:million|billion|lakh|crore|mn|k|m|b)?\s*(?:[\$€£₹¥]|USD|EUR|GBP|INR|CAD|AUD)\b|(?:[\$€£₹¥]|(?:USD|EUR|GBP|INR|CAD|AUD)\s*)\s*\d+(?:,\d{3})*(?:\.\d+)?\b"
    cash_patterns = [
        # 1. 'We need/require/seeking/raising $500,000 for clinical trials...'
        r"(?i)\b(?:we\s+)?(?:need|needs|require|requires|seeking|raising|look(?:ing)?\s+for|target(?:ing)?)\s+(?:to\s+raise\s+)?(?:around|approx(?:imately)?|about)?\s*(" + curr_amount_pattern + r")(?:[^\.\n;]*?(?:for|in|as|towards|to\s+fund)\s+[^\.\n;]+)?",
        # 2. '$500,000 required in seed funding' / 'INR 25 lakh needed for...'
        r"(?i)(" + curr_amount_pattern + r")\s+(?:is\s+)?(?:required|needed|sought|to\s+be\s+raised)(?:\s+(?:in|for|as|of)\s+[^\.\n;]+)?",
        # 3. 'Capital requirement: $500,000' / 'Funding required: INR 25 lakh' / 'Cash requirements: ...'
        r"(?i)\b(?:capital|funding|cash|investment|runway)\s+(?:requirements?|needed|required|ask|target|goal)\s*[:=-]\s*(" + curr_amount_pattern + r"(?:[^\.\n;]+)?)",
    ]
    for s in sentences:
        s_lower = s.lower()
        if "valuation" in s_lower or "valued at" in s_lower:
            if not any(k in s_lower for k in ["need", "require", "seeking", "raising", "funding needed", "capital required"]):
                continue
        if any(p in s_lower for p in ["raised last", "already raised", "previously raised", "have raised"]) and not any(k in s_lower for k in ["seeking", "raising now", "require", "need"]):
            continue
        if any(p in s_lower for p in ["/month", "per month", "subscription", "charge", "pricing", "annual revenue", "revenue is"]) and not any(k in s_lower for k in ["funding", "capital", "trials", "runway", "raise", "seeking", "require", "need"]):
            continue
        for pat in cash_patterns:
            m = re.search(pat, s)
            if m:
                matched = m.group(0).strip()
                matched_lower = matched.lower()
                if "valuation" in matched_lower or "subscription" in matched_lower:
                    continue
                cash_req = matched
                break
        if cash_req:
            break

    # Build field mapping
    values_map = {
        "startup_name": (name, name != "Unnamed Venture"),
        "industry": (industry, True),
        "stage": (stage, True),
        "geography": ("", False),
        "startup_description": (first_sentence or clean[:120], False),
        "problem_statement": (problem or "Problem description requires elaboration.", bool(problem)),
        "affected_people": (customers or "", bool(customers)),
        "current_solutions": ("", False),
        "proposed_solution": (solution or first_sentence, bool(solution)),
        "value_proposition": (solution[:100] if solution else "", True),
        "target_customers": (customers or "Target customers not yet explicitly specified.", bool(customers)),
        "target_geography": ("", False),
        "market_size": ("", False),
        "direct_competitors": (competitors, bool(competitors)),
        "differentiators": ("", False),
        "revenue_model": (revenue, bool(revenue)),
        "pricing": ("", False),
        "acquisition": ("", False),
        "estimated_costs": ("", False),
        "revenue_assumptions": ("", False),
        "cash_requirements": (cash_req, bool(cash_req)),
        "traction": ("", False),
        "business_risks": ("", False),
        "technical_operational_risks": ("", False),
        "mitigations": ("", False),
    }

    fields_list: list[ExtractedField] = []
    for f_key, label, sec, target_pk in FIELD_DEFINITIONS:
        val, has_val = values_map.get(f_key, ("", False))
        fields_list.append(ExtractedField(
            field_key=f_key,
            display_label=label,
            section=sec,
            suggested_value=val,
            supporting_text=val if has_val else "",
            is_inferred=not has_val,
            confidence=0.85 if has_val else 0.4,
            confirmation_status=STATUS_PENDING if val else STATUS_REJECTED,
            target_profile_key=target_pk,
        ))

    # Detect Missing Information
    missing_items: list[MissingInfoItem] = []
    if not customers:
        missing_items.append(MissingInfoItem(
            section="Customer analysis",
            dimension="Market Potential",
            missing_information="Target customer persona is not explicitly defined.",
            why_it_matters="Without a defined ICP (Ideal Customer Profile), customer acquisition costs and market size cannot be calculated.",
            suggested_question="Who is the specific individual or department that holds the budget?",
            suggested_evidence="Customer discovery interviews with target buyers.",
        ))
    if not competitors:
        missing_items.append(MissingInfoItem(
            section="Market and competition",
            dimension="Competitive Position",
            missing_information="No direct competitors or incumbent workarounds are identified.",
            why_it_matters="Investors look for clear differentiation over existing manual or automated solutions.",
            suggested_question="How do users solve this problem today without your product?",
            suggested_evidence="Competitor comparison matrix or pricing teardown.",
        ))
    if not revenue:
        missing_items.append(MissingInfoItem(
            section="Business model",
            dimension="Business Model",
            missing_information="Revenue model and pricing structure are not described.",
            why_it_matters="Unit economics and viability depend on clear pricing assumptions.",
            suggested_question="Will you charge a recurring subscription, transactional fee, or license?",
            suggested_evidence="Pricing validation interviews or competitor pricing benchmarks.",
        ))
    if not any(k in lower_text for k in ["cost", "expense", "budget", "burn"]):
        missing_items.append(MissingInfoItem(
            section="Financial and operational information",
            dimension="Financial Readiness",
            missing_information="Cost and cash requirements are absent.",
            why_it_matters="Runway and capital efficiency cannot be assessed without cost estimates.",
            suggested_question="What are your estimated server, personnel, and customer acquisition costs?",
            suggested_evidence="Preliminary cost model or budget estimate.",
        ))

    # Check for evidence claims mentioned in text
    suggested_ev: list[SuggestedEvidence] = []
    ev_matches = re.finditer(r"(?:interview(?:ed)?\s+(\d+)|survey(?:ed)?\s+(\d+)|pilot(?:ed)?\s+with\s+(\d+)|(\d+)\s+customers?|(\d+)\s+loi|letters? of intent)", clean, re.I)
    for m in ev_matches:
        matched_str = m.group(0)
        suggested_ev.append(SuggestedEvidence(
            evidence_title=f"Validation claim: {matched_str.title()}",
            evidence_description=f"Founder stated: '{matched_str}' in description.",
            evidence_type="Customer interviews" if "interview" in matched_str.lower() else "Pilot results",
            related_dimension="Problem & Solution Clarity" if "interview" in matched_str.lower() else "Market Potential",
            source_name="Founder statement",
            verification_status="Founder submitted",
            supporting_excerpt=matched_str,
            is_approved=False,
        ))

    return AnalysisResult(
        fields=fields_list,
        missing_info=missing_items,
        suggested_evidence=suggested_ev,
        raw_response="",
        provider_used="heuristic",
    )


def _call_openai_api(api_key: str, text: str, model: str = "gpt-4o-mini", timeout: int = 25) -> str:
    """Call OpenAI Chat Completions API with JSON mode."""
    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Startup idea description:\n\n{text}"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        res_data = json.loads(response.read().decode("utf-8"))
        global _last_provider_usage
        usage = res_data.get("usage", {})
        _last_provider_usage = {key: int(usage[key]) for key in ("prompt_tokens", "completion_tokens", "total_tokens") if isinstance(usage.get(key), int)}
        return res_data["choices"][0]["message"]["content"]


def _call_gemini_api(api_key: str, text: str, model: str = "gemini-3.8-flash", timeout: int = 25) -> str:
    """Call Google Gemini Generative Language API with JSON response format."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": f"{SYSTEM_PROMPT}\n\nStartup idea description:\n\n{text}"}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
        },
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        res_data = json.loads(response.read().decode("utf-8"))
        global _last_provider_usage
        usage = res_data.get("usageMetadata", {})
        _last_provider_usage = {key: int(usage[key]) for key in ("promptTokenCount", "candidatesTokenCount", "totalTokenCount") if isinstance(usage.get(key), int)}
        return res_data["candidates"][0]["content"]["parts"][0]["text"]


def parse_ai_json_response(json_str: str, provider_name: str = "llm") -> AnalysisResult:
    """Parse and validate JSON response from an LLM into AnalysisResult."""
    clean_json = _clean_json_markdown(json_str)
    parsed = json.loads(clean_json)
    if not isinstance(parsed, dict):
        raise ValueError("AI response is not a valid JSON dictionary.")

    raw_fields = parsed.get("fields", {})
    if not isinstance(raw_fields, dict):
        raise ValueError("AI fields must be a JSON object.")
    if set(parsed) - {"fields", "missing_info", "suggested_evidence"}:
        raise ValueError("AI response contains unexpected top-level fields.")
    fields_list: list[ExtractedField] = []
    for f_key, label, sec, target_pk in FIELD_DEFINITIONS:
        f_info = raw_fields.get(f_key, {})
        if isinstance(f_info, dict):
            val = str(f_info.get("value", "")).strip()
            supp = str(f_info.get("supporting_text", "")).strip()
            inferred = bool(f_info.get("is_inferred", False))
            conf = float(f_info.get("confidence", 0.8 if val else 0.3))
            if not 0 <= conf <= 1:
                raise ValueError("AI confidence is outside the permitted range.")
        else:
            val = str(f_info).strip()
            supp = ""
            inferred = False
            conf = 0.8 if val else 0.3

        fields_list.append(ExtractedField(
            field_key=f_key,
            display_label=label,
            section=sec,
            suggested_value=val,
            supporting_text=supp,
            is_inferred=inferred,
            confidence=conf,
            confirmation_status=STATUS_PENDING if val else STATUS_REJECTED,
            target_profile_key=target_pk,
        ))

    # Parse missing info
    missing_items: list[MissingInfoItem] = []
    for item in parsed.get("missing_info", []):
        if isinstance(item, dict) and item.get("missing_information"):
            missing_items.append(MissingInfoItem(
                section=str(item.get("section", "General")),
                dimension=str(item.get("dimension", "General")),
                missing_information=str(item.get("missing_information", "")),
                why_it_matters=str(item.get("why_it_matters", "")),
                suggested_question=str(item.get("suggested_question", "")),
                suggested_evidence=str(item.get("suggested_evidence", "")),
            ))

    # Parse suggested evidence
    suggested_ev: list[SuggestedEvidence] = []
    for ev in parsed.get("suggested_evidence", []):
        if isinstance(ev, dict) and ev.get("evidence_title"):
            suggested_ev.append(SuggestedEvidence(
                evidence_title=str(ev.get("evidence_title", "")),
                evidence_description=str(ev.get("evidence_description", "")),
                evidence_type=str(ev.get("evidence_type", "Customer interviews")),
                related_dimension=str(ev.get("related_dimension", "Market Potential")),
                source_name=str(ev.get("source_name", "Founder statement")),
                verification_status="Founder submitted",
                supporting_excerpt=str(ev.get("supporting_excerpt", "")),
                is_approved=False,
            ))

    return AnalysisResult(
        fields=fields_list,
        missing_info=missing_items,
        suggested_evidence=suggested_ev,
        raw_response=json_str,
        provider_used=provider_name,
    )


def analyze_startup_idea(
    text: str,
    *,
    api_key: str | None = None,
    provider: str | None = None,
    timeout: int = 25,
) -> tuple[AnalysisResult, str | None]:
    """Analyze natural-language startup text.

    Returns (AnalysisResult, error_message).
    If an external LLM fails or is not configured, gracefully falls back to heuristic extraction.
    """
    clean_text = text.strip()
    if not clean_text:
        return AnalysisResult(), "Startup description cannot be empty."

    if len(clean_text) > 25000:
        return AnalysisResult(), "Startup description exceeds maximum character limit (25,000)."

    active_key = api_key or AI_API_KEY or os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
    active_provider = provider or os.getenv("AI_PROVIDER", "auto").lower()

    if not active_key:
        logger.info("No AI_API_KEY set; using robust local heuristic extraction.")
        res = heuristic_extract_startup_idea(clean_text)
        return res, None

    # Call configured provider
    try:
        if active_provider == "gemini" or (active_provider == "auto" and active_key.startswith("AIza")):
            raw_json, metadata = _invoke_provider(lambda **kw: _call_gemini_api(active_key, clean_text, **kw), provider="gemini", model="gemini-3.8-flash", timeout=timeout)
            result = _ground_analysis(parse_ai_json_response(raw_json, provider_name="gemini"), clean_text)
            result.usage_metadata = metadata
            return result, None
        else:
            raw_json, metadata = _invoke_provider(lambda **kw: _call_openai_api(active_key, clean_text, **kw), provider="openai", model="gpt-4o-mini", timeout=timeout)
            result = _ground_analysis(parse_ai_json_response(raw_json, provider_name="openai"), clean_text)
            result.usage_metadata = metadata
            return result, None
    except urllib.error.HTTPError as error:
        err_msg = f"AI Provider HTTP {error.code}: {error.reason}"
        logger.warning(f"{err_msg}; falling back to heuristic engine.")
        fallback = heuristic_extract_startup_idea(clean_text)
        return fallback, f"{err_msg} (used local heuristic fallback)"
    except urllib.error.URLError as error:
        err_msg = f"AI Provider network error: {error.reason}"
        logger.warning(f"{err_msg}; falling back to heuristic engine.")
        fallback = heuristic_extract_startup_idea(clean_text)
        return fallback, f"{err_msg} (used local heuristic fallback)"
    except TimeoutError:
        err_msg = "AI Provider request timed out."
        logger.warning(f"{err_msg}; falling back to heuristic engine.")
        fallback = heuristic_extract_startup_idea(clean_text)
        return fallback, f"{err_msg} (used local heuristic fallback)"
    except Exception as error:
        err_msg = f"AI parsing or model error: {type(error).__name__}"
        logger.warning(f"{err_msg}; falling back to heuristic engine.")
        fallback = heuristic_extract_startup_idea(clean_text)
        return fallback, f"{err_msg} (used local heuristic fallback)"



# ---------------------------------------------------------------------------
# Document Text Analysis & Evidence Claim Extraction
# ---------------------------------------------------------------------------

@dataclass
class DocumentEvidenceClaim:
    """A candidate evidence claim extracted from an uploaded document."""
    claim_title: str
    claim_description: str
    evidence_type: str
    related_dimension: str
    supporting_excerpt: str
    source_document: str
    is_directly_stated: bool = True
    confidence: str = "High"  # High, Medium, Low
    recommended_verification: str = "Verify source document and founder notes"
    approval_status: str = STATUS_PENDING  # Pending review, Approved, Rejected

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentAnalysisResult:
    """Result of analyzing an extracted document text for startup evidence."""
    source_filename: str
    claims: list[DocumentEvidenceClaim] = field(default_factory=list)
    missing_evidence_gaps: list[MissingInfoItem] = field(default_factory=list)
    document_summary: str = ""
    provider_used: str = "heuristic"
    error: str | None = None
    usage_metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_filename": self.source_filename,
            "claims": [c.as_dict() for c in self.claims],
            "missing_evidence_gaps": [g.as_dict() for g in self.missing_evidence_gaps],
            "document_summary": self.document_summary,
            "provider_used": self.provider_used,
            "error": self.error,
            "usage_metadata": self.usage_metadata,
        }


def analyze_extracted_document(
    document_text: str,
    source_filename: str,
    *,
    startup_profile: Mapping[str, Any] | None = None,
    api_key: str | None = None,
    provider: str | None = None,
    timeout: int = 25,
) -> DocumentAnalysisResult:
    """Analyze extracted document text and return structured candidate evidence claims.

    If AI credentials are not configured, uses deterministic heuristic evidence extraction.
    """
    clean_text = document_text.strip()
    if not clean_text:
        return DocumentAnalysisResult(
            source_filename=source_filename,
            error="Document contains no text to analyze.",
        )

    active_key = api_key or AI_API_KEY or os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
    active_provider = provider or os.getenv("AI_PROVIDER", "auto").lower()

    if not active_key:
        logger.info("No AI key available for document analysis; running local heuristic analyzer.")
        return heuristic_analyze_document(clean_text, source_filename, startup_profile=startup_profile)

    # Prompt for LLM document analysis
    prompt = f"""You are Startup IQ's Document Validation Analyst.
Analyze the following extracted text from document '{source_filename}'.
Extract candidate startup validation evidence claims.

IMPORTANT INSTRUCTIONS:
1. Extract real claims mentioned in the text (interviews, surveys, pilot tests, LOIs, financials, market data, benchmarks).
2. DO NOT fabricate claims. Every claim must have an exact supporting excerpt from the text.
3. Classify evidence_type into one of: 'Customer interviews', 'Customer survey results', 'Market research', 'Competitor research', 'Financial assumptions', 'Pricing validation', 'Letters of intent', 'Pilot results', 'Product benchmarks', 'Legal or regulatory research', 'Technical documentation', 'Other'.
4. Map to one of the 7 dimensions: 'Market Potential', 'Problem & Solution Clarity', 'Competitive Position', 'Business Model', 'Financial Readiness', 'Risk Management', 'Pitch Readiness'.
5. Identify evidence gaps (missing methodology, unverified numbers, missing baselines).

Respond ONLY with valid JSON in this exact structure:
{{
  "document_summary": "1-2 sentence objective summary of the document",
  "claims": [
    {{
      "claim_title": "Short title",
      "claim_description": "Detailed description of claim",
      "evidence_type": "Customer interviews",
      "related_dimension": "Problem & Solution Clarity",
      "supporting_excerpt": "Exact quotation from document",
      "is_directly_stated": true,
      "confidence": "High",
      "recommended_verification": "Review sample interview recording or transcript"
    }}
  ],
  "missing_evidence_gaps": [
    {{
      "section": "Validation Gap",
      "dimension": "Market Potential",
      "missing_information": "Sample size for survey was not disclosed",
      "why_it_matters": "Small sample sizes reduce statistical significance",
      "suggested_question": "How many participants completed the survey?",
      "suggested_evidence": "Survey participant demographic and count table"
    }}
  ]
}}

Document Text:
{clean_text[:18000]}
"""

    try:
        if active_provider == "gemini" or (active_provider == "auto" and active_key.startswith("AIza")):
            raw_json, metadata = _invoke_provider(lambda **kw: _call_gemini_api(active_key, prompt, **kw), provider="gemini", model="gemini-3.8-flash", timeout=timeout)
        else:
            raw_json, metadata = _invoke_provider(lambda **kw: _call_openai_api(active_key, prompt, **kw), provider="openai", model="gpt-4o-mini", timeout=timeout)

        result = parse_document_analysis_json(raw_json, source_filename, provider_name=active_provider)
        result.usage_metadata = metadata
        for claim in result.claims:
            if claim.supporting_excerpt and claim.supporting_excerpt not in clean_text:
                claim.supporting_excerpt = ""
                claim.is_directly_stated = False
        return result
    except Exception as exc:
        logger.warning(f"AI Document analysis API failed: {exc}; falling back to heuristic analyzer.")
        fallback_res = heuristic_analyze_document(clean_text, source_filename, startup_profile=startup_profile)
        fallback_res.error = f"AI Provider unavailable ({type(exc).__name__}). Performed local analysis."
        return fallback_res


def parse_document_analysis_json(raw_json: str, source_filename: str, provider_name: str) -> DocumentAnalysisResult:
    """Parse JSON returned by LLM into DocumentAnalysisResult."""
    try:
        # Strip markdown code fence if present
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_json.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        data = json.loads(cleaned)
    except Exception:
        # Fallback to heuristic if JSON is broken
        return heuristic_analyze_document(raw_json, source_filename)

    if not isinstance(data, dict) or not isinstance(data.get("claims", []), list) or not isinstance(data.get("missing_evidence_gaps", []), list):
        raise ValueError("AI document response has an invalid schema.")
    claims: list[DocumentEvidenceClaim] = []
    for c in data.get("claims", []):
        if c.get("claim_title") and c.get("claim_description"):
            claims.append(
                DocumentEvidenceClaim(
                    claim_title=c.get("claim_title", "Evidence Claim"),
                    claim_description=c.get("claim_description", ""),
                    evidence_type=c.get("evidence_type", "Other"),
                    related_dimension=c.get("related_dimension", "Market Potential"),
                    supporting_excerpt=c.get("supporting_excerpt", ""),
                    source_document=source_filename,
                    is_directly_stated=c.get("is_directly_stated", True),
                    confidence=c.get("confidence", "High"),
                    recommended_verification=c.get("recommended_verification", "Verify founder documentation"),
                )
            )

    gaps: list[MissingInfoItem] = []
    for g in data.get("missing_evidence_gaps", []):
        if g.get("missing_information"):
            gaps.append(
                MissingInfoItem(
                    section=g.get("section", "Validation Gap"),
                    dimension=g.get("dimension", "Market Potential"),
                    missing_information=g.get("missing_information", ""),
                    why_it_matters=g.get("why_it_matters", "Gaps affect investor credibility"),
                    suggested_question=g.get("suggested_question", "Provide supporting evidence"),
                    suggested_evidence=g.get("suggested_evidence", "Supporting document"),
                )
            )

    return DocumentAnalysisResult(
        source_filename=source_filename,
        claims=claims,
        missing_evidence_gaps=gaps,
        document_summary=data.get("document_summary", ""),
        provider_used=provider_name,
    )


def heuristic_analyze_document(
    text: str,
    source_filename: str,
    *,
    startup_profile: Mapping[str, Any] | None = None,
) -> DocumentAnalysisResult:
    """Deterministic, zero-dependency pattern matching to extract candidate evidence claims."""
    claims: list[DocumentEvidenceClaim] = []
    gaps: list[MissingInfoItem] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 1. Customer interview findings
    interview_pattern = re.compile(r"(interview|customer feedback|spoke with|founders interviewed|participants stated)", re.IGNORECASE)
    for line in lines:
        if interview_pattern.search(line) and len(line) > 20:
            claims.append(
                DocumentEvidenceClaim(
                    claim_title="Customer Interview Findings",
                    claim_description=f"Evidence of customer problem/feedback: {line[:120]}...",
                    evidence_type="Customer interviews",
                    related_dimension="Problem & Solution Clarity",
                    supporting_excerpt=line[:250],
                    source_document=source_filename,
                    is_directly_stated=True,
                    confidence="High",
                    recommended_verification="Review interview transcripts and participant list",
                )
            )
            break

    # 2. Survey findings
    survey_pattern = re.compile(r"(\b\d{1,3}%\b|survey of \d+|respondents|survey results)", re.IGNORECASE)
    for line in lines:
        if survey_pattern.search(line) and len(line) > 20:
            claims.append(
                DocumentEvidenceClaim(
                    claim_title="Survey Validation Metric",
                    claim_description=f"Survey metric identified: {line[:120]}...",
                    evidence_type="Customer survey results",
                    related_dimension="Market Potential",
                    supporting_excerpt=line[:250],
                    source_document=source_filename,
                    is_directly_stated=True,
                    confidence="High",
                    recommended_verification="Verify survey distribution methodology and raw responses",
                )
            )
            break

    # 3. Pilot / LOI validation
    pilot_pattern = re.compile(r"(pilot program|letters? of intent|signed LOI|trial customer|beta test)", re.IGNORECASE)
    for line in lines:
        if pilot_pattern.search(line) and len(line) > 20:
            claims.append(
                DocumentEvidenceClaim(
                    claim_title="Pilot / LOI Traction Record",
                    claim_description=f"Commercial validation agreement: {line[:120]}...",
                    evidence_type="Pilot results" if "pilot" in line.lower() else "Letters of intent",
                    related_dimension="Pitch Readiness",
                    supporting_excerpt=line[:250],
                    source_document=source_filename,
                    is_directly_stated=True,
                    confidence="High",
                    recommended_verification="Inspect signed counter-party agreement or pilot KPI tracker",
                )
            )
            break

    # 4. Financial & pricing validation
    financial_pattern = re.compile(r"(\$[\d,]+|\bpricing\b|\bgross margin\b|\bCAC\b|\bLTV\b|\bunit economics\b)", re.IGNORECASE)
    for line in lines:
        if financial_pattern.search(line) and len(line) > 15:
            claims.append(
                DocumentEvidenceClaim(
                    claim_title="Financial & Pricing Assumption",
                    claim_description=f"Financial metric stated in document: {line[:120]}...",
                    evidence_type="Financial assumptions",
                    related_dimension="Financial Readiness",
                    supporting_excerpt=line[:250],
                    source_document=source_filename,
                    is_directly_stated=True,
                    confidence="Medium",
                    recommended_verification="Reconcile with financial spreadsheet model",
                )
            )
            break

    # 5. Competitor research
    comp_pattern = re.compile(r"(competitor|alternative|vs\.|market advantage|moat|differentiat)", re.IGNORECASE)
    for line in lines:
        if comp_pattern.search(line) and len(line) > 20:
            claims.append(
                DocumentEvidenceClaim(
                    claim_title="Competitive Advantage Benchmark",
                    claim_description=f"Comparative assessment: {line[:120]}...",
                    evidence_type="Competitor research",
                    related_dimension="Competitive Position",
                    supporting_excerpt=line[:250],
                    source_document=source_filename,
                    is_directly_stated=True,
                    confidence="Medium",
                    recommended_verification="Confirm feature comparisons against competitor websites",
                )
            )
            break

    # Gap detection
    if not any(c.evidence_type == "Customer interviews" for c in claims):
        gaps.append(
            MissingInfoItem(
                section="Validation Gap",
                dimension="Problem & Solution Clarity",
                missing_information="No direct customer interview quotes or qualitative evidence found in document",
                why_it_matters="Direct customer feedback proves genuine problem urgency",
                suggested_question="Have you conducted structured discovery interviews with at least 15 target users?",
                suggested_evidence="Customer interview log with problem quotes",
            )
        )
    if not any(c.evidence_type == "Financial assumptions" for c in claims):
        gaps.append(
            MissingInfoItem(
                section="Validation Gap",
                dimension="Financial Readiness",
                missing_information="No unit economics or cost structure figures detected",
                why_it_matters="Investors require validated customer acquisition costs and margin assumptions",
                suggested_question="What are your estimated direct costs per user/unit delivered?",
                suggested_evidence="Unit economics breakdown spreadsheet",
            )
        )

    summary = f"Extracted {len(claims)} candidate evidence claims and {len(gaps)} potential validation gaps from '{source_filename}' using local pattern analysis."

    return DocumentAnalysisResult(
        source_filename=source_filename,
        claims=claims,
        missing_evidence_gaps=gaps,
        document_summary=summary,
        provider_used="heuristic",
    )
