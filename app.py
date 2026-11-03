"""Startup IQ intake, assessment, and results dashboard interface."""

import re
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
import streamlit as st

logger = logging.getLogger(__name__)

from intake import split_lines, validate_submission
from assessment import EVIDENCE_STATUSES, get_dimension_question, reset_assessment_state, validate_assessment_entries
from scoring import DIMENSIONS, calculate_assessment
from dashboard import DISCLAIMER, dimension_rows, evidence_status_counts
from recommendations import generate_recommendations
from report_generator import generate_report, report_filename
from ai_service import (
    EXTRACTION_SECTIONS, STATUS_APPROVED, STATUS_EDITED, STATUS_PENDING, STATUS_REJECTED,
    analyze_startup_idea, analyze_extracted_document, DocumentAnalysisResult, DocumentEvidenceClaim,
)
from document_extractor import extract_document_text, ExtractionResult
from business_plan_generator import (
    BUSINESS_PLAN_SECTIONS, create_business_plan_docx, draft_business_plan_content,
)
from pitch_deck_generator import (
    PITCH_DECK_SLIDES, create_pitch_deck_pptx, draft_pitch_deck_slides,
)
from database import (DEFAULT_DB_PATH, DEFAULT_DOCUMENTS_DIR, EVIDENCE_TYPES, VERIFICATION_STATUSES,
                       archive_evidence, ensure_startup, get_assessment, get_generated_document,
                       initialize_database, list_evidence, list_assessments, list_generated_documents,
                       save_assessment, save_evidence, save_generated_document, store_attachment,
                       update_evidence, create_user, get_user_by_email, get_user_by_id,
                       update_user_last_login, create_workspace, list_user_workspaces,
                       get_workspace, update_workspace, archive_workspace, bootstrap_legacy_ownership,
                       publish_generated_document)
from auth_service import (hash_password, verify_password, validate_password_strength,
                          validate_email, create_session_token, verify_session_token)

st.set_page_config(page_title="Startup IQ | Startup Intake", page_icon="📊", layout="wide")

# ---------------------------------------------------------------------------
# Database initialisation (runs before auth so tables exist)
# ---------------------------------------------------------------------------
try:
    initialize_database()
    database_error = None
except Exception as error:
    database_error = str(error)

# ---------------------------------------------------------------------------
# Auth session-state initialisation
# ---------------------------------------------------------------------------
if "auth_token" not in st.session_state:
    st.session_state.auth_token = None
if "current_user_id" not in st.session_state:
    st.session_state.current_user_id = None
if "current_user_email" not in st.session_state:
    st.session_state.current_user_email = None
if "current_user_name" not in st.session_state:
    st.session_state.current_user_name = None
if "active_workspace_id" not in st.session_state:
    st.session_state.active_workspace_id = None
if "active_workspace_name" not in st.session_state:
    st.session_state.active_workspace_name = None
if "_auth_tab" not in st.session_state:
    st.session_state._auth_tab = "Login"

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _do_logout() -> None:
    """Clear all auth and workspace session state and rerun."""
    for key in ["auth_token", "current_user_id", "current_user_email", "current_user_name",
                "active_workspace_id", "active_workspace_name", "startup_profile",
                "assessment_entries", "assessment_result", "recommendations",
                "assessment_view", "dashboard_view", "assessment_date",
                "saved_assessment_id", "ai_analysis_result", "profile_defaults", "_auth_tab"]:
        st.session_state.pop(key, None)
    st.rerun()


def _resolve_user_from_token() -> bool:
    """Verify stored JWT and populate current_user_* keys. Returns True if valid."""
    token = st.session_state.get("auth_token")
    if not token:
        return False
    payload = verify_session_token(token)
    if payload is None:
        # Token expired or invalid — wipe it
        st.session_state.auth_token = None
        return False
    st.session_state.current_user_id = payload["user_id"]
    st.session_state.current_user_email = payload["email"]
    # Reload full name from DB on each rerun so renames propagate
    user_row = get_user_by_id(payload["user_id"])
    st.session_state.current_user_name = user_row["full_name"] if user_row else payload["email"]
    return True


def _render_auth_wall() -> None:
    """Render login / registration forms and halt further rendering."""
    st.title("Startup IQ")
    auth_mode = st.radio("Account action", ["Log in", "Create account"], horizontal=True, label_visibility="collapsed")

    if auth_mode == "Log in":
        st.subheader("Log in to your account")
        with st.form("login_form"):
            email_in = st.text_input("Email address", key="login_email")
            pw_in = st.text_input("Password", type="password", key="login_pw")
            login_btn = st.form_submit_button("Log in", type="primary", width="stretch")
        if login_btn:
            if not email_in.strip() or not pw_in:
                st.error("Please enter your email and password.")
            else:
                user_row = get_user_by_email(email_in.strip())
                if user_row is None or not verify_password(pw_in, user_row["password_hash"]):
                    st.error("Invalid email or password.")
                elif user_row.get("account_status") != "active":
                    st.error("This account has been deactivated. Contact support.")
                else:
                    token = create_session_token(user_row["user_id"], user_row["email"])
                    st.session_state.auth_token = token
                    st.session_state.current_user_id = user_row["user_id"]
                    st.session_state.current_user_email = user_row["email"]
                    st.session_state.current_user_name = user_row["full_name"]
                    update_user_last_login(user_row["user_id"])
                    # Auto-select workspace if user has exactly one
                    workspaces = list_user_workspaces(user_row["user_id"])
                    if len(workspaces) == 1:
                        st.session_state.active_workspace_id = workspaces[0]["workspace_id"]
                        st.session_state.active_workspace_name = workspaces[0]["workspace_name"]
                    st.rerun()

    else:
        st.subheader("Create a new account")
        with st.form("register_form"):
            full_name_in = st.text_input("Full name", key="reg_name")
            email_reg = st.text_input("Email address", key="reg_email")
            pw_reg = st.text_input("Password", type="password", key="reg_pw",
                                   help="Minimum 8 characters, one uppercase, one lowercase, one digit.")
            pw_confirm = st.text_input("Confirm password", type="password", key="reg_pw_confirm")
            reg_btn = st.form_submit_button("Create account", type="primary", width="stretch")
        if reg_btn:
            errs: list[str] = []
            if not full_name_in.strip():
                errs.append("Full name is required.")
            if not validate_email(email_reg.strip()):
                errs.append("Enter a valid email address.")
            pw_errs = validate_password_strength(pw_reg)
            errs.extend(pw_errs)
            if pw_reg != pw_confirm:
                errs.append("Passwords do not match.")
            if errs:
                for e in errs:
                    st.error(e)
            else:
                existing = get_user_by_email(email_reg.strip())
                if existing:
                    st.error("An account with this email already exists. Please log in.")
                else:
                    try:
                        pw_hash = hash_password(pw_reg)
                        new_uid = create_user(full_name_in.strip(), email_reg.strip().lower(), pw_hash)
                        # Create a default workspace for the new user
                        create_workspace(new_uid, f"{full_name_in.strip()}'s Workspace")
                        st.success("Account created! You can now log in.")
                    except Exception as reg_err:
                        st.error(f"Registration failed: {reg_err}")


