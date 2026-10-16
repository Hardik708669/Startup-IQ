"""Minimal Streamlit entry point for Startup IQ's Day 1 foundation."""

import streamlit as st


st.set_page_config(page_title="Startup IQ", page_icon="📊", layout="centered")
st.title("Startup IQ")
st.subheader("An analytical indicator, not a magic prediction.")
st.write(
    "Startup IQ is an assessment workspace for founders who want to examine "
    "their idea, evidence, assumptions, and readiness in a structured way."
)

st.info("The assessment workflow is under development. No assessment results are generated yet.")
st.markdown("### Project status")
st.write("✅ Day 1 foundation: rubric, scope, sample data, and test scaffold")
st.write("🛠️ Next: startup information intake form (17 October 2026)")
