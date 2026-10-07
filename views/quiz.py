"""Quiz: four question types (or mixed), take it, get graded, save to progress, export."""
import time

import streamlit as st

from core import cards, store
from core.export import to_json, to_markdown, to_pdf
from core.grading import grade
from core.llm import LLMError
from core.mcq import generate_mixed, generate_quiz
from core.schemas import LETTERS, QUESTION_TYPES

ss = st.session_state
ss.setdefault("grades", {})          # question index -> Grade, after submitting
selected = ss.selected_docs
max_pages = ss.max_pages

TYPE_CHOICES = {**{v: [k] for k, v in QUESTION_TYPES.items()},
                "Mixed: multiple choice, true/false, fill-in": ["mcq", "tf", "fill"],
                "Mixed: all four types": ["mcq", "tf", "fill", "short"]}

st.header("📝 Quiz")
if not selected and not ss.quiz:
    st.info("Select at least one document in the sidebar.")
    st.stop()

if selected:
    with st.form("quiz_form"):
        c1, c2 = st.columns(2)
        n_q = c1.slider("Number of questions", 5, 30, 10)
        difficulty = c2.radio("Difficulty", ["easy", "medium", "hard"], index=1, horizontal=True)
        qtype = st.selectbox("Question type", list(TYPE_CHOICES),
                             help="Short answers are graded by the LLM against the lecture when you submit.")
        topic = st.text_input("Topic / keyword (optional)", placeholder="e.g. congestion control")
        use_range = st.checkbox("Limit to a page range", disabled=max_pages < 2)
        page_range = st.slider("Pages", 1, max(2, max_pages), (1, max(2, max_pages)), disabled=max_pages < 2)
        go = st.form_submit_button("Generate quiz", type="primary")

    if go:
        bar = st.progress(0.0, text="Selecting material…")
        t0 = time.time()
        kinds = TYPE_CHOICES[qtype]
        opts = dict(topic=topic or None, page_range=page_range if use_range else None,
                    progress=lambda f, m: bar.progress(min(f, 1.0), text=m))
        try:
            qs = (generate_quiz(selected, n_q, difficulty, kind=kinds[0], **opts) if len(kinds) == 1
                  else generate_mixed(selected, n_q, difficulty, kinds, **opts))
        except LLMError as e:
            st.error(str(e))
            qs = None
        bar.empty()
        if qs is not None:
            if not qs:
                st.warning("No questions could be generated - try another topic, type or page range.")
            else:
                if len(qs) < n_q:
                    st.warning(f"Only {len(qs)} valid, non-duplicate questions could be generated from this material.")
                st.toast(f"{len(qs)} questions in {time.time() - t0:.0f}s")
                ss.quiz = qs  # keep validated objects; re-validating on rerun breaks if rules change
                ss.quiz_meta = {"difficulty": difficulty, "topic": topic.strip() or None, "doc_ids": list(selected)}
                ss.quiz_id += 1
                ss.answers, ss.grades, ss.submitted = {}, {}, False


def _remember(i: int, key: str):
    # Widget values are dropped when you visit another page; keep answers in plain session state.
    ss.answers[i] = ss[key]


def _answer_widget(i: int, q, key: str, saved):
    common = dict(key=key, disabled=ss.submitted, on_change=_remember, args=(i, key), label_visibility="collapsed")
    if q.kind == "mcq":
        st.radio(f"q{i}", LETTERS, index=LETTERS.index(saved) if saved else None,
                 format_func=lambda L, q=q: f"{L}) {q.options[LETTERS.index(L)]}", **common)
    elif q.kind == "tf":
        st.radio(f"q{i}", ["True", "False"], index=["True", "False"].index(saved) if saved else None,
                 horizontal=True, **common)
    elif q.kind == "fill":
        st.text_input(f"q{i}", value=saved or "", placeholder="Type the missing word(s)", **common)
    else:
        st.text_area(f"q{i}", value=saved or "", placeholder="Answer in one to three sentences (English or Arabic)",
                     height=90, **common)


