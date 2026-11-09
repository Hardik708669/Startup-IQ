"""Professional editable pitch deck generator exporting styled PPTX presentations.

Creates clean 12-slide investor pitch decks adhering to design best practices:
consistent typography, visual hierarchy, no fabricated charts, clear placeholders
for missing facts, and evidence/readiness integration.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Mapping, Sequence

import pptx
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

PITCH_DECK_SLIDES = [
    ("Slide 1: Startup Title & Intro", "title"),
    ("Slide 2: Problem Statement", "problem"),
    ("Slide 3: Proposed Solution", "solution"),
    ("Slide 4: Product & Core Features", "product"),
    ("Slide 5: Target Customers & Beachhead", "customers"),
    ("Slide 6: Market Opportunity", "market"),
    ("Slide 7: Competitive Landscape", "competition"),
    ("Slide 8: Business & Monetization Model", "business_model"),
    ("Slide 9: Traction & Validation Evidence", "traction"),
    ("Slide 10: Financial & Cost Overview", "financials"),
    ("Slide 11: Growth & Execution Roadmap", "roadmap"),
    ("Slide 12: Team & Funding Ask", "funding"),
]


def _val(val: Any, missing_placeholder: str) -> str:
    s = str(val or "").strip()
    return s if s else f"[Founder input required: {missing_placeholder}]"


def draft_pitch_deck_slides(
    profile: Mapping[str, Any],
    assessment: Mapping[str, Any] | None = None,
    evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, dict[str, Any]]:
    """Draft structured data for each slide without fabricating data."""
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
    cash = str(profile.get("cash_requirements") or "").strip()
    traction = str(profile.get("traction") or "").strip()

    overall_score = ""
    if assessment and "result" in assessment:
        overall_score = f"{assessment['result'].overall_score:.2f} / 100"

    ev_bullets = []
    for ev in evidence[:4]:
        ev_bullets.append(f"• {ev.get('evidence_title')} ({ev.get('evidence_type')}): {ev.get('evidence_description')} [{ev.get('verification_status')}]")
    if not ev_bullets:
        ev_bullets = ["• No registered evidence records currently associated with this assessment snapshot."]

    return {
        "title": {
            "title": name,
            "subtitle": f"{industry} • {stage} Stage\n{desc or 'Investor Presentation'}",
            "bullets": [f"Readiness Indicator: {overall_score or 'Scoring Pending'}", "CONFIDENTIAL & PROPRIETARY"],
        },
        "problem": {
            "title": "The Problem",
            "subtitle": "Critical pain point experienced by customers today",
            "bullets": [
                f"Core Challenge: {_val(prob, 'Define the primary operational or financial problem')}",
                f"Affected Stakeholders: {_val(profile.get('affected_people'), 'Identify specific impacted customer personas')}",
                f"Problem Validation: {_val(profile.get('problem_evidence'), 'Reference customer interviews or empirical observations')}",
            ],
        },
        "solution": {
            "title": "Our Solution",
            "subtitle": "A compelling, scalable approach to the problem",
            "bullets": [
                f"Proposed Value: {_val(sol, 'Describe the product solution clearly')}",
                f"Value Proposition: {_val(profile.get('value_proposition'), 'State quantifiable customer ROI or efficiency benefit')}",
                "Designed for seamless workflow adoption and rapid time-to-value.",
            ],
        },
        "product": {
            "title": "Product & Key Features",
            "subtitle": "How the solution delivers tangible outcomes",
            "bullets": [
                f"Core Functionality: {_val(sol, 'Detail product architecture and workflows')}",
                f"Defensible Advantage: {_val(diff, 'Articulate defensive moat or technical differentiation')}",
                f"Delivery Model: {_val(profile.get('delivery'), 'Specify delivery mechanism (SaaS, API, hardware, services)')}",
            ],
        },
        "customers": {
            "title": "Target Customers & Beachhead",
            "subtitle": "Focused Ideal Customer Profile (ICP) for initial traction",
            "bullets": [
                f"Beachhead Segment: {_val(cust, 'Define precise initial buyer persona')}",
                f"Geographic Focus: {_val(profile.get('target_geography'), 'State target launch geography')}",
                f"Customer Discovery: {_val(profile.get('customer_research'), 'Document completed discovery interviews')}",
            ],
        },
        "market": {
            "title": "Market Opportunity",
            "subtitle": "Reachable market size and demand signals",
            "bullets": [
                f"Market Assumptions: {_val(profile.get('market_size'), 'State bottom-up TAM/SAM assumptions')}",
                f"Demand Indicators: {_val(profile.get('demand_evidence'), 'Cite inbound waitlist or trial interest signals')}",
                "[Founder input required: Present bottom-up SAM and Beachhead sizing calculation.]",
            ],
        },
        "competition": {
            "title": "Competitive Landscape",
            "subtitle": "How we win against incumbents and workarounds",
            "bullets": [
                f"Direct Competitors: {_val(comp, 'Identify known direct competitors')}",
                f"Alternative Workarounds: {_val(profile.get('indirect_competitors'), 'Describe manual spreadsheets or partial tools')}",
                f"Differentiating Moat: {_val(diff, 'Highlight cost, speed, IP, or workflow integration advantages')}",
            ],
        },
        "business_model": {
            "title": "Business & Monetization Model",
            "subtitle": "Predictable, scalable unit economics",
            "bullets": [
                f"Revenue Model: {_val(rev_model, 'Detail subscription, transaction, or licensing model')}",
                f"Pricing Structure: {_val(pricing, 'Detail pricing tiers, seat costs, or annual contracts')}",
                f"Acquisition Strategy: {_val(profile.get('acquisition'), 'Outline inbound content, direct outbound, or partnerships')}",
            ],
        },
        "traction": {
            "title": "Traction & Validation Evidence",
            "subtitle": "Empirical evidence supporting current milestone readiness",
            "bullets": [
                f"Current Progress: {_val(profile.get('progress'), 'State current product build and testing status')}",
                f"Commercial Traction: {_val(traction, 'State pilots, LOIs, or verified paying customers (do not fabricate)')}",
            ] + ev_bullets,
        },
        "financials": {
            "title": "Financial & Cost Overview",
            "subtitle": "High-level economics and runway assumptions",
            "bullets": [
                f"Operating Cost Structure: {_val(costs, 'Breakdown hosting, headcount, and marketing expenses')}",
                f"Revenue Projections: {_val(profile.get('revenue_assumptions'), 'State year 1-2 customer acquisition projections')}",
                f"Unit Economics: {_val(profile.get('unit_economics'), 'Provide estimated CAC, LTV, and payback months')}",
            ],
        },
        "roadmap": {
            "title": "Execution Roadmap",
            "subtitle": "Key milestones over the next 18 months",
            "bullets": [
                "Phase 1 (Months 1-6): Complete beta validation trials; harden product stability.",
                "Phase 2 (Months 7-12): Accelerate direct sales; achieve initial scalable MRR.",
                "Phase 3 (Months 13-18): Expand product integrations and prepare Series A expansion.",
            ],
        },
        "funding": {
            "title": "Team & Funding Ask",
            "subtitle": "Resource requirements to unlock next growth stage",
            "bullets": [
                f"Capital Requirement: {_val(cash, 'Specify target round size, e.g. $750,000 for 18 months runway')}",
                f"Core Team Capabilities: {_val(profile.get('capabilities'), 'Highlight founder expertise and engineering depth')}",
                f"Identified Gaps: {_val(profile.get('capability_gaps'), 'State key upcoming hires (e.g. Head of Growth)')}",
                "Contact: [Founder Contact Information & Schedule Demo]",
            ],
        },
    }


def create_pitch_deck_pptx(
    profile: Mapping[str, Any],
    slides_data: Mapping[str, Mapping[str, Any]],
    output_path: str | Path | None = None,
) -> bytes:
    """Generate a 16:9 widescreen, beautifully styled investor pitch deck."""
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    blank_layout = prs.slide_layouts[6]  # Completely blank slide layout

    color_primary = RGBColor(0x15, 0x55, 0x6D)  # Navy Blue
    color_accent = RGBColor(0x2E, 0x7D, 0x9A)   # Teal Accent
    color_dark = RGBColor(0x20, 0x30, 0x40)     # Body Text
    color_amber = RGBColor(0xD3, 0x54, 0x00)    # Missing info warning
    color_light_bg = RGBColor(0xF4, 0xF7, 0xF9) # Light grey/blue

    slide_keys = [k for _, k in PITCH_DECK_SLIDES]

    for idx, slide_key in enumerate(slide_keys):
        s_data = slides_data.get(slide_key, {})
        slide = prs.slides.add_slide(blank_layout)

        # Slide 1: Cover Slide styling
        if idx == 0:
            # Decorative top banner shape
            top_bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.333), Inches(0.4))
            top_bar.fill.solid()
            top_bar.fill.fore_color.rgb = color_primary
            top_bar.line.color.rgb = color_primary

            # Title Box
            tb = slide.shapes.add_textbox(Inches(1.2), Inches(2.2), Inches(11.0), Inches(3.0))
            tf = tb.text_frame
            tf.word_wrap = True

            p1 = tf.paragraphs[0]
            p1.text = s_data.get("title", "Startup Pitch Deck")
            p1.font.name = "Arial"
            p1.font.size = Pt(48)
            p1.font.bold = True
            p1.font.color.rgb = color_primary

            p2 = tf.add_paragraph()
            p2.text = s_data.get("subtitle", "Investor Presentation")
            p2.font.name = "Calibri"
            p2.font.size = Pt(22)
            p2.font.color.rgb = color_accent
            p2.space_before = Pt(14)

            # Footer Box
            fb = slide.shapes.add_textbox(Inches(1.2), Inches(5.8), Inches(11.0), Inches(1.0))
            ftf = fb.text_frame
            for b in s_data.get("bullets", []):
                pb = ftf.add_paragraph()
                pb.text = b
                pb.font.size = Pt(12)
                pb.font.italic = True
                pb.font.color.rgb = RGBColor(0x7F, 0x8C, 0x8D)
            continue

        # Standard Content Slide
        # Top banner line
        top_line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.8), Inches(0.6), Inches(11.7), Inches(0.05))
        top_line.fill.solid()
        top_line.fill.fore_color.rgb = color_accent
        top_line.line.color.rgb = color_accent

        # Slide Title & Subtitle Box
        head_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.7), Inches(11.7), Inches(1.2))
        htf = head_box.text_frame
        htf.word_wrap = True

        title_p = htf.paragraphs[0]
        title_p.text = s_data.get("title", f"Slide {idx + 1}")
        title_p.font.name = "Arial"
        title_p.font.size = Pt(28)
        title_p.font.bold = True
        title_p.font.color.rgb = color_primary

        sub_p = htf.add_paragraph()
        sub_p.text = s_data.get("subtitle", "")
        sub_p.font.name = "Calibri"
        sub_p.font.size = Pt(14)
        sub_p.font.color.rgb = color_accent
        sub_p.space_before = Pt(4)

        # Content Card / Body Box
        content_box = slide.shapes.add_textbox(Inches(0.8), Inches(2.1), Inches(11.7), Inches(4.5))
        ctf = content_box.text_frame
        ctf.word_wrap = True

        bullets = s_data.get("bullets", [])
        for b_idx, bullet_text in enumerate(bullets):
            p = ctf.paragraphs[0] if b_idx == 0 else ctf.add_paragraph()
            p.text = bullet_text
            p.font.name = "Calibri"
            p.font.size = Pt(18)
            p.font.color.rgb = color_amber if "[Founder input required:" in bullet_text else color_dark
            p.space_before = Pt(14)
            p.space_after = Pt(4)
            p.level = 0

        # Footer Slide Indicator
        foot_box = slide.shapes.add_textbox(Inches(0.8), Inches(6.8), Inches(11.7), Inches(0.4))
        ftf = foot_box.text_frame
        p_foot = ftf.paragraphs[0]
        p_foot.text = f"{profile.get('startup_name', 'Startup IQ')} | Investor Presentation • Slide {idx + 1} of {len(slide_keys)}"
        p_foot.font.size = Pt(10)
        p_foot.font.color.rgb = RGBColor(0x95, 0xA5, 0xA6)

    buffer = io.BytesIO()
    prs.save(buffer)
    pptx_bytes = buffer.getvalue()

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(pptx_bytes)

    return pptx_bytes

