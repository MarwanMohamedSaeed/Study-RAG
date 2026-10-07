"""Progress: quiz history and accuracy per page (the base for weak-spot tracking)."""
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import random

from core import store
from core.ingest import fmt_ref
from core.llm import LLMError
from core.mcq import generate_mixed, shuffle_options
from core.schemas import MCQ
from views.ui import demo_cap, demo_guard, progress_bar

REVIEW_BELOW = 0.6  # pages under 60% correct are flagged for review
ss = st.session_state
st.header("📈 Progress")

totals = store.totals()
if not totals["answered"]:
    st.info("No quizzes yet. Take a quiz on the 📝 Quiz page and submit it; your results will appear here.")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("Quizzes taken", totals["quizzes"])
m2.metric("Questions answered", totals["answered"])
m3.metric("Overall accuracy", f"{100 * totals['accuracy']:.0f}%")


def _start_practice(questions: list, label: str, doc_ids: list[str]):
    """Load a practice set into the quiz page and go there."""
    ss.quiz = questions
    ss.quiz_meta = {"difficulty": "practice", "topic": None, "doc_ids": doc_ids, "label": label}
    ss.quiz_id += 1
    ss.answers, ss.grades, ss.submitted = {}, {}, False
    st.switch_page("views/quiz.py")


# ---------------------------------------------------------------- practice weak spots
scope = ss.selected_docs or None
wrong = store.mistakes(scope)
weak = [s for s in store.page_stats(scope) if s["accuracy"] < REVIEW_BELOW]
with st.container(border=True):
    st.subheader("🎯 Practice your weak spots")
    p1, p2 = st.columns(2)
    with p1:
        st.markdown(f"**{len(wrong)} question{'s' if len(wrong) != 1 else ''}** you last answered wrong.  \n"
                    ":gray[Answer one correctly and it leaves this list.]")
        if st.button("Retry my wrong answers", type="primary", disabled=not wrong, use_container_width=True):
            rng = random.Random()
            retry = [shuffle_options(q, rng) if isinstance(q, MCQ) else q for q in wrong[:20]]
            rng.shuffle(retry)
            _start_practice(retry, f"🎯 Retrying {len(retry)} question{'s' if len(retry) != 1 else ''} "
                                   "you got wrong (options reshuffled).", sorted({q.source_doc for q in retry if q.source_doc}))
    with p2:
        pages_txt = ", ".join(f"{fmt_ref(s['unit'] or 'page', s['page'])}" for s in weak[:6]) or "none"
        st.markdown(f"**{len(weak)} weak page{'s' if len(weak) != 1 else ''}** (below {REVIEW_BELOW:.0%}): {pages_txt}  \n"
                    ":gray[Fresh questions from those pages only.]")
        n_new = st.slider("Questions", 5, max(5, demo_cap(20)), min(8, demo_cap(20)), key="weak_n",
                          disabled=not weak, label_visibility="collapsed")
        if st.button(f"New questions on weak pages", disabled=not weak, use_container_width=True):
            demo_guard(2 * -(-n_new // 5) + 2)
            focus = {(s["doc_id"], s["page"]) for s in weak}
            docs = sorted({d for d, _ in focus if d in ss.doc_names})
            bar, cb = progress_bar("Writing questions on your weak pages…")
            try:
                qs = generate_mixed(docs, n_new, "medium", ["mcq", "tf", "fill"], progress=cb, focus=focus) if docs else []
            except LLMError as e:
                qs = None
                st.error(str(e))
            bar.empty()
            if qs:
                _start_practice(qs, f"🎯 {len(qs)} new questions on your weak pages: {pages_txt}.", docs)
            elif qs is not None:
                st.warning("Could not write questions for these pages (their documents may have been deleted).")

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
                           fmt_ref(r["unit"] or "page", r["page"]), axis=1)
    df["pct"] = (100 * df["accuracy"]).round(0)
    df["status"] = df["accuracy"].map(lambda a: "Review" if a < REVIEW_BELOW else "OK")
    df["score"] = df.apply(lambda r: f"{r['correct']}/{r['answered']}", axis=1)
    order = df.sort_values(["filename", "page"])["label"].tolist()
    base = alt.Chart(df).encode(
        y=alt.Y("label:N", sort=order, title=None, axis=alt.Axis(labelLimit=260)),
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
    "Type": "⏱️ Exam" if a.get("mode") == "exam" else "📝 Quiz",
    "Time": f"{a['duration_s'] // 60}:{a['duration_s'] % 60:02d}" if a.get("duration_s") else "",
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
                    f"{r['filename'] or ''} {fmt_ref(r['source_unit'] or 'page', r['source_page'])}")
