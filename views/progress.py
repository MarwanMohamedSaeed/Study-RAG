"""Progress: quiz history and accuracy per page (the base for weak-spot tracking)."""
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from core import store

REVIEW_BELOW = 0.6  # pages under 60% correct are flagged for review
ss = st.session_state
st.header("📈 Progress")

totals = store.totals()
if not totals["answered"]:
    st.info("No quizzes yet. Take a quiz on the 📝 MCQ quiz page and submit it; your results will appear here.")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("Quizzes taken", totals["quizzes"])
m2.metric("Questions answered", totals["answered"])
m3.metric("Overall accuracy", f"{100 * totals['accuracy']:.0f}%")

# ---------------------------------------------------------------- accuracy per page
st.subheader("Accuracy by page")
only_selected = st.toggle("Only the documents selected in the sidebar", value=bool(ss.selected_docs))
stats = store.page_stats(ss.selected_docs if only_selected and ss.selected_docs else None)
if not stats:
    st.caption("No answered questions for these documents yet.")
else:
    df = pd.DataFrame(stats)
    multi_doc = df["doc_id"].nunique() > 1
    df["label"] = df.apply(lambda r: (f"{Path(r['filename'] or '?').stem[:18]} " if multi_doc else "") +
                           f"p.{r['page']}", axis=1)
    df["pct"] = (100 * df["accuracy"]).round(0)
    df["status"] = df["accuracy"].map(lambda a: "Review" if a < REVIEW_BELOW else "OK")
    df["score"] = df.apply(lambda r: f"{r['correct']}/{r['answered']}", axis=1)
    order = df.sort_values(["filename", "page"])["label"].tolist()
    base = alt.Chart(df).encode(
        y=alt.Y("label:N", sort=order, title=None),
        x=alt.X("pct:Q", scale=alt.Scale(domain=[0, 100]), title="Correct answers (%)"),
        tooltip=[alt.Tooltip("label:N", title="Page"), alt.Tooltip("score:N", title="Correct"),
                 alt.Tooltip("pct:Q", title="%")])
    bars = base.mark_bar(cornerRadiusEnd=3).encode(
        color=alt.Color("status:N", scale=alt.Scale(domain=["Review", "OK"], range=["#E4572E", "#2E9E6B"]),
                        legend=alt.Legend(title=None, orient="top")))
    # The score is printed next to each bar, so a 0% page (no visible bar) still shows "0/2".
    text = base.mark_text(align="left", dx=4, color="#8A93A6").encode(text="score:N")  # readable on light + dark
    st.altair_chart((bars + text).properties(height=alt.Step(30)), use_container_width=True)
    weak = df[(df["accuracy"] < REVIEW_BELOW)].head(5)
    if len(weak):
        st.markdown(f"**Pages to review** (below {REVIEW_BELOW:.0%} correct):  " +
                    " · ".join(f"{r.label} ({r.correct}/{r.answered})" for r in weak.itertuples()))
    else:
        st.success("No weak pages: every page is at 60% or better.")

# ---------------------------------------------------------------- history
st.subheader("Quiz history")
attempts = store.list_attempts()
hist = pd.DataFrame([{
    "Date": datetime.fromisoformat(a["created_at"]).astimezone().strftime("%Y-%m-%d %H:%M"),
    "Documents": ", ".join(a["doc_names"]),
    "Difficulty": a["difficulty"] or "",
    "Topic": a["topic"] or "",
    "Score": f"{a['score']} / {a['n_questions']}",
    "%": round(100 * a["score"] / a["n_questions"]),
} for a in attempts])
st.dataframe(hist, hide_index=True, use_container_width=True,
             column_config={"%": st.column_config.ProgressColumn("%", min_value=0, max_value=100, format="%d%%")})

with st.expander("Review a past quiz"):
    pick = st.selectbox("Quiz", attempts, format_func=lambda a: f"#{a['id']} · "
                        f"{datetime.fromisoformat(a['created_at']).astimezone():%Y-%m-%d %H:%M} · "
                        f"{a['score']}/{a['n_questions']}")
    for i, r in enumerate(store.attempt_results(pick["id"]), 1):
        mark = "✅" if r["is_correct"] else "❌"
        chosen = r["chosen"] or "no answer"
        st.markdown(f"{mark} **{i}. {r['question']}**  \nYour answer: {chosen} · correct: {r['correct']} · "
                    f"{r['filename'] or ''} p.{r['source_page']}")
