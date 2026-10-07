"""Exam simulation: several documents, mixed difficulty, a countdown that auto-submits, and a report."""
import time

import pandas as pd
import streamlit as st

from core import exam, store
from core.llm import LLMError
from core.schemas import LETTERS
from views.ui import demo_cap, demo_guard, page_header, progress_bar

ss = st.session_state
ss.setdefault("exam", None)   # dict while an exam is prepared / running / finished
page_header("⏱️", "Exam simulation",
            "A timed mixed exam, graded at the end like the real thing.")


def _remember(i: int, key: str):
    ss.exam["answers"][i] = ss[key]


def _submit(auto: bool = False):
    ex = ss.exam
    if ex["submitted"]:
        return
    ex["used_s"] = min(ex["duration_s"], int(time.time() - ex["started_at"]))
    answers = [ex["answers"].get(i) for i in range(len(ex["questions"]))]
    store.record_attempt(ex["questions"], answers, ex["doc_ids"], [ss.doc_names.get(d, d) for d in ex["doc_ids"]],
                         difficulty="mixed", mode="exam", duration_s=ex["used_s"], difficulties=ex["levels"])
    ex["submitted"], ex["auto"] = True, auto


def _clock(seconds: float) -> str:
    m, s = divmod(max(0, int(seconds)), 60)
    return f"{m:02d}:{s:02d}"


ex = ss.exam

# ---------------------------------------------------------------- 1. setup
if ex is None:
    if not ss.doc_names:
        st.info("Upload at least one lecture first.")
        st.stop()
    st.caption("Questions are prepared first (this takes a few minutes); the timer starts only when you press "
               "**Start**. When time runs out, the exam is submitted automatically.")
    with st.form("exam_setup"):
        docs = st.multiselect("Lectures", list(ss.doc_names), default=ss.selected_docs or None,
                              format_func=ss.doc_labels.get)
        c1, c2 = st.columns(2)
        n = c1.slider("Questions", 5, max(10, demo_cap(40)), min(20, demo_cap(40)), step=5)
        minutes = c2.slider("Time limit (minutes)", 5, 90, 20, step=5)
        mix_name = st.radio("Difficulty mix", list(exam.MIXES))
        go = st.form_submit_button("Prepare exam", type="primary")
    if go:
        demo_guard(2 * -(-n // 5) + 2)
        if not docs:
            st.warning("Choose at least one lecture.")
            st.stop()
        bar, cb = progress_bar("Preparing questions…")
        try:
            qs, levels = exam.build_exam(docs, n, exam.MIXES[mix_name], progress=cb)
        except LLMError as e:
            bar.empty()
            st.error(str(e))
            st.stop()
        bar.empty()
        if not qs:
            st.warning("No questions could be generated from these lectures.")
            st.stop()
        ss.exam = {"id": int(time.time()), "questions": qs, "levels": levels, "doc_ids": docs,
                   "duration_s": minutes * 60, "mix": mix_name, "started_at": None, "answers": {},
                   "submitted": False}
        st.rerun()
    st.stop()

# ---------------------------------------------------------------- 2. ready / running
n_q = len(ex["questions"])
if ex["started_at"] is None:
    st.success(f"Exam ready: **{n_q} questions**, **{ex['duration_s'] // 60} minutes**, {ex['mix'].split(' (')[0]} mix.")
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("Start", type="primary"):
        ex["started_at"] = time.time()
        st.rerun()
    if c2.button("Cancel"):
        ss.exam = None
        st.rerun()
    st.stop()

# Time may have run out while you were on another page.
if not ex["submitted"] and time.time() - ex["started_at"] >= ex["duration_s"]:
    _submit(auto=True)

if not ex["submitted"]:
    @st.fragment(run_every=1)
    def timer():
        left = ex["duration_s"] - (time.time() - ex["started_at"])
        if left <= 0:
            _submit(auto=True)
            st.rerun(scope="app")
        answered = sum(v is not None for v in ex["answers"].values())
        st.progress(max(0.0, left / ex["duration_s"]),
                    text=f"⏱️ **{_clock(left)}** left · {answered}/{n_q} answered")

    timer()

# ---------------------------------------------------------------- 3. report (after submitting)
if ex["submitted"]:
    answers = [ex["answers"].get(i) for i in range(n_q)]
    rep = exam.report(ex["questions"], ex["levels"], answers)
    if ex.get("auto"):
        st.warning("Time is up: your exam was submitted automatically.")
    m1, m2, m3 = st.columns(3)
    m1.metric("Score", f"{rep['score']} / {rep['total']}", f"{100 * rep['score'] / rep['total']:.0f}%")
    m2.metric("Time used", _clock(ex["used_s"]), f"of {_clock(ex['duration_s'])}", delta_color="off")
    m3.metric("Unanswered", rep["unanswered"])
    st.dataframe(pd.DataFrame([{"Difficulty": r["level"].title(), "Correct": f"{r['correct']} / {r['total']}",
                                "%": round(100 * r["correct"] / r["total"])} for r in rep["by_level"]]),
                 hide_index=True, column_config={"%": st.column_config.ProgressColumn(
                     "%", min_value=0, max_value=100, format="%d%%")})
    if rep["weak_pages"]:
        st.markdown("**Review these:** " + " · ".join(
            f"{p['file']} {p['ref']} ({p['correct']}/{p['total']})" for p in rep["weak_pages"][:6]))
    st.caption("Saved to your progress (📈 Progress page).")
    if st.button("New exam", type="primary"):
        ss.exam = None
        st.rerun()
    st.divider()

for i, (q, level) in enumerate(zip(ex["questions"], ex["levels"])):
    key = f"exam_{ex['id']}_{i}"
    saved = ex["answers"].get(i)
    # The source would be a hint, so it is only shown in the review after submitting.
    hint = f"  \n:gray[{level} · {q.source_file} {q.source_ref}]" if ex["submitted"] else f"  \n:gray[{level}]"
    st.markdown(f"**{i + 1}. {q.question}**{hint}")
    st.radio(f"q{i}", LETTERS, index=LETTERS.index(saved) if saved else None, label_visibility="collapsed",
             format_func=lambda L, q=q: f"{L}) {q.options[LETTERS.index(L)]}", key=key,
             disabled=ex["submitted"], on_change=_remember, args=(i, key))
    if ex["submitted"]:
        if saved == q.correct:
            st.success(f"Correct - {q.explanation}")
        else:
            st.error(f"Answer: **{q.correct}) {q.correct_text}** "
                     f"({'no answer' if saved is None else 'you chose ' + saved}). {q.explanation}")

if not ex["submitted"] and st.button("Submit exam", type="primary"):
    _submit()
    st.rerun()
