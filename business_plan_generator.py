"""Professional business plan generator exporting editable DOCX documents.

Follows the 22 required sections, integrates founder profile data, assessment
snapshots, evidence records, and recommendations without fabricating facts.
"""

from __future__ import annotations

import io
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import docx
from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

BUSINESS_PLAN_SECTIONS = [
    ("1. Executive Summary", "executive_summary"),
    ("2. Startup Overview", "startup_overview"),
    ("3. Problem Statement", "problem_statement"),
    ("4. Proposed Solution", "proposed_solution"),
    ("5. Target Market", "target_market"),
    ("6. Customer Segments", "customer_segments"),
    ("7. Market Opportunity", "market_opportunity"),
    ("8. Competitor Analysis", "competitor_analysis"),
    ("9. Product and Value Proposition", "product_value_prop"),
    ("10. Business Model", "business_model"),
    ("11. Revenue Model", "revenue_model"),
    ("12. Marketing and Sales Strategy", "marketing_sales"),
    ("13. Operations and Technology", "operations_technology"),
    ("14. Traction and Validation", "traction_validation"),
    ("15. Financial Assumptions", "financial_assumptions"),
    ("16. Funding Requirements", "funding_requirements"),
    ("17. Risk Analysis", "risk_analysis"),
    ("18. Growth Strategy", "growth_strategy"),
    ("19. Implementation Roadmap", "implementation_roadmap"),
    ("20. Supporting Evidence and Assumptions", "evidence_assumptions"),
    ("21. Assessment Summary", "assessment_summary"),
    ("22. Conclusion & Next Steps", "conclusion"),
]


def _val(val: Any, missing_placeholder: str) -> str:
    """Return text if provided, otherwise a clear founder action required placeholder."""
    s = str(val or "").strip()
    return s if s else f"[Founder input required: {missing_placeholder}]"