def _render_workspace_selector(user_id: int) -> None:
    """Render workspace selection / creation when no workspace is active."""
    st.title("Startup IQ")
    st.subheader("Choose a workspace")
    st.caption("Each workspace holds its own startups, assessments, and evidence records.")
    workspaces = list_user_workspaces(user_id)
    if workspaces:
        ws_options = {ws["workspace_name"]: ws for ws in workspaces}
        chosen_name = st.selectbox("Select a workspace", list(ws_options.keys()))
        if st.button("Open workspace", type="primary"):
            chosen = ws_options[chosen_name]
            st.session_state.active_workspace_id = chosen["workspace_id"]
            st.session_state.active_workspace_name = chosen["workspace_name"]
            st.rerun()
        st.markdown("---")
    st.markdown("#### Create a new workspace")
    with st.form("new_workspace_form"):
        new_ws_name = st.text_input("Workspace name")
        new_ws_desc = st.text_input("Description (optional)")
        create_ws_btn = st.form_submit_button("Create workspace", type="secondary")
    if create_ws_btn:
        if not new_ws_name.strip():
            st.error("Workspace name cannot be empty.")
        else:
            try:
                ws_id = create_workspace(user_id, new_ws_name.strip(), new_ws_desc.strip() or None)
                st.session_state.active_workspace_id = ws_id
                st.session_state.active_workspace_name = new_ws_name.strip()
                st.success(f"Workspace '{new_ws_name.strip()}' created and selected.")
                st.rerun()
            except Exception as ws_err:
                st.error(f"Could not create workspace: {ws_err}")


# ---------------------------------------------------------------------------
# Authentication gate — must pass before any main content renders
# ---------------------------------------------------------------------------
_authenticated = _resolve_user_from_token()

if not _authenticated:
    _render_auth_wall()
    st.stop()

# Workspace gate — if authenticated but no workspace selected yet
if st.session_state.active_workspace_id is None:
    _render_workspace_selector(st.session_state.current_user_id)
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar: User info, workspace selector, and logout
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f"**👤 {st.session_state.current_user_name}**")
    st.caption(st.session_state.current_user_email)
    st.markdown("---")
    # Workspace switcher
    _all_workspaces = list_user_workspaces(st.session_state.current_user_id)
    if len(_all_workspaces) > 1:
        _ws_names = [ws["workspace_name"] for ws in _all_workspaces]
        _ws_ids   = [ws["workspace_id"] for ws in _all_workspaces]
        _cur_idx  = _ws_ids.index(st.session_state.active_workspace_id) if st.session_state.active_workspace_id in _ws_ids else 0
        _selected_ws = st.selectbox("🗂 Active workspace", _ws_names, index=_cur_idx, key="sidebar_ws_select")
        _selected_ws_obj = next(ws for ws in _all_workspaces if ws["workspace_name"] == _selected_ws)
        if _selected_ws_obj["workspace_id"] != st.session_state.active_workspace_id:
            st.session_state.active_workspace_id = _selected_ws_obj["workspace_id"]
            st.session_state.active_workspace_name = _selected_ws_obj["workspace_name"]
            # Reset intake/assessment state when switching workspaces
            for _k in ["startup_profile", "assessment_entries", "assessment_result",
                       "recommendations", "assessment_view", "dashboard_view",
                       "assessment_date", "saved_assessment_id", "ai_analysis_result", "profile_defaults"]:
                st.session_state.pop(_k, None)
            st.rerun()
    else:
        st.caption(f"🗂 **{st.session_state.active_workspace_name}**")

    st.markdown("---")
    if st.button("🚪 Log out", width="stretch"):
        _do_logout()

# Convenience aliases used throughout the page
_uid  = st.session_state.current_user_id
_wsid = st.session_state.active_workspace_id

# ---------------------------------------------------------------------------
# Main application header (only reached when authenticated)
# ---------------------------------------------------------------------------
st.title("Startup IQ")
st.subheader("An analytical indicator, not a magic prediction.")
st.write("Complete this structured profile before assessment. Claims, estimates, and unknowns are kept distinct; no score is calculated at this stage.")

if database_error is not None:
    st.warning("Local assessment history is unavailable in this session. Current in-memory assessment features remain available.")

if "startup_profile" not in st.session_state:
    st.session_state.startup_profile = None
if "assessment_entries" not in st.session_state:
    st.session_state.assessment_entries = None
if "assessment_result" not in st.session_state:
    st.session_state.assessment_result = None
if "recommendations" not in st.session_state:
    st.session_state.recommendations = None
if "assessment_view" not in st.session_state:
    st.session_state.assessment_view = False
if "dashboard_view" not in st.session_state:
    st.session_state.dashboard_view = False
if "assessment_date" not in st.session_state:
    st.session_state.assessment_date = None
if "saved_assessment_id" not in st.session_state:
    st.session_state.saved_assessment_id = None
if "ai_analysis_result" not in st.session_state:
    st.session_state.ai_analysis_result = None
if "profile_defaults" not in st.session_state:
    st.session_state.profile_defaults = {}


def field(label: str, key: str, *, help_text: str | None = None) -> str:
    """Render a consistent text area for longer intake responses."""
    default_val = st.session_state.profile_defaults.get(key, "")
    return st.text_area(label, value=default_val, key=key, help=help_text, height=90)


# -------------------------------------------------------------
# AI-Powered Idea Analysis & Extraction Container
# -------------------------------------------------------------
st.markdown("## ✨ AI Startup Idea Understanding & Structured Extraction (Optional)")
st.caption("Paste your startup idea in plain, natural language. The AI assistant extracts structured candidate information, detects missing details, and surfaces validation evidence claims for your review. No field is saved without your explicit approval.")
ai_raw_input = st.text_area(
    "Describe your startup idea in natural language",
    key="ai_raw_idea_input",
    height=120,
    placeholder="e.g. We are building MediScan, an AI diagnostic system for emergency radiologists. Emergency departments suffer from a 4-hour delay in triage..."
)
col_ai1, col_ai2 = st.columns([1, 4])
with col_ai1:
    analyze_clicked = st.button("AI Analyse", type="secondary", width="stretch")
