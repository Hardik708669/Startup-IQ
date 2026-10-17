"""Startup IQ Day 2 startup information intake interface."""

import streamlit as st

from intake import split_lines, validate_submission

st.set_page_config(page_title="Startup IQ | Startup Intake", page_icon="📊", layout="wide")
st.title("Startup IQ")
st.subheader("An analytical indicator, not a magic prediction.")
st.write("Complete this structured profile before assessment. Claims, estimates, and unknowns are kept distinct; no score is calculated at this stage.")


def field(label: str, key: str, *, help_text: str | None = None) -> str:
    """Render a consistent text area for longer intake responses."""
    return st.text_area(label, key=key, help=help_text, height=90)


with st.form("startup_intake_form"):
    st.markdown("## 1. Basic information")
    col1, col2, col3 = st.columns(3)
    with col1:
        startup_name = st.text_input("Startup name *")
        industry = st.text_input("Industry or sector")
    with col2:
        stage = st.selectbox("Startup stage", ["Idea", "Early validation", "Pre-seed", "Seed", "Other"])
        if stage == "Other":
            stage = st.text_input("Describe the startup stage")
    with col3:
        geography = st.text_input("Primary geography (optional)")
    startup_description = field("Short startup description *", "startup_description", help_text="Describe what the startup does in one or two sentences.")

    st.markdown("## 2. Problem and solution")
    problem_statement = field("Problem statement *", "problem_statement")
    affected_people = field("Who is affected by this problem?", "affected_people")
    proposed_solution = field("Proposed solution *", "proposed_solution")
    value_proposition = field("Main value proposition", "value_proposition")
    problem_evidence = field("Evidence that the problem exists", "problem_evidence", help_text="State whether this is verified evidence, an estimate, a hypothesis, or unknown.")

    st.markdown("## 3. Market")
    target_customers = field("Target customers *", "target_customers", help_text="Name the specific customer group, not only a broad industry.")
    target_geography = st.text_input("Target geography, if different")
    market_size = field("Market size estimates or assumptions", "market_size")
    customer_research = field("Customer research", "customer_research")
    demand_evidence = field("Evidence of demand", "demand_evidence")

    st.markdown("## 4. Competition")
    direct_competitors = field("Direct competitors", "direct_competitors")
    indirect_competitors = field("Indirect competitors or alternatives", "indirect_competitors")
    differentiators = field("Differentiating factors", "differentiators")
    competitive_evidence = field("Evidence supporting the proposed advantage", "competitive_evidence")

    st.markdown("## 5. Business model")
    revenue_model = field("Revenue model", "revenue_model")
    pricing = field("Proposed pricing and pricing assumptions", "pricing")
    acquisition = field("Customer acquisition channels", "acquisition")
    delivery = field("Delivery or operational model", "delivery")

    st.markdown("## 6. Financial assumptions")
    estimated_costs = field("Estimated costs", "estimated_costs")
    revenue_assumptions = field("Revenue assumptions", "revenue_assumptions")
    cash_requirements = field("Cash requirements", "cash_requirements")
    unit_economics = field("Unit economics, if relevant", "unit_economics")

    st.markdown("## 7. Traction and team")
    progress = field("Current progress", "progress")
    interviews_trials = field("Customer interviews or trials", "interviews_trials")
    traction = field("Sales or other traction, if any", "traction", help_text="If none exists, write Not yet known rather than inventing a result.")
    capabilities = field("Team capabilities", "capabilities")
    capability_gaps = field("Important capability gaps", "capability_gaps")

    st.markdown("## 8. Risks and mitigations")
    business_risks = field("Important business risks", "business_risks")
    technical_operational_risks = field("Technical and operational risks", "technical_operational_risks")
    mitigations = field("Proposed mitigations", "mitigations")

    st.markdown("## 9. Supporting evidence")
    evidence_status = st.selectbox("Overall evidence status", ["Not yet reviewed", "Mostly assumptions", "Mixed claims and evidence", "Mostly supported"])
    evidence_records = field("Evidence records (one item per line)", "evidence_records", help_text="Include a description, source/URL, date, and related dimension where available.")
    unknown_information = field("Information not yet known", "unknown_information", help_text="List gaps explicitly. Missing information is not automatically a zero.")

    submitted = st.form_submit_button("Review startup profile", type="primary", use_container_width=True)


def render_summary_field(label: str, value: object) -> None:
    """Render one summary field without fabricating missing information."""
    if isinstance(value, list):
        if value:
            st.markdown(f"**{label}:**")
            for item in value:
                st.markdown(f"- {item}")
        else:
            st.markdown(f"**{label}:** Not provided")
    else:
        text = str(value).strip() if value is not None else ""
        st.markdown(f"**{label}:** {text or 'Not provided'}")