def draft_business_plan_content(
    profile: Mapping[str, Any],
    assessment: Mapping[str, Any] | None = None,
    evidence: Sequence[Mapping[str, Any]] = (),
    recommendations: Sequence[Any] = (),
) -> dict[str, str]:
    """Draft structured text for all 22 business plan sections without fabricating data."""
    name = str(profile.get("startup_name") or "Unnamed Venture").strip()
    industry = str(profile.get("industry") or "Technology").strip()
    stage = str(profile.get("stage") or "Idea").strip()
    desc = str(profile.get("startup_description") or "").strip()
    prob = str(profile.get("problem_statement") or "").strip()
    sol = str(profile.get("proposed_solution") or "").strip()
    cust = str(profile.get("target_customers") or "").strip()
    rev_model = str(profile.get("revenue_model") or "").strip()
    pricing = str(profile.get("pricing") or "").strip()
    comp = str(profile.get("direct_competitors") or "").strip()
    diff = str(profile.get("differentiators") or "").strip()
    costs = str(profile.get("estimated_costs") or "").strip()
    rev_assump = str(profile.get("revenue_assumptions") or "").strip()
    cash = str(profile.get("cash_requirements") or "").strip()
    traction = str(profile.get("traction") or "").strip()
    risks = str(profile.get("business_risks") or "").strip()
    mitig = str(profile.get("mitigations") or "").strip()
    tech_risks = str(profile.get("technical_operational_risks") or "").strip()

    overall_score = ""
    rubric_ver = ""
    dim_text_lines = []
    if assessment and "result" in assessment:
        res = assessment["result"]
        overall_score = f"{res.overall_score:.2f} / 100"
        rubric_ver = res.rubric_version
        for d in res.dimensions:
            dim_text_lines.append(f"• {d.name} ({d.weight}%): Rating {d.rating}/100 — Status: {d.evidence_status}. Rationale: {d.rationale or 'None provided.'}")

    ev_lines = []
    for ev in evidence:
        ev_lines.append(f"• {ev.get('evidence_title')} ({ev.get('evidence_type')} - {ev.get('related_dimension')}): {ev.get('evidence_description')} [Verification: {ev.get('verification_status', 'Founder submitted')}]")

    rec_lines = []
    for r in recommendations:
        if hasattr(r, "issue"):
            rec_lines.append(f"• [{r.priority} Priority - {r.dimension}] {r.issue}: {r.recommended_action} (Evidence: {r.suggested_evidence})")
        elif isinstance(r, dict):
            rec_lines.append(f"• [{r.get('priority')} Priority - {r.get('dimension')}] {r.get('issue')}: {r.get('recommended_action')}")

    sections: dict[str, str] = {
        "executive_summary": (
            f"{name} is an early-stage startup operating in the {industry} sector currently at the {stage} stage. "
            f"The company addresses a critical market need: {_val(prob, 'Articulate the primary problem clearly')}. "
            f"To solve this, {name} provides {_val(sol, 'Describe the core product solution')}. "
            f"Targeting {_val(cust, 'Specify target customer segments')}, the venture monetizes through {_val(rev_model, 'Define revenue stream model')} "
            f"with pricing structured as {_val(pricing, 'Detail initial pricing assumptions')}.\n\n"
            f"This business plan compiles current founder inputs, validated evidence records, and analytical readiness flags. "
            f"All unvalidated estimates are explicitly flagged for founder follow-up."
        ),
        "startup_overview": (
            f"Company Name: {name}\n"
            f"Industry / Sector: {industry}\n"
            f"Development Stage: {stage}\n"
            f"Primary Geography: {_val(profile.get('geography'), 'Specify operational region or primary launch geography')}\n\n"
            f"Mission & Purpose:\n{_val(desc, 'Provide an expanded 2-3 sentence company overview and purpose')}"
        ),
        "problem_statement": (
            f"Problem Description:\n{_val(prob, 'Detail the exact pain point, frequency, and severity')}\n\n"
            f"Who Experiences the Problem:\n{_val(profile.get('affected_people'), 'Identify specific personas, departments, or demographics affected')}\n\n"
            f"Evidence of Problem Existence:\n{_val(profile.get('problem_evidence'), 'Document user interviews, survey data, or observational evidence confirming the problem')}"
        ),
        "proposed_solution": (
            f"Solution Overview:\n{_val(sol, 'Detail the technical and workflow design of the solution')}\n\n"
            f"Value Proposition:\n{_val(profile.get('value_proposition'), 'Explain quantifiable customer ROI, time savings, or convenience gains')}"
        ),
        "target_market": (
            f"Target Customer Definition:\n{_val(cust, 'Identify the initial beachhead Ideal Customer Profile (ICP)')}\n\n"
            f"Target Geography:\n{_val(profile.get('target_geography'), 'State target regional scope, expansion phases, and regulatory boundaries')}"
        ),
        "customer_segments": (
            f"Primary Persona:\n{_val(cust, 'Detail decision-maker title, buyer characteristics, and pain triggers')}\n\n"
            f"Customer Research Completed:\n{_val(profile.get('customer_research'), 'Document number of customer discovery interviews and recorded feedback')}"
        ),
        "market_opportunity": (
            f"Market Size Assumptions:\n{_val(profile.get('market_size'), 'Provide TAM, SAM, and SOM bottom-up market sizing estimates')}\n\n"
            f"Demand Validation:\n{_val(profile.get('demand_evidence'), 'Present search traffic, waitlists, survey results, or LOIs demonstrating active demand')}"
        ),
        "competitor_analysis": (
            f"Direct Competitors:\n{_val(comp, 'List 2-4 primary direct competing products or companies')}\n\n"
            f"Indirect Competitors & Workarounds:\n{_val(profile.get('indirect_competitors'), 'Describe spreadsheets, legacy manual methods, or partial alternatives')}\n\n"
            f"Differentiating Factors:\n{_val(diff, 'Articulate defensive moats, IP, speed, cost advantages, or proprietary data')}"
        ),
        "product_value_prop": (
            f"Core Capabilities & Features:\n{_val(sol, 'Detail product architecture and key feature capabilities')}\n\n"
            f"Unique Selling Proposition (USP):\n{_val(profile.get('value_proposition'), 'State the singular compelling differentiator for buyers')}"
        ),
        "business_model": (
            f"Monetization Framework:\n{_val(rev_model, 'Outline transaction fees, subscriptions, or licensing mechanics')}\n\n"
            f"Delivery & Operations Model:\n{_val(profile.get('delivery'), 'Describe cloud delivery, manufacturing, or distribution logistics')}"
        ),
        "revenue_model": (
            f"Pricing Tiers & Structure:\n{_val(pricing, 'Detail pricing tiers, seat costs, or unit pricing schedules')}\n\n"
            f"Revenue Projections & Assumptions:\n{_val(rev_assump, 'Provide unit sales assumptions, contract values, and renewal rate estimates')}"
        ),
        "marketing_sales": (
            f"Customer Acquisition Channels:\n{_val(profile.get('acquisition'), 'Identify inbound channels, direct outbound sales, content, or partner distribution')}\n\n"
            f"Go-To-Market Plan:\n[Founder input required: Define initial 6-month customer acquisition milestones and conversion funnels.]"
        ),
        "operations_technology": (
            f"Technical Infrastructure:\n{_val(profile.get('delivery'), 'Detail hosting, software stack, integrations, or operational logistics')}\n\n"
            f"Operational Capabilities:\n{_val(profile.get('capabilities'), 'Summarize engineering, product, and leadership capabilities available')}"
        ),
        "traction_validation": (
            f"Current Progress & Milestones:\n{_val(profile.get('progress'), 'Document current product status (prototype, MVP, beta, live)')}\n\n"
            f"Traction & Commercial Signals:\n{_val(traction, 'State pilot participants, waitlist volume, or verified revenue (do not fabricate)')}\n\n"
            f"Trials & Feedback:\n{_val(profile.get('interviews_trials'), 'Summarize trial outcomes and qualitative feedback')}"
        ),
        "financial_assumptions": (
            f"Estimated Operating Costs:\n{_val(costs, 'Break down hosting, personnel, acquisition, and administrative costs')}\n\n"
            f"Revenue Assumptions:\n{_val(rev_assump, 'Detail customer volume ramp and monthly churn estimates')}\n\n"
            f"Unit Economics:\n{_val(profile.get('unit_economics'), 'Provide Estimated CAC, LTV, Gross Margins, and Payback Period')}"
        ),
        "funding_requirements": (
            f"Capital Required:\n{_val(cash, 'State round target size and planned runway (e.g. $500,000 for 18 months)')}\n\n"
            f"Use of Funds Breakdown:\n[Founder input required: Allocate percentage budget to R&D, Sales/Marketing, G&A, and Regulatory compliance.]"
        ),
        "risk_analysis": (
            f"Business & Market Risks:\n{_val(risks, 'Identify market adoption, competitive response, and pricing sensitivity risks')}\n\n"
            f"Technical & Operational Risks:\n{_val(tech_risks, 'Identify scalability, cybersecurity, dependency, and execution bottlenecks')}\n\n"
            f"Mitigation Strategies:\n{_val(mitig, 'Detail concrete risk mitigation protocols and backup contingencies')}"
        ),
        "growth_strategy": (
            f"Expansion Strategy:\n[Founder input required: Detail expansion from initial beachhead to adjacent verticals and geographic regions.]\n\n"
            f"Partnerships & Ecosystem:\n[Founder input required: Specify channel distribution partners and integration alliances.]"
        ),
        "implementation_roadmap": (
            f"Near-Term Milestones (Months 1-6):\n• Finalize MVP feature set and complete founder validation trials.\n• Establish initial customer feedback loops and refine onboarding.\n\n"
            f"Mid-Term Milestones (Months 7-12):\n• Ramp scalable customer acquisition channels and pilot conversions.\n• Secure key compliance, IP, or security certifications.\n\n"
            f"Long-Term Milestones (Months 13-24):\n• Expand market presence and prepare for institutional scaling round."
        ),
        "evidence_assumptions": (
            "Registered Supporting Evidence:\n" + ("\n".join(ev_lines) if ev_lines else "• No registered evidence records currently associated with this assessment snapshot.") + "\n\n"
            "Key Unvalidated Hypotheses:\n" + (_val(profile.get("unknown_information"), "Explicitly document remaining unknowns and unvalidated hypotheses."))
        ),
        "assessment_summary": (
            f"Analytical Readiness Score: {overall_score or 'Assessment not yet scored'}\n"
            f"Rubric Version: {rubric_ver or 'N/A'}\n\n"
            f"Dimension Breakdown:\n" + ("\n".join(dim_text_lines) if dim_text_lines else "• Dimension assessment data not available for this snapshot.") + "\n\n"
            f"Rule-Based Recommendations:\n" + ("\n".join(rec_lines) if rec_lines else "• No active high-priority recommendations recorded.")
        ),
        "conclusion": (
            f"{name} has defined a compelling thesis in the {industry} market. "
            f"To achieve institutional investor-readiness, leadership must prioritize validating the flagged missing information items, "
            f"converting hypotheses into verified evidence records, and establishing repeatable unit economics."
        ),
    }

    return sections