if analyze_clicked:
    if not ai_raw_input.strip():
        st.error("Please enter a startup description before running AI analysis.")
    else:
        with st.spinner("Analyzing startup idea and extracting structured fields..."):
            analysis_res, ai_err = analyze_startup_idea(ai_raw_input)
            st.session_state.ai_analysis_result = analysis_res
            if ai_err:
                st.warning(ai_err)
            else:
                st.success(f"Analysis completed using {analysis_res.provider_used} engine! Review suggested values below.")

if st.session_state.ai_analysis_result is not None:
    analysis: Any = st.session_state.ai_analysis_result
    st.markdown("### Review & Confirm AI Suggestions")
    st.info("Review candidate values below. You can approve, edit, or reject each suggestion before applying them to your intake form.")

    # Group by sections
    for sec in EXTRACTION_SECTIONS:
        sec_fields = [f for f in analysis.fields if f.section == sec and f.suggested_value]
        if sec_fields:
            st.markdown(f"#### {sec}")
            for item in sec_fields:
                with st.container():
                    c_val, c_status, c_act = st.columns([3, 1, 1])
                    with c_val:
                        edited_val = st.text_input(
                            f"{item.display_label} (Excerpt: {item.supporting_text[:40] + '...' if item.supporting_text else 'Inferred'})",
                            value=item.suggested_value,
                            key=f"ai_field_{item.field_key}"
                        )
                        if edited_val != item.suggested_value:
                            item.suggested_value = edited_val
                            item.confirmation_status = STATUS_EDITED
                    with c_status:
                        st.caption(f"Status: `{item.confirmation_status}`")
                        st.caption(f"{'Inferred' if item.is_inferred else 'Directly stated'}")
                    with c_act:
                        app_btn, rej_btn = st.columns(2)
                        with app_btn:
                            if st.button("Approve", key=f"app_{item.field_key}"):
                                item.confirmation_status = STATUS_APPROVED
                                st.rerun()
                        with rej_btn:
                            if st.button("Reject", key=f"rej_{item.field_key}"):
                                item.confirmation_status = STATUS_REJECTED
                                st.rerun()

    # Missing Information Section
    if analysis.missing_info:
        st.markdown("---")
        st.markdown("### ⚠️ Missing Information Detected")
        st.caption("The AI identified the following critical gaps in your description. Addressing these strengthens your investor-readiness.")
        for miss in analysis.missing_info:
            with st.expander(f"{miss.dimension} — {miss.missing_information}"):
                st.write(f"**Why it matters:** {miss.why_it_matters}")
                st.write(f"**Recommended question:** {miss.suggested_question}")
                st.write(f"**Suggested validation evidence:** {miss.suggested_evidence}")

    # Suggested Evidence Section
    if analysis.suggested_evidence:
        st.markdown("---")
        st.markdown("### 📋 Suggested Evidence Claims for Registry")
        st.caption("The AI detected concrete validation claims. Confirming an item registers it in your Evidence Registry with status 'Founder submitted'.")
        for idx, ev_cand in enumerate(analysis.suggested_evidence):
            with st.expander(f"Claim: {ev_cand.evidence_title} ({ev_cand.evidence_type})"):
                st.write(ev_cand.evidence_description)
                st.caption(f"Related dimension: {ev_cand.related_dimension} | Verification: {ev_cand.verification_status}")
                if ev_cand.supporting_excerpt:
                    st.caption(f"Founder excerpt: \"{ev_cand.supporting_excerpt}\"")
                if not ev_cand.is_approved:
                    if st.button("Register as Evidence", key=f"reg_ev_ai_{idx}"):
                        if st.session_state.startup_profile:
                            save_evidence(st.session_state.startup_profile, ev_cand.as_dict(), workspace_id=_wsid, user_id=_uid)
                            ev_cand.is_approved = True
                            st.success(f"Registered '{ev_cand.evidence_title}' to Evidence Registry!")
                            st.rerun()
                        else:
                            ev_cand.is_approved = True
                            st.info("Claim approved. It will be recorded once your profile is reviewed and saved.")

    # Batch action to apply approved values into the profile intake form
    st.markdown("---")
    b_col1, b_col2 = st.columns([2, 2])
    with b_col1:
        if st.button("Apply Approved Suggestions to Intake Form", type="primary"):
            for item in analysis.fields:
                if item.confirmation_status in (STATUS_APPROVED, STATUS_EDITED) and item.target_profile_key:
                    st.session_state.profile_defaults[item.target_profile_key] = item.suggested_value
            st.success("Approved suggestions loaded into the intake form fields below! Review and submit your profile.")
            st.rerun()
    with b_col2:
        if st.button("Approve All Pending Suggestions"):
            for item in analysis.fields:
                if item.confirmation_status == STATUS_PENDING and item.suggested_value:
                    item.confirmation_status = STATUS_APPROVED
                    if item.target_profile_key:
                        st.session_state.profile_defaults[item.target_profile_key] = item.suggested_value
            st.success("All non-empty suggestions approved and populated into form defaults!")
            st.rerun()

st.markdown("---")


with st.form("startup_intake_form"):
    st.markdown("## 1. Basic information")
    col1, col2, col3 = st.columns(3)
    with col1:
        default_name = st.session_state.profile_defaults.get("startup_name", "")
        default_ind = st.session_state.profile_defaults.get("industry", "")
        startup_name = st.text_input("Startup name *", value=default_name)
        industry = st.text_input("Industry or sector", value=default_ind)
    with col2:
        default_stage = st.session_state.profile_defaults.get("stage", "Idea")
        stage_options = ["Idea", "Early validation", "Pre-seed", "Seed", "Other"]
        stage_idx = stage_options.index(default_stage) if default_stage in stage_options else 0
        stage = st.selectbox("Startup stage", stage_options, index=stage_idx)
        if stage == "Other":
            stage = st.text_input("Describe the startup stage")
    with col3:
        default_geo = st.session_state.profile_defaults.get("geography", "")
        geography = st.text_input("Primary geography (optional)", value=default_geo)
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

    submitted = st.form_submit_button("Review startup profile", type="primary", width="stretch")


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