def render_summary_section(title: str, fields: list[tuple[str, str]], submission: dict[str, object]) -> None:
    """Render a labeled profile section in a collapsible expander."""
    with st.expander(title, expanded=True):
        for label, key in fields:
            render_summary_field(label, submission.get(key, ""))


if submitted:
    submission = {
        "startup_name": startup_name,
        "industry": industry,
        "stage": stage,
        "geography": geography,
        "startup_description": startup_description,
        "problem_statement": problem_statement,
        "affected_people": affected_people,
        "proposed_solution": proposed_solution,
        "value_proposition": value_proposition,
        "problem_evidence": problem_evidence,
        "target_customers": target_customers,
        "target_geography": target_geography,
        "market_size": market_size,
        "customer_research": customer_research,
        "demand_evidence": demand_evidence,
        "direct_competitors": direct_competitors,
        "indirect_competitors": indirect_competitors,
        "differentiators": differentiators,
        "competitive_evidence": competitive_evidence,
        "revenue_model": revenue_model,
        "pricing": pricing,
        "acquisition": acquisition,
        "delivery": delivery,
        "estimated_costs": estimated_costs,
        "revenue_assumptions": revenue_assumptions,
        "cash_requirements": cash_requirements,
        "unit_economics": unit_economics,
        "progress": progress,
        "interviews_trials": interviews_trials,
        "traction": traction,
        "capabilities": capabilities,
        "capability_gaps": capability_gaps,
        "business_risks": business_risks,
        "technical_operational_risks": technical_operational_risks,
        "mitigations": mitigations,
        "evidence_status": evidence_status,
        "evidence_records": split_lines(evidence_records),
        "unknown_information": split_lines(unknown_information),
    }
    errors = validate_submission(submission)
    if errors:
        st.error("Please correct the following:")
        for error in errors:
            st.write(f"- {error}")
    else:
        st.success("Startup profile received for review.")
        st.markdown("## Submission summary")
        st.caption("This is an intake summary only. No score or recommendation has been generated.")
        summary_cols = st.columns(3)
        summary_cols[0].metric("Startup", submission["startup_name"])
        summary_cols[1].metric("Stage", submission["stage"])
        summary_cols[2].metric("Evidence status", submission["evidence_status"])
        render_summary_section("1. Basic Information", [
            ("Startup name", "startup_name"), ("Industry or sector", "industry"),
            ("Startup stage", "stage"), ("Primary geography", "geography"),
        ], submission)
        render_summary_section("2. Problem and Solution", [
            ("Short description", "startup_description"), ("Problem statement", "problem_statement"),
            ("People affected", "affected_people"), ("Proposed solution", "proposed_solution"),
            ("Main value proposition", "value_proposition"), ("Problem evidence", "problem_evidence"),
        ], submission)
        render_summary_section("3. Market", [
            ("Target customers", "target_customers"), ("Target geography", "target_geography"),
            ("Market size estimates or assumptions", "market_size"),
            ("Customer research", "customer_research"), ("Evidence of demand", "demand_evidence"),
        ], submission)
        render_summary_section("4. Competition", [
            ("Direct competitors", "direct_competitors"),
            ("Indirect competitors or alternatives", "indirect_competitors"),
            ("Differentiating factors", "differentiators"),
            ("Evidence supporting proposed advantage", "competitive_evidence"),
        ], submission)
        render_summary_section("5. Business Model", [
            ("Revenue model", "revenue_model"), ("Proposed pricing", "pricing"),
            ("Customer acquisition channels", "acquisition"), ("Delivery or operational model", "delivery"),
        ], submission)
        render_summary_section("6. Financial Assumptions", [
            ("Estimated costs", "estimated_costs"), ("Revenue assumptions", "revenue_assumptions"),
            ("Cash requirements", "cash_requirements"), ("Unit economics", "unit_economics"),
        ], submission)
        render_summary_section("7. Traction and Team", [
            ("Current progress", "progress"), ("Customer interviews or trials", "interviews_trials"),
            ("Sales or other traction", "traction"), ("Team capabilities", "capabilities"),
            ("Capability gaps", "capability_gaps"),
        ], submission)
        render_summary_section("8. Risks and Mitigations", [
            ("Important business risks", "business_risks"),
            ("Technical and operational risks", "technical_operational_risks"),
            ("Proposed mitigations", "mitigations"),
        ], submission)
        with st.expander("9. Supporting Evidence", expanded=True):
            render_summary_field("Overall evidence status", submission.get("evidence_status", ""))
            st.markdown(f"**Evidence record count:** {len(submission['evidence_records'])}")
            render_summary_field("Evidence records", submission["evidence_records"])
            st.markdown(f"**Unknown information count:** {len(submission['unknown_information'])}")
            render_summary_field("Information not yet known", submission["unknown_information"])