def create_business_plan_docx(
    profile: Mapping[str, Any],
    sections: Mapping[str, str],
    output_path: str | Path | None = None,
) -> bytes:
    """Generate a clean, professional, styled DOCX document."""
    doc = Document()

    # Document-wide margins (1 inch)
    for section in doc.sections:
        section.top_margin = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    # Base styling
    normal_style = doc.styles["Normal"]
    normal_style.font.name = "Calibri"
    normal_style.font.size = Pt(11)
    normal_style.font.color.rgb = RGBColor(0x2A, 0x33, 0x3F)

    # 1. Cover Page
    startup_name = str(profile.get("startup_name") or "Startup IQ Project").strip()
    industry = str(profile.get("industry") or "Technology").strip()

    p_pre = doc.add_paragraph()
    p_pre.paragraph_format.space_before = Pt(80)

    p_title = doc.add_paragraph()
    run_title = p_title.add_run(startup_name)
    run_title.font.name = "Arial"
    run_title.font.size = Pt(32)
    run_title.font.bold = True
    run_title.font.color.rgb = RGBColor(0x15, 0x55, 0x6D)  # Primary Navy Blue
    p_title.paragraph_format.space_after = Pt(10)

    p_sub = doc.add_paragraph()
    run_sub = p_sub.add_run(f"Comprehensive Business Plan & Operational Strategy\nIndustry: {industry}")
    run_sub.font.size = Pt(14)
    run_sub.font.color.rgb = RGBColor(0x55, 0x6B, 0x7D)
    p_sub.paragraph_format.space_after = Pt(160)

    p_meta = doc.add_paragraph()
    run_meta = p_meta.add_run(
        "Document Version: 1.0 (Draft for Founder Review)\n"
        "Generated via: Startup IQ Investor Readiness Platform\n"
        "CONFIDENTIAL & PROPRIETARY"
    )
    run_meta.font.size = Pt(9.5)
    run_meta.font.italic = True
    run_meta.font.color.rgb = RGBColor(0x7F, 0x8C, 0x8D)

    doc.add_page_break()

    # 2. Disclaimer Section
    p_disc_h = doc.add_heading("Important Notice & Disclaimer", level=2)
    p_disc_h.style.font.color.rgb = RGBColor(0xC0, 0x39, 0x2B)
    p_disc = doc.add_paragraph()
    r_disc = p_disc.add_run(
        "This business plan is an AI-assisted working draft based on submitted founder intake information "
        "and analytical rubrics. It does not constitute investment advice, a formal securities prospectus, "
        "or a guarantee of financial return. Market assumptions, customer quotes, and revenue estimates "
        "require independent verification prior to presenting to accredited investors or financial institutions."
    )
    r_disc.font.size = Pt(10)
    r_disc.font.italic = True

    # 3. Render All 22 Sections
    for title, key in BUSINESS_PLAN_SECTIONS:
        doc.add_paragraph().paragraph_format.space_before = Pt(12)
        h = doc.add_heading(title, level=1)
        h.style.font.name = "Arial"
        h.style.font.size = Pt(16)
        h.style.font.bold = True
        h.style.font.color.rgb = RGBColor(0x15, 0x55, 0x6D)
        h.paragraph_format.space_after = Pt(6)

        content = sections.get(key, "[Section content pending founder completion.]")
        for line in content.split("\n\n"):
            line_str = line.strip()
            if not line_str:
                continue
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 1.15
            # Highlight missing input placeholders
            if "[Founder input required:" in line_str:
                parts = re.split(r"(\[Founder input required:[^\]]+\])", line_str)
                for part in parts:
                    if part.startswith("[Founder input required:"):
                        r_warn = p.add_run(part)
                        r_warn.font.color.rgb = RGBColor(0xD3, 0x54, 0x00)  # Amber
                        r_warn.font.bold = True
                    else:
                        p.add_run(part)
            else:
                p.add_run(line_str)

    buffer = io.BytesIO()
    doc.save(buffer)
    docx_bytes = buffer.getvalue()

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(docx_bytes)

    return docx_bytes

