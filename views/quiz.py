"""MCQ quiz: generate, take, score, save to progress, export."""
import time

import streamlit as st

from core import store
from core.export import to_json, to_markdown, to_pdf
from core.llm import LLMError
from core.mcq import generate_quiz
from core.schemas import LETTERS, MCQ

ss = st.session_state
selected = ss.selected_docs
max_pages = ss.max_pages

st.header("📝 MCQ quiz")
if not selected:
    st.info("Select at least one document in the sidebar.")
    st.stop()

with st.form("quiz_form"):
    c1, c2 = st.columns(2)
    n_q = c1.slider("Number of questions", 5, 30, 10)
    difficulty = c2.radio("Difficulty", ["easy", "medium", "hard"], index=1, horizontal=True)
    topic = st.text_input("Topic / keyword (optional)", placeholder="e.g. congestion control")
    use_range = st.checkbox("Limit to a page range", disabled=max_pages < 2)
    page_range = st.slider("Pages", 1, max(2, max_pages), (1, max(2, max_pages)), disabled=max_pages < 2)
    go = st.form_submit_button("Generate quiz", type="primary")

if go:
    bar = st.progress(0.0, text="Selecting material…")
    t0 = time.time()
    try:
        qs = generate_quiz(selected, n_q, difficulty, topic or None, page_range if use_range else None,
                           progress=lambda f, m: bar.progress(min(f, 1.0), text=m))
    except LLMError as e:
        st.error(str(e))
        qs = None
    bar.empty()
    if qs is not None:
        if not qs:
            st.warning("No questions could be generated - try another topic or page range.")
        else:
            if len(qs) < n_q:
                st.warning(f"Only {len(qs)} valid, non-duplicate questions could be generated from this material.")
            st.toast(f"{len(qs)} questions in {time.time() - t0:.0f}s")
            ss.quiz = qs  # keep validated objects; re-validating on rerun breaks if rules change
            ss.quiz_meta = {"difficulty": difficulty, "topic": topic.strip() or None, "doc_ids": list(selected)}
            ss.quiz_id += 1
            ss.answers = {}
            ss.submitted = False


def _remember(i: int, key: str):
    # Widget values are dropped when you visit another page; keep answers in plain session state.
    ss.answers[i] = ss[key]


if ss.quiz:
    quiz: list[MCQ] = ss.quiz
    st.divider()
    for i, q in enumerate(quiz):
        key = f"ans_{ss.quiz_id}_{i}"
        saved = ss.answers.get(i)
        st.markdown(f"**{i + 1}. {q.question}**")
        st.radio(f"q{i}", options=LETTERS, index=LETTERS.index(saved) if saved else None,
                 label_visibility="collapsed", format_func=lambda L, q=q: f"{L}) {q.options[LETTERS.index(L)]}",
                 key=key, disabled=ss.submitted, on_change=_remember, args=(i, key))
        if ss.submitted:
            if saved == q.correct:
                st.success(f"Correct - {q.explanation}  _(source: page {q.source_page})_")
            else:
                picked = "no answer" if saved is None else f"you chose {saved}"
                st.error(f"Answer: **{q.correct}) {q.correct_text}** ({picked}). {q.explanation}  "
                         f"_(source: page {q.source_page})_")

    c1, c2, _ = st.columns([1, 1, 4])
    if not ss.submitted and c1.button("Submit answers", type="primary"):
        meta = ss.quiz_meta
        doc_ids = meta.get("doc_ids", list(selected))
        store.record_attempt(quiz, [ss.answers.get(i) for i in range(len(quiz))], doc_ids,
                             [ss.doc_names.get(d, d) for d in doc_ids], meta.get("difficulty"), meta.get("topic"))
        ss.submitted = True
        st.rerun()
    if ss.submitted:
        score = sum(ss.answers.get(i) == q.correct for i, q in enumerate(quiz))
        st.metric("Score", f"{score} / {len(quiz)}", f"{100 * score / len(quiz):.0f}%")
        st.caption("Saved to your progress. See the 📈 Progress page.")
        if c2.button("Retake"):
            ss.quiz_id += 1
            ss.answers = {}
            ss.submitted = False
            st.rerun()

    st.subheader("Export")
    title = "StudyRAG quiz - " + ", ".join(ss.doc_names.get(d, d) for d in ss.quiz_meta.get("doc_ids", selected))
    e1, e2, e3 = st.columns(3)
    e1.download_button("JSON", to_json(quiz, title), "quiz.json", "application/json", use_container_width=True)
    e2.download_button("Markdown (with key)", to_markdown(quiz, title), "quiz.md", "text/markdown",
                       use_container_width=True)
    e3.download_button("Printable PDF (with key)", to_pdf(quiz, title), "quiz.pdf", "application/pdf",
                       use_container_width=True)
