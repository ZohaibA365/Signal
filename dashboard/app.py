"""
Signal - the morning dashboard.

Reads the apply_queue mart and presents it as a decision list: what to apply
to today, what to skip, and why. Everything shown here is produced by the
pipeline; nothing is computed in the UI beyond filtering.
"""

from __future__ import annotations

import os

import pandas as pd
import psycopg2
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Signal", page_icon="📡", layout="wide")


@st.cache_resource
def get_connection():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5433"),
        dbname=os.getenv("POSTGRES_DB", "signal"),
        user=os.getenv("POSTGRES_USER", "signal"),
        password=os.getenv("POSTGRES_PASSWORD", "signal"),
    )


@st.cache_data(ttl=300)
def load_queue() -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM apply_queue ORDER BY apply_rank", get_connection())


df = load_queue()

st.title("📡 Signal")
st.caption(
    "Job postings ingested daily, deduplicated to one row per role, and scored "
    "against your profile by Claude. Ranked by whether they are worth your time."
)

scored = df[df["fit_score"].notna()]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Roles tracked", len(df))
c2.metric("Scored", len(scored))
c3.metric("Worth applying to", int((scored["fit_score"] >= 70).sum()))
c4.metric("Blocked (citizenship / clearance)", int((df["eligibility"] == "blocked").sum()))

st.divider()

with st.sidebar:
    st.header("Filters")

    hide_blocked = st.checkbox("Hide blocked roles", value=True,
                               help="Roles requiring US citizenship or a security clearance.")
    min_score = st.slider("Minimum fit score", 0, 100, 45)
    max_age = st.slider("Posted within (days)", 1, 90, 30)

    seniorities = sorted(df["seniority"].dropna().unique())
    picked_seniority = st.multiselect("Seniority", seniorities, default=seniorities)

    states = sorted(df["location_state"].dropna().unique())
    picked_states = st.multiselect("State (empty = all)", states)

view = df.copy()
if hide_blocked:
    view = view[view["eligibility"] != "blocked"]
view = view[
    (view["fit_score"].fillna(-1) >= min_score)
    & (view["days_since_posted"] <= max_age)
    & (view["seniority"].isin(picked_seniority))
]
if picked_states:
    view = view[view["location_state"].isin(picked_states)]

st.subheader(f"{len(view)} roles match your filters")

if view.empty:
    st.info("Nothing matches. Loosen the filters in the sidebar.")
else:
    for _, r in view.head(50).iterrows():
        score = int(r["fit_score"]) if pd.notna(r["fit_score"]) else None
        badge = {"apply now": "🟢", "worth a look": "🟡",
                 "low priority": "⚪", "skip": "🔴"}.get(r["recommendation"], "⚪")

        header = f"{badge}  {score if score is not None else '--'}  ·  {r['job_title']}  ·  {r['company_name']}"
        with st.expander(header):
            left, right = st.columns([2, 1])

            with left:
                if pd.notna(r["fit_reasoning"]):
                    st.markdown(f"**Why this score:** {r['fit_reasoning']}")
                if pd.notna(r["eligibility_reason"]):
                    st.markdown(f"**Eligibility ({r['eligibility']}):** {r['eligibility_reason']}")
                if pd.notna(r["visa_reasoning"]):
                    st.markdown(f"**Sponsorship ({r['sponsorship_signal']}):** {r['visa_reasoning']}")

                concerns = r["concerns"] if isinstance(r["concerns"], list) else []
                if concerns:
                    st.markdown("**Watch out for:** " + " · ".join(concerns))

                stack = r["tech_stack"] if isinstance(r["tech_stack"], list) else []
                if stack:
                    st.markdown("**Tech stack:** " + ", ".join(stack))

            with right:
                st.markdown(f"**Location** {r['location_raw']}")
                st.markdown(f"**Posted** {r['days_since_posted']} days ago")
                st.markdown(f"**Seniority** {r['seniority']}")
                if r["locations_posted"] > 1:
                    st.markdown(f"**Also posted in** {int(r['locations_posted']) - 1} other cities")
                if pd.notna(r["salary_min_reported"]):
                    st.markdown(f"**Salary** ${int(r['salary_min_reported']):,} (stated)")
                if pd.notna(r["redirect_url"]):
                    st.link_button("Open posting", r["redirect_url"])

st.divider()
st.caption(
    "Salary figures are omitted unless the posting stated them - the data source "
    "estimates ~99% of salaries with a model, and those are not shown."
)
