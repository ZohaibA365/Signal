"""
Signal Market Index - the public page.

Shows what the US data-engineering job market is actually asking for: which
technologies carry demand, what they pay, and which tools travel together.

Two series appear here and they are NOT equally trustworthy. Keeping them
distinct is the single most important thing this page does:

  * market_demand      - counts straight from the job board's market-wide
                         index. Trustworthy. Labelled "live market".
  * tech_demand_history - reconstructed from the posted_date of postings we
                         happened to collect. A biased sample: an old posting
                         only appears if it is STILL listed, so months skew
                         toward slow-to-fill roles. Labelled as a sample, and
                         only months with a usable sample size are drawn.

Presenting the second as market-wide demand would be the most damaging error
this project could make, so the distinction is enforced in the UI, not just
in a comment.
"""

from __future__ import annotations

import os

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "storage"))
from db import connect  # noqa: E402

load_dotenv()

st.set_page_config(page_title="Signal Market Index", page_icon="📈", layout="wide")


@st.cache_resource
def get_connection():
    return connect(autocommit=True)


@st.cache_data(ttl=900)
def q(sql: str) -> pd.DataFrame:
    return pd.read_sql(sql, get_connection())


demand = q("""
    SELECT * FROM market_demand
    WHERE snapshot_date = (SELECT max(snapshot_date) FROM market_demand)
""")
history = q("SELECT * FROM tech_demand_history WHERE sample_is_usable")
salary = q("""
    SELECT * FROM salary_by_tech
    WHERE snapshot_date = (SELECT max(snapshot_date) FROM salary_by_tech)
      AND sample_is_usable
""")
pairs = q("SELECT * FROM tech_cooccurrence WHERE tech_total >= 30 AND lift > 1")

st.title("📈 Signal Market Index")
st.caption(
    "What the US data and AI job market is hiring for, measured daily. "
    "Built from job-board postings, updated every morning."
)

if demand.empty:
    st.warning("No snapshot data yet. Run `python ingestion/market_snapshot.py`.")
    st.stop()

snapshot_day = demand["snapshot_date"].max()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Technologies tracked", len(demand))
c2.metric("Total openings indexed", f"{int(demand['openings'].sum()):,}")
c3.metric("Categories", demand["category"].nunique())
c4.metric("Snapshot date", str(snapshot_day))

st.divider()

# ---------------------------------------------------------------- demand ----
st.subheader("Demand by technology")
st.caption("Live market counts — how many current US openings mention each technology.")

categories = ["All"] + sorted(demand["category"].unique())
picked = st.selectbox("Category", categories)
view = demand if picked == "All" else demand[demand["category"] == picked]
view = view.sort_values("openings", ascending=False)

st.bar_chart(view.set_index("tech_name")["openings"], height=380)

# Head-to-head comparisons are the most legible way to show the market, and
# the warehouse race is the one practitioners actually argue about.
st.subheader("The warehouse race")
wh = demand[demand["category"] == "warehouse"].sort_values("openings", ascending=False)
if not wh.empty:
    top2 = wh.head(2)
    if len(top2) == 2 and top2.iloc[1]["openings"]:
        lead = top2.iloc[0]["openings"] / top2.iloc[1]["openings"]
        st.markdown(
            f"**{top2.iloc[0]['tech_name']}** leads **{top2.iloc[1]['tech_name']}** "
            f"by {lead:.2f}× — {int(top2.iloc[0]['openings']):,} vs "
            f"{int(top2.iloc[1]['openings']):,} openings."
        )
    st.dataframe(
        wh[["tech_name", "openings", "pct_of_category", "category_rank"]]
        .rename(columns={"tech_name": "Technology", "openings": "Openings",
                         "pct_of_category": "% of category", "category_rank": "Rank"}),
        hide_index=True, use_container_width=True,
    )

st.divider()

# ----------------------------------------------------------------- trend ----
st.subheader("Trend over time")
if history.empty:
    st.info("Not enough history yet — the daily snapshot needs a few more days.")
else:
    st.caption(
        "⚠️ **Sample, not the whole market.** Reconstructed from the posting dates of "
        "roles Signal collected, so it under-counts jobs that filled and disappeared. "
        "Months with too few postings are excluded. Direction is meaningful; "
        "absolute levels are not."
    )
    top_slugs = demand.nlargest(10, "openings")["tech_slug"].tolist()
    choices = st.multiselect(
        "Technologies", sorted(history["tech_slug"].unique()),
        default=[s for s in top_slugs if s in set(history["tech_slug"])][:5],
    )
    if choices:
        sub = history[history["tech_slug"].isin(choices)]
        pivot = sub.pivot_table(index="month", columns="tech_slug",
                                values="pct_of_postings", aggfunc="mean").sort_index()
        st.line_chart(pivot, height=340)
        st.caption("Share of collected postings mentioning each technology, by month.")

st.divider()

# ---------------------------------------------------------------- salary ----
st.subheader("What each skill pays")
if salary.empty:
    st.info("No salary distribution captured yet.")
else:
    top_bucket = int(salary["top_bucket"].max())
    st.caption(
        f"From the job board's market-wide salary histogram — independent of which "
        f"postings Signal collected. The histogram has only seven bands and the top "
        f"one (\\${top_bucket:,}+) is open-ended, so medians are uninformative: they "
        f"land on \\${top_bucket:,} for almost every technology. **Share of postings in "
        f"the top band** is the metric that actually separates them."
    )
    s = salary.sort_values("pct_top_band", ascending=False)
    st.dataframe(
        s[["tech_name", "category", "pct_top_band", "pct_under_80k", "total_postings"]]
        .rename(columns={"tech_name": "Technology", "category": "Category",
                         "pct_top_band": f"% at \\${top_bucket//1000}k+",
                         "pct_under_80k": "% under $80k",
                         "total_postings": "Sample"}),
        hide_index=True, use_container_width=True,
    )
    lead = s.head(3)["tech_name"].tolist()
    st.markdown(
        f"**Newer tooling pays best.** {', '.join(lead)} lead on the share of "
        f"postings in the top salary band."
    )

st.divider()

# ------------------------------------------------------------ the stack ----
st.subheader("Which tools travel together")
st.caption(
    "Computed from postings that name both tools. **Lift** is how much more often a "
    "pair appears together than chance would predict — lift of 100 means 100× more "
    "often. This is what reveals the actual stacks employers hire for."
)
if pairs.empty:
    st.info("Not enough co-occurrence data yet.")
else:
    anchor = st.selectbox(
        "Show tools that appear alongside:",
        sorted(pairs["tech_slug"].unique()),
        index=0,
    )
    rel = pairs[pairs["tech_slug"] == anchor].nlargest(12, "lift")
    st.dataframe(
        rel[["co_tech_slug", "co_postings", "pct_of_tech_postings", "lift"]]
        .rename(columns={"co_tech_slug": "Also mentions", "co_postings": "Postings",
                         "pct_of_tech_postings": "% of postings", "lift": "Lift"}),
        hide_index=True, use_container_width=True,
    )

st.divider()
st.caption(
    "Signal tracks the US data and AI job market daily. Demand counts come from the "
    "job board's market-wide index; trend lines come from a collected sample and are "
    "labelled as such. Built by Zohaib · github.com/ZohaibA365/Signal"
)