def render_evidence_registry(profile: dict[str, object], *, workspace_id: int | None = None, user_id: int | None = None) -> None:
    """Render the local evidence inbox without changing assessment scores."""
    st.markdown("## Evidence Inbox / Registry")
    st.caption("Evidence records are separate from assessment ratings and classifications. Verification labels reflect submitted review state, not independent confirmation.")

    # -------------------------------------------------------------
    # Document Text Extraction & AI Evidence Analysis Expander
    # -------------------------------------------------------------
    with st.expander("📄 Document Text Extraction & AI Evidence Analysis", expanded=False):
        st.markdown("#### Upload Supporting Document")
        st.caption("Upload a validation document (PDF, DOCX, TXT, CSV, or XLSX up to 10MB). Text is extracted securely and candidate evidence claims are surfaced for your manual review.")

        doc_upload = st.file_uploader(
            "Select supporting document",
            type=["pdf", "docx", "txt", "csv", "xlsx"],
            key="doc_extractor_file_upload"
        )

        if doc_upload is not None:
            c_meta1, c_meta2, c_meta3 = st.columns(3)
            c_meta1.caption(f"**Filename:** {doc_upload.name}")
            c_meta2.caption(f"**Size:** {len(doc_upload.getvalue()) // 1024} KB")
            c_meta3.caption(f"**Format:** {Path(doc_upload.name).suffix.upper()}")

            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                extract_btn = st.button("Extract Document Text", type="secondary", key="btn_extract_doc_text")
            with col_btn2:
                analyze_doc_btn = st.button("Extract & Run AI Analysis", type="primary", key="btn_analyze_doc_text")

            # Session storage for extracted text and findings
            if f"ext_text_{doc_upload.name}" not in st.session_state:
                st.session_state[f"ext_text_{doc_upload.name}"] = None
            if f"doc_analysis_{doc_upload.name}" not in st.session_state:
                st.session_state[f"doc_analysis_{doc_upload.name}"] = None

            if extract_btn or analyze_doc_btn:
                with st.spinner("Extracting text and structured data from document..."):
                    ext_res: ExtractionResult = extract_document_text(doc_upload.getvalue(), doc_upload.name)
                    st.session_state[f"ext_text_{doc_upload.name}"] = ext_res

                    if not ext_res.success:
                        st.error(ext_res.error)
                    elif ext_res.is_scanned_or_empty:
                        st.warning(ext_res.error or "Document contains no extractable text.")
                    else:
                        st.success(f"Extracted {ext_res.char_count} characters ({ext_res.file_type.upper()}).")

                    if analyze_doc_btn and ext_res.success and ext_res.text:
                        with st.spinner("Analyzing document for startup evidence claims and validation gaps..."):
                            analysis_doc_res: DocumentAnalysisResult = analyze_extracted_document(
                                ext_res.text,
                                doc_upload.name,
                                startup_profile=profile
                            )
                            st.session_state[f"doc_analysis_{doc_upload.name}"] = analysis_doc_res

            cur_ext: ExtractionResult | None = st.session_state.get(f"ext_text_{doc_upload.name}")
            if cur_ext and cur_ext.text:
                st.markdown("##### Preview Extracted Text")
                st.text_area("Extracted Content", value=cur_ext.text, height=180, key=f"preview_text_{doc_upload.name}", disabled=True)

            cur_analysis: DocumentAnalysisResult | None = st.session_state.get(f"doc_analysis_{doc_upload.name}")
            if cur_analysis:
                st.markdown("---")
                st.markdown(f"### 📋 Candidate Evidence Findings ({len(cur_analysis.claims)} found)")
                st.caption(f"Engine: `{cur_analysis.provider_used}` | {cur_analysis.document_summary}")
                if cur_analysis.error:
                    st.info(cur_analysis.error)

                for c_idx, claim in enumerate(cur_analysis.claims):
                    with st.container():
                        st.markdown(f"**Claim {c_idx + 1}: {claim.claim_title}** (`{claim.evidence_type}` → *{claim.related_dimension}*)")
                        st.write(claim.claim_description)
                        if claim.supporting_excerpt:
                            st.caption(f"**Supporting Excerpt:** \"{claim.supporting_excerpt}\"")
                        st.caption(f"Confidence: `{claim.confidence}` | Directly Stated: `{claim.is_directly_stated}` | Action: {claim.recommended_verification}")

                        col_act1, col_act2 = st.columns([2, 5])
                        with col_act1:
                            if claim.approval_status != STATUS_APPROVED and claim.approval_status != STATUS_REJECTED:
                                if st.button(f"Approve & Register Claim #{c_idx + 1}", key=f"app_doc_claim_{c_idx}"):
                                    # Save to evidence registry safely
                                    # Also store the uploaded attachment if not yet stored
                                    att_info = {}
                                    try:
                                        stored, orig, sz = store_attachment(doc_upload.name, doc_upload.getvalue())
                                        att_info = {"attachment_path": stored, "attachment_original_name": orig, "attachment_size": sz}
                                    except Exception:
                                        pass

                                    save_evidence(profile, {
                                        "evidence_title": claim.claim_title,
                                        "evidence_description": claim.claim_description,
                                        "supporting_excerpt": claim.supporting_excerpt,
                                        "evidence_type": claim.evidence_type,
                                        "related_dimension": claim.related_dimension,
                                        "source_name": doc_upload.name,
                                        "source_url": "",
                                        "source_date": date.today().isoformat(),
                                        "verification_status": "Founder submitted",
                                        "founder_notes": f"Extracted via {cur_analysis.provider_used} document analysis. {claim.recommended_verification}.",
                                        **att_info,
                                    }, workspace_id=workspace_id, user_id=user_id)
                                    claim.approval_status = STATUS_APPROVED
                                    st.success(f"Registered claim #{c_idx + 1} into Evidence Registry!")
                                    st.rerun()
                            elif claim.approval_status == STATUS_APPROVED:
                                st.caption("✅ Approved & Registered")
                            elif claim.approval_status == STATUS_REJECTED:
                                st.caption("❌ Rejected")
                        with col_act2:
                            if claim.approval_status != STATUS_APPROVED and claim.approval_status != STATUS_REJECTED:
                                if st.button(f"Reject Claim #{c_idx + 1}", key=f"rej_doc_claim_{c_idx}"):
                                    claim.approval_status = STATUS_REJECTED
                                    st.info(f"Rejected claim #{c_idx + 1}.")
                                    st.rerun()

                if cur_analysis.missing_evidence_gaps:
                    st.markdown("---")
                    st.markdown("#### ⚠️ Document Validation Gaps")
                    for gap in cur_analysis.missing_evidence_gaps:
                        st.markdown(f"**{gap.dimension} — {gap.missing_information}**")
                        st.write(f"- Why it matters: {gap.why_it_matters}")
                        st.write(f"- Follow-up question: {gap.suggested_question}")
                        st.write(f"- Suggested evidence: {gap.suggested_evidence}")

    st.markdown("---")
    with st.expander("Add manual evidence", expanded=False):
        with st.form("evidence_form"):
            title = st.text_input("Evidence title *")
            description = st.text_area("Evidence claim or description *")
            type_choice = st.selectbox("Evidence type *", EVIDENCE_TYPES)
            custom_type = st.text_input("Custom category (used when type is Other)")
            dimension = st.selectbox("Related assessment dimension *", [d.name for d in DIMENSIONS])
            source_name = st.text_input("Source name")
            source_url = st.text_input("Source URL or reference")
            source_date = st.date_input("Evidence date", value=None)
            verification = st.selectbox("Verification status", VERIFICATION_STATUSES)
            notes = st.text_area("Founder notes")
            attachment = st.file_uploader("Optional attachment (PDF, DOCX, XLSX, CSV, TXT, PNG, JPG)", type=["pdf", "docx", "xlsx", "csv", "txt", "png", "jpg", "jpeg"])
            add = st.form_submit_button("Register evidence", type="primary")
        if add:
            selected_type = custom_type.strip() if type_choice == "Other" and custom_type.strip() else type_choice
            if type_choice == "Other" and not custom_type.strip():
                st.error("Enter a custom evidence category.")
            elif not title.strip() or not description.strip():
                st.error("Evidence title and description are required.")
            else:
                attachment_data = {}
                try:
                    if attachment is not None:
                        stored, original, size = store_attachment(attachment.name, attachment.getvalue())
                        attachment_data = {"attachment_path": stored, "attachment_original_name": original, "attachment_size": size}
                    evidence_id = save_evidence(profile, {
                        "evidence_title": title, "evidence_description": description, "evidence_type": selected_type,
                        "related_dimension": dimension, "source_name": source_name, "source_url": source_url,
                        "source_date": source_date.isoformat() if source_date else "", "verification_status": verification,
                        "founder_notes": notes, **attachment_data,
                    }, workspace_id=workspace_id, user_id=user_id)
                    st.success(f"Evidence registered ({evidence_id}).")
                    st.rerun()
                except Exception as error:
                    st.error(f"Evidence could not be registered: {error}")

    records = list_evidence(profile, workspace_id=workspace_id, user_id=user_id)
    filter_cols = st.columns(4)
    search = filter_cols[0].text_input("Search evidence", key="evidence_search")
    dimension_filter = filter_cols[1].selectbox("Filter dimension", ["All"] + [d.name for d in DIMENSIONS], key="evidence_dimension_filter")
    type_filter = filter_cols[2].selectbox("Filter type", ["All"] + list(EVIDENCE_TYPES), key="evidence_type_filter")
    status_filter = filter_cols[3].selectbox("Filter verification", ["All"] + list(VERIFICATION_STATUSES), key="evidence_status_filter")
    records = list_evidence(
        profile,
        dimension=None if dimension_filter == "All" else dimension_filter,
        evidence_type=None if type_filter == "All" else type_filter,
        verification_status=None if status_filter == "All" else status_filter,
        search=search,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    if not records:
        st.info("No registered evidence matches the current filters.")
    for record in records:
        with st.expander(f"{record['evidence_title']} — {record['related_dimension']} — {record['verification_status']}"):
            st.write(record["evidence_description"])
            if record.get("supporting_excerpt"):
                st.caption(f"**Supporting Excerpt:** \"{record['supporting_excerpt']}\"")
            st.caption(f"Type: {record['evidence_type']} | Source: {record['source_name'] or 'Not provided'} | Submitted: {record['submission_date']}")
            if record.get("attachment_original_name"):
                st.caption(f"Attachment: {record['attachment_original_name']} (stored privately)")
            with st.form(f"edit_evidence_{record['evidence_id']}"):
                new_status = st.selectbox("Update verification status", VERIFICATION_STATUSES, index=VERIFICATION_STATUSES.index(record["verification_status"]))
                new_notes = st.text_area("Update founder notes", value=record.get("founder_notes") or "")
                edit, archive = st.columns(2)
                with edit: edit_clicked = st.form_submit_button("Save changes")
                with archive: archive_clicked = st.form_submit_button("Archive record")
            if edit_clicked:
                update_evidence(record["evidence_id"], {"verification_status": new_status, "founder_notes": new_notes}, user_id=user_id)
                st.success("Evidence updated.")
                st.rerun()
            if archive_clicked:
                archive_evidence(record["evidence_id"], user_id=user_id)
                st.success("Evidence archived; historical snapshots are preserved.")
                st.rerun()


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
        st.session_state.startup_profile = submission
        reset_assessment_state(st.session_state)
        st.session_state.assessment_view = False
        st.success("Startup profile received for review.")

        # Register any pending approved AI evidence claims if not yet saved
        if st.session_state.ai_analysis_result is not None:
            for ev_cand in st.session_state.ai_analysis_result.suggested_evidence:
                if ev_cand.is_approved:
                    try:
                        save_evidence(submission, ev_cand.as_dict(), workspace_id=_wsid, user_id=_uid)
                    except Exception as err:
                        pass
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


if st.session_state.startup_profile is not None:
    st.markdown("---")
    profile = st.session_state.startup_profile
    if database_error is None:
        render_evidence_registry(profile, workspace_id=_wsid, user_id=_uid)
    if not st.session_state.assessment_view:
        st.markdown("## Continue to assessment")
        st.write(f"Your validated profile for **{profile['startup_name']}** is stored for this session. Review it above, then continue when ready.")
        if st.button("Continue to seven-dimension assessment", type="primary"):
            st.session_state.assessment_view = True
            st.rerun()
    elif not st.session_state.dashboard_view:
        st.markdown("## Seven-dimension assessment")
        st.write("Rate each dimension from 0 to 100 and explain the rating. This step collects assessment inputs only; it does not calculate a final score.")
        st.info("A missing evidence rationale is not converted into a zero. Select ‘Not yet known’ when information is unavailable and explain the gap. Confirm each rating only after reviewing it.")
        with st.form("assessment_form"):
            assessment_entries = {}
            for dimension in DIMENSIONS:
                st.markdown(f"### {dimension.name} — {dimension.weight}%")
                st.caption(get_dimension_question(dimension.identifier))
                
                # Show relevant evidence records for this dimension
                if database_error is None:
                    dim_evidence = list_evidence(profile, dimension=dimension.name, workspace_id=_wsid, user_id=_uid)
                    if dim_evidence:
                        with st.expander(f"Supporting evidence for {dimension.name} ({len(dim_evidence)} registered)", expanded=False):
                            st.caption("Verification labels reflect submitted review state and do not guarantee claim truth. An existing evidence record does not automatically increase or decrease the rating.")
                            for ev in dim_evidence:
                                att_info = f" | Attachment: {ev['attachment_original_name']}" if ev.get("attachment_original_name") else ""
                                st.markdown(f"- **{ev['evidence_title']}** ({ev['evidence_type']}) — *Verification:* `{ev['verification_status']}`{att_info}")
                                if ev.get("evidence_description"):
                                    st.caption(f"  Claim: {ev['evidence_description']}")
                    else:
                        st.caption(f"*No registered evidence currently recorded for {dimension.name}.*")

                rating = st.slider(
                    f"{dimension.name} rating",
                    min_value=0,
                    max_value=100,
                    value=50,
                    key=f"rating_{dimension.identifier}",
                )
                reviewed = st.checkbox(
                    "I reviewed this rating and it reflects the current information",
                    key=f"reviewed_{dimension.identifier}",
                )
                evidence_status = st.selectbox(
                    "Information status",
                    EVIDENCE_STATUSES,
                    key=f"status_{dimension.identifier}",
                )
                rationale = st.text_area(
                    "Supporting rationale or evidence",
                    key=f"rationale_{dimension.identifier}",
                    height=100,
                    help="Describe the basis for the rating. State what is supported, estimated, hypothesized, or not yet known.",
                )
                assessment_entries[dimension.identifier] = {
                    "rating": rating,
                    "reviewed": reviewed,
                    "evidence_status": evidence_status,
                    "rationale": rationale,
                }
            assessment_submitted = st.form_submit_button("Save assessment inputs", type="primary", width="stretch")

        if assessment_submitted:
            errors = validate_assessment_entries(assessment_entries)
            if errors:
                st.error("Please complete the assessment inputs:")
                for error in errors:
                    st.write(f"- {error}")
            else:
                st.session_state.assessment_entries = assessment_entries
                st.session_state.assessment_result = calculate_assessment(assessment_entries)
                st.session_state.recommendations = generate_recommendations(st.session_state.assessment_result)
                st.session_state.assessment_date = date.today().isoformat()
                if database_error is None:
                    try:
                        st.session_state.saved_assessment_id = save_assessment(
                            profile,
                            assessment_entries,
                            st.session_state.assessment_result,
                            st.session_state.recommendations,
                            st.session_state.assessment_date,
                        evidence_ids=[item["evidence_id"] for item in list_evidence(profile, workspace_id=_wsid, user_id=_uid)],
                        workspace_id=_wsid,
                        user_id=_uid,
                        )
                    except Exception as error:
                        st.session_state.saved_assessment_id = None
                        st.error(f"Assessment was calculated but could not be saved to local history: {error}")
                    else:
                        st.success(f"Assessment saved to local history (ID {st.session_state.saved_assessment_id}).")
                st.session_state.dashboard_view = True
                st.success("Assessment inputs saved and scored deterministically for this session.")
                st.rerun()

        if st.button("Return to profile review"):
            st.session_state.assessment_view = False
            st.rerun()
    else:
        result = st.session_state.assessment_result
        if result is None:
            st.session_state.dashboard_view = False
            st.warning("Complete and save a valid assessment before viewing results.")
        else:
            rows = dimension_rows(result)
            st.markdown("## Assessment results")
            st.caption("This dashboard displays the current deterministic assessment result; it does not predict business success.")
            profile_cols = st.columns(3)
            profile_cols[0].metric("Startup", profile["startup_name"])
            profile_cols[1].metric("Stage", profile.get("stage") or "Not provided")
            profile_cols[2].metric("Overall readiness", f"{result.overall_score:.2f} / 100")
            st.info("Assessment indicator only — a high score does not guarantee success, funding, profitability, or investment returns.")

            st.markdown("### Dimension ratings")
            chart = go.Figure(go.Bar(
                x=[row["rating"] for row in rows],
                y=[row["dimension"] for row in rows],
                orientation="h",
                text=[f"{row['rating']}/100" for row in rows],
                textposition="outside",
                marker_color="#2E7D9A",
            ))
            chart.update_layout(
                title="Ratings by assessment dimension",
                xaxis={"title": "Rating", "range": [0, 100]},
                yaxis={"title": "", "autorange": "reversed"},
                height=430,
                margin={"l": 10, "r": 55, "t": 65, "b": 45},
            )
            st.plotly_chart(chart, width="stretch")

            st.markdown("### Dimension breakdown")
            for row in rows:
                with st.expander(f"{row['dimension']} — {row['rating']}/100 | {row['weight']}% weight | {row['contribution']:.2f} contribution", expanded=True):
                    detail_cols = st.columns(3)
                    detail_cols[0].markdown(f"**Evidence status:** {row['evidence_status']}")
                    detail_cols[1].markdown(f"**Weighted contribution:** {row['contribution']:.2f}")
                    detail_cols[2].markdown(f"**Evidence gap:** {'Yes' if row['missing_evidence'] else 'No'}")
                    st.markdown(f"**Supporting rationale:** {row['rationale'] or 'Not provided'}")
                    if row["missing_evidence"]:
                        st.warning("Supporting evidence is limited or not yet known. This flag is separate from the numeric rating.")

            st.markdown("### Evidence status")
            counts = evidence_status_counts(result)
            for status in EVIDENCE_STATUSES:
                st.write(f"- **{status}:** {counts.get(status, 0)} dimension(s)")
            st.caption("These are status counts, not a scientifically validated evidence-coverage percentage.")

            with st.expander("Assessment details", expanded=False):
                st.write(f"**Startup name:** {profile.get('startup_name') or 'Not provided'}")
                st.write(f"**Industry:** {profile.get('industry') or 'Not provided'}")
                st.write(f"**Startup stage:** {profile.get('stage') or 'Not provided'}")
                st.write(f"**Assessment date:** {st.session_state.assessment_date or 'Not provided'}")
                st.write(f"**Rubric version:** {result.rubric_version}")
                st.write(f"**Assessment dimensions:** {len(result.dimensions)}")

            st.warning(DISCLAIMER)
            recommendations = st.session_state.recommendations or ()
            report_html = generate_report(
                profile,
                result,
                recommendations,
                st.session_state.assessment_date,
                evidence=list_evidence(profile, workspace_id=_wsid, user_id=_uid),
            )
            st.download_button(
                "Download Assessment Report",
                data=report_html,
                file_name=report_filename(profile.get("startup_name", "startup"), st.session_state.assessment_date or "assessment"),
                mime="text/html",
                width="stretch",
            )
            st.markdown("### Rule-based recommendations")
            if not recommendations:
                st.success("No high-priority actions were generated for this assessment. Continue reviewing assumptions and evidence as the startup develops.")
            else:
                st.caption("These recommendations are deterministic rules based on the current ratings and evidence statuses. They are not AI-generated advice.")
                for recommendation in recommendations:
                    with st.expander(f"{recommendation.priority} priority — {recommendation.dimension}", expanded=True):
                        st.markdown(f"**Issue:** {recommendation.issue}")
                        st.markdown(f"**Explanation:** {recommendation.explanation}")
                        st.markdown(f"**Recommended next action:** {recommendation.recommended_action}")
                        st.markdown(f"**Suggested evidence or validation:** {recommendation.suggested_evidence}")
                        st.markdown(f"**Reason:** {recommendation.reason}")
            if st.button("Return to assessment inputs"):
                st.session_state.dashboard_view = False
                st.rerun()

            # Defer rendering until after the module-level generator function
            # has been defined below. Streamlit reruns execute this module
            # top-to-bottom, so calling it here raises NameError on the first
            # dashboard render.
            st.session_state._active_document_generator_record = {
                "id": st.session_state.saved_assessment_id or 0,
                "profile": profile,
                "result": result,
                "recommendations": recommendations,
                "assessment_date": st.session_state.assessment_date,
                "evidence": list_evidence(profile, workspace_id=_wsid, user_id=_uid),
            }


def render_historical_assessment(record: dict[str, object]) -> None:
    """Display an immutable saved assessment without changing active session state."""
    historical_result = record["result"]
    historical_profile = record["profile"]
    historical_recommendations = record["recommendations"]
    st.markdown("## Historical assessment")
    st.info("This is a saved historical snapshot. Selecting it does not overwrite the currently active assessment.")
    columns = st.columns(3)
    columns[0].metric("Assessment ID", record["id"])
    columns[1].metric("Startup", historical_profile.get("startup_name", "Not provided"))
    columns[2].metric("Overall score", f"{historical_result.overall_score:.2f} / 100")
    st.write(f"**Assessment date:** {record.get('assessment_date') or 'Not provided'}")
    st.write(f"**Rubric version:** {historical_result.rubric_version}")
    for item in historical_result.dimensions:
        with st.expander(f"{item.name} — {item.rating}/100 | {item.contribution:.2f} contribution"):
            st.write(f"**Weight:** {item.weight}%")
            st.write(f"**Evidence status:** {item.evidence_status}")
            st.write(f"**Missing evidence:** {'Yes' if item.missing_evidence else 'No'}")
            st.write(f"**Rationale:** {item.rationale or 'Not provided'}")

    saved_evidence = record.get("evidence", [])
    st.markdown("### Historical evidence snapshot")
    if not saved_evidence:
        st.write("No registered evidence was associated with this historical assessment snapshot.")
    else:
        for ev in saved_evidence:
            with st.expander(f"{ev.get('evidence_title', 'Untitled')} — {ev.get('related_dimension', 'General')} ({ev.get('verification_status', 'Not reviewed')})"):
                st.write(ev.get("evidence_description", ""))
                st.caption(f"Type: {ev.get('evidence_type')} | Source: {ev.get('source_name') or 'Not provided'} | Date: {ev.get('source_date') or 'Not provided'}")
                if ev.get("attachment_original_name"):
                    st.caption(f"Attachment (historical metadata): {ev.get('attachment_original_name')}")

    st.markdown("### Saved recommendations")
    if not historical_recommendations:
        st.write("No recommendations were saved for this assessment.")
    for recommendation in historical_recommendations:
        st.markdown(f"- **{recommendation.priority} — {recommendation.dimension}:** {recommendation.recommended_action}")
    historical_html = generate_report(
        historical_profile,
        historical_result,
        historical_recommendations,
        record.get("assessment_date"),
        evidence=record.get("evidence", []),
    )
    st.download_button(
        "Download Historical Assessment Report",
        data=historical_html,
        file_name=report_filename(historical_profile.get("startup_name", "startup"), record.get("assessment_date") or "assessment"),
        mime="text/html",
        key=f"historical_report_{record['id']}",
    )

    # Render document generator for this historical snapshot
    render_document_generator(historical_profile, record)


# -------------------------------------------------------------------
# Document Generator Section: Business Plan (DOCX) & Pitch Deck (PPTX)
# -------------------------------------------------------------------
def render_document_generator(profile: dict[str, Any], assessment_record: dict[str, Any] | None = None) -> None:
    """Render the founder document generation section for DOCX and PPTX exports."""
    st.markdown("---")
    st.markdown("## 📄 Founder Document Generator (DOCX & PPTX)")
    st.caption("Generate professional, editable Business Plans (DOCX) and Investor Pitch Decks (PPTX). Content is strictly derived from validated profile data, analytical readiness scores, and registered evidence without fabricating metrics.")

    record_suffix = f"_{assessment_record['id']}" if assessment_record else "_active"
    bp_key = f"last_generated_bp{record_suffix}"
    bp_fname_key = f"last_bp_fname{record_suffix}"
    deck_key = f"last_generated_deck{record_suffix}"
    deck_fname_key = f"last_deck_fname{record_suffix}"

    col_type, col_mode = st.columns([2, 2])
    with col_type:
        doc_choice = st.radio("Select document format to generate:", ["Business Plan (Word DOCX)", "Investor Pitch Deck (PowerPoint PPTX)"], horizontal=True, key=f"doc_choice{record_suffix}")
    with col_mode:
        st.write("**Data Source Snapshot:**")
        if assessment_record:
            st.info(f"Using saved Assessment #{assessment_record['id']} snapshot ({assessment_record.get('assessment_date') or 'Unspecified Date'}).")
        else:
            st.info(f"Using current active session profile for '{profile.get('startup_name', 'Current Startup')}'.")

    # Business Plan Flow
    if doc_choice == "Business Plan (Word DOCX)":
        with st.expander("Preview & Edit Business Plan Sections (22 Sections)", expanded=False):
            st.caption("Review or customize section content prior to exporting. Missing facts are flagged with amber prompts.")
            bp_sections = draft_business_plan_content(
                profile,
                assessment=assessment_record,
                evidence=assessment_record.get("evidence", []) if assessment_record else list_evidence(profile, workspace_id=_wsid, user_id=_uid),
                recommendations=assessment_record.get("recommendations", []) if assessment_record else (st.session_state.recommendations or ()),
            )
            for title, key in BUSINESS_PLAN_SECTIONS:
                st.markdown(f"**{title}**")
                bp_sections[key] = st.text_area(title, value=bp_sections.get(key, ""), key=f"bp_edit_{key}{record_suffix}", height=100, label_visibility="collapsed")

        if st.button("Generate Business Plan (DOCX)", type="primary", key=f"btn_gen_bp{record_suffix}"):
            with st.spinner("Compiling professional DOCX document..."):
                bp_sections = draft_business_plan_content(
                    profile,
                    assessment=assessment_record,
                    evidence=assessment_record.get("evidence", []) if assessment_record else list_evidence(profile, workspace_id=_wsid, user_id=_uid),
                    recommendations=assessment_record.get("recommendations", []) if assessment_record else (st.session_state.recommendations or ()),
                )
                docx_bytes = create_business_plan_docx(profile, bp_sections)
                # Persist only for an owned, saved assessment. Active unsaved
                # previews remain in session state and are not written to disk.
                s_name = re.sub(r"[^A-Za-z0-9]+", "-", str(profile.get("startup_name", "startup"))).lower()
                document_id = str(uuid.uuid4())
                doc_fname = f"business-plan-{s_name}-{document_id[:12]}.docx"
                file_rel = f"documents/workspace-{_wsid}/{doc_fname}"
                published_path = None
                if assessment_record:
                    if database_error is not None:
                        raise RuntimeError("Database is unavailable; document was not persisted.")
                    published_path = publish_generated_document(document_id, docx_bytes, file_rel)
                try:
                    if assessment_record:
                        save_generated_document(
                            startup_id=ensure_startup(profile, workspace_id=_wsid, user_id=_uid),
                            assessment_id=assessment_record["id"],
                            document_type="Business Plan",
                            title=f"{profile.get('startup_name')} - Business Plan",
                            file_path=file_rel,
                            file_format="docx",
                            generation_mode="verified_snapshot",
                            sections=bp_sections,
                            workspace_id=_wsid,
                            user_id=_uid,
                            document_id=document_id,
                        )
                except Exception:
                    if published_path is not None:
                        try:
                            published_path.unlink(missing_ok=True)
                        except Exception as cleanup_error:
                            logger.error("Business Plan cleanup failed after registration error: %s", cleanup_error)
                            st.error(f"Document cleanup failed: {cleanup_error}")
                    raise
                st.session_state[bp_key] = docx_bytes
                st.session_state[bp_fname_key] = doc_fname
                st.success("Business plan generated successfully!")

        if bp_key in st.session_state:
            st.download_button(
                label="📥 Download Business Plan (.DOCX)",
                data=st.session_state[bp_key],
                file_name=st.session_state.get(bp_fname_key, "business-plan.docx"),
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
                width="stretch",
                key=f"dl_btn_bp{record_suffix}",
            )

    # Pitch Deck Flow
    else:
        with st.expander("Preview & Edit Pitch Deck Slides (12 Slides)", expanded=False):
            st.caption("Review or adjust slide bullet points prior to presentation export.")
            deck_slides = draft_pitch_deck_slides(
                profile,
                assessment=assessment_record,
                evidence=assessment_record.get("evidence", []) if assessment_record else list_evidence(profile, workspace_id=_wsid, user_id=_uid),
            )
            for title, key in PITCH_DECK_SLIDES:
                s_info = deck_slides.get(key, {})
                st.markdown(f"**{title} — {s_info.get('title')}**")
                bullets_text = "\n".join(s_info.get("bullets", []))
                edited_bullets = st.text_area(f"Bullets for {key}", value=bullets_text, key=f"deck_edit_{key}{record_suffix}", height=80, label_visibility="collapsed")
                s_info["bullets"] = [b.strip() for b in edited_bullets.splitlines() if b.strip()]

        if st.button("Generate Pitch Deck (PPTX)", type="primary", key=f"btn_gen_deck{record_suffix}"):
            with st.spinner("Building styled 16:9 widescreen PowerPoint deck..."):
                deck_slides = draft_pitch_deck_slides(
                    profile,
                    assessment=assessment_record,
                    evidence=assessment_record.get("evidence", []) if assessment_record else list_evidence(profile, workspace_id=_wsid, user_id=_uid),
                )
                pptx_bytes = create_pitch_deck_pptx(profile, deck_slides)
                s_name = re.sub(r"[^A-Za-z0-9]+", "-", str(profile.get("startup_name", "startup"))).lower()
                document_id = str(uuid.uuid4())
                doc_fname = f"pitch-deck-{s_name}-{document_id[:12]}.pptx"
                file_rel = f"documents/workspace-{_wsid}/{doc_fname}"
                published_path = None
                if assessment_record:
                    if database_error is not None:
                        raise RuntimeError("Database is unavailable; document was not persisted.")
                    published_path = publish_generated_document(document_id, pptx_bytes, file_rel)
                try:
                    if assessment_record:
                        save_generated_document(
                            startup_id=ensure_startup(profile, workspace_id=_wsid, user_id=_uid),
                            assessment_id=assessment_record["id"],
                            document_type="Pitch Deck",
                            title=f"{profile.get('startup_name')} - Pitch Deck",
                            file_path=file_rel,
                            file_format="pptx",
                            generation_mode="verified_snapshot",
                            sections={k: v.get("bullets", []) for k, v in deck_slides.items()},
                            workspace_id=_wsid,
                            user_id=_uid,
                            document_id=document_id,
                        )
                except Exception:
                    if published_path is not None:
                        try:
                            published_path.unlink(missing_ok=True)
                        except Exception as cleanup_error:
                            logger.error("Pitch Deck cleanup failed after registration error: %s", cleanup_error)
                            st.error(f"Document cleanup failed: {cleanup_error}")
                    raise
                st.session_state[deck_key] = pptx_bytes
                st.session_state[deck_fname_key] = doc_fname
                st.success("Investor pitch deck generated successfully!")

        if deck_key in st.session_state:
            st.download_button(
                label="📥 Download Investor Pitch Deck (.PPTX)",
                data=st.session_state[deck_key],
                file_name=st.session_state.get(deck_fname_key, "pitch-deck.pptx"),
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                type="primary",
                width="stretch",
                key=f"dl_btn_deck{record_suffix}",
            )

    # Document History List
    if database_error is None and assessment_record:
        doc_history = list_generated_documents(assessment_id=assessment_record["id"], workspace_id=_wsid, user_id=_uid)
        if doc_history:
            st.markdown("#### Previously Generated Document Versions")
            for doc_item in doc_history:
                st.caption(f"• **v{doc_item['document_version']}** {doc_item['document_type']} — Format: `{doc_item['file_format'].upper()}` — Created: {doc_item['created_at']}")


if st.session_state.get("_active_document_generator_record") is not None:
    _active_document_record = st.session_state.pop("_active_document_generator_record")
    render_document_generator(_active_document_record["profile"], _active_document_record)


if database_error is None:
    try:
        history = list_assessments(workspace_id=_wsid, user_id=_uid)
    except Exception as error:
        history = []
        st.sidebar.error(f"Assessment history could not be loaded: {error}")
    st.sidebar.markdown("### Assessment history")
    if not history:
        st.sidebar.caption("No saved assessments yet. Complete an assessment to create the first history record.")
    else:
        history_options = [None] + [item["id"] for item in history]
        selected_history_id = st.sidebar.selectbox(
            "Open a saved assessment",
            history_options,
            format_func=lambda assessment_id: "Select an assessment" if assessment_id is None else next(
                f"#{item['id']} — {item['startup_name'] or 'Unnamed'} — {item['overall_score']:.2f}/100" for item in history if item["id"] == assessment_id
            ),
        )
        if selected_history_id is not None:
            historical_record = get_assessment(selected_history_id, user_id=_uid)
            if historical_record is not None:
                render_historical_assessment(historical_record)