def _feedback(q, saved, g):
    src = f"_(source: {q.source_ref})_"
    if q.kind == "short":
        model_answer = f"\n\n**Model answer:** {q.reference}"
        missing = f"\n\n**Missing:** {'; '.join(g.missing)}" if g.missing else ""
        box = st.success if g.score == 1 else st.warning if g.score == 0.5 else st.error
        label = {1: "Full marks", 0.5: "Half credit", 0: "Not quite"}[g.score]
        box(f"**{label}.** {g.feedback}{missing}{model_answer}  {src}")
    elif g.score == 1:
        st.success(f"Correct - {q.explanation}  {src}")
    else:
        if q.kind == "mcq":
            shown = f"{q.correct}) {q.correct_text}"
        elif q.kind == "fill":
            shown = q.answer + (f" (also: {', '.join(q.alternatives)})" if q.alternatives else "")
        else:
            shown = q.correct_text
        yours = "no answer" if not saved else f"you {'chose' if q.kind in ('mcq', 'tf') else 'wrote'} {saved}"
        st.error(f"Answer: **{shown}** ({yours}). {q.explanation}  {src}")


if ss.quiz:
    quiz = ss.quiz
    if label := ss.quiz_meta.get("label"):
        st.info(label)
    st.divider()
    for i, q in enumerate(quiz):
        key = f"ans_{ss.quiz_id}_{i}"
        saved = ss.answers.get(i)
        prefix = {"tf": "True or false: ", "fill": "Fill in the blank: "}.get(q.kind, "")
        st.markdown(f"**{i + 1}. {prefix}{q.question}**")
        _answer_widget(i, q, key, saved)
        if ss.submitted and i in ss.grades:
            _feedback(q, saved, ss.grades[i])

    c1, c2, c3, _ = st.columns([1, 1, 2, 2])
    if not ss.submitted and c1.button("Submit answers", type="primary"):
        answers = [ss.answers.get(i) for i in range(len(quiz))]
        n_short = sum(q.kind == "short" for q in quiz)
        try:
            with st.spinner(f"Grading {n_short} short answer{'s' if n_short != 1 else ''}…" if n_short else "Grading…"):
                ss.grades = {i: grade(q, a) for i, (q, a) in enumerate(zip(quiz, answers))}
        except LLMError as e:
            st.error(str(e))
            st.stop()
        meta = ss.quiz_meta
        doc_ids = meta.get("doc_ids", list(selected))
        store.record_attempt(quiz, answers, doc_ids, [ss.doc_names.get(d, d) for d in doc_ids], meta.get("difficulty"),
                             meta.get("topic"), scores=[ss.grades[i].score for i in range(len(quiz))])
        ss.submitted = True
        st.rerun()
    if ss.submitted:
        score = sum(g.score for g in ss.grades.values())
        st.metric("Score", f"{score:g} / {len(quiz)}", f"{100 * score / len(quiz):.0f}%")
        st.caption("Saved to your progress. See the 📈 Progress page.")
        if c2.button("Retake"):
            ss.quiz_id += 1
            ss.answers, ss.grades, ss.submitted = {}, {}, False
            st.rerun()
        if any(g.score < 1 for g in ss.grades.values()) and c3.button("🃏 Add my mistakes to flashcards"):
            added = cards.from_mistakes(ss.quiz_meta.get("doc_ids") or None)
            st.toast(f"{added} new flashcard{'s' if added != 1 else ''} added" if added else "Already in your flashcards")

    st.subheader("Export")
    title = "StudyRAG quiz - " + ", ".join(ss.doc_names.get(d, d) for d in ss.quiz_meta.get("doc_ids", selected))
    e1, e2, e3 = st.columns(3)
    e1.download_button("JSON", to_json(quiz, title), "quiz.json", "application/json", use_container_width=True)
    e2.download_button("Markdown (with key)", to_markdown(quiz, title), "quiz.md", "text/markdown",
                       use_container_width=True)
    e3.download_button("Printable PDF (with key)", to_pdf(quiz, title), "quiz.pdf", "application/pdf",
                       use_container_width=True)
