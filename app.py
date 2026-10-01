"""StudyRAG - Streamlit entry point.  Run:  streamlit run app.py"""
from __future__ import annotations

import re
import time

import streamlit as st

from core import llm, rag
from core.export import to_json, to_markdown, to_pdf
from core.ingest import delete_document, get_embedder, ingest_pdf, list_documents
from core.llm import LLMError
from core.mcq import generate_quiz
from core.schemas import LETTERS, MCQ

st.set_page_config(page_title="StudyRAG", page_icon="📚", layout="wide")


@st.cache_resource(show_spinner="Loading embedding model (first run downloads ~470 MB)...")
def _warm_embedder():
    return get_embedder()


_warm_embedder()
ss = st.session_state
ss.setdefault("messages", [])        # chat history: {role, content, sources?}
ss.setdefault("ingested", set())     # (name, size) of uploads already processed this session
ss.setdefault("quiz", None)          # list[MCQ]
ss.setdefault("quiz_id", 0)
ss.setdefault("submitted", False)


# ============================================================================ sidebar
with st.sidebar:
    st.title("📚 StudyRAG")
    llm_ok, llm_msg = llm.health()
    st.caption(f"LLM: **{llm.provider_label()}** {'🟢' if llm_ok else '🔴'}")
    if not llm_ok:
        st.error(llm_msg)

    uploads = st.file_uploader("Upload lecture PDFs", type="pdf", accept_multiple_files=True)
    for up in uploads or []:
        key = (up.name, up.size)
        if key in ss.ingested:
            continue
        bar = st.progress(0.0, text=f"Processing {up.name}")
        res = ingest_pdf(up.getvalue(), up.name, progress=lambda f, m: bar.progress(min(f, 1.0), text=m))
        bar.empty()
        ss.ingested.add(key)
        if res.warning:
            st.warning(res.warning)
        if res.n_chunks:
            note = "already indexed" if res.already_indexed else f"{res.n_chunks} chunks from {res.n_pages} pages"
            st.success(f"{up.name}: {note}")

    docs = list_documents()
    st.subheader("Documents")
    if not docs:
        st.info("Upload a PDF to get started.")
    labels = {d["doc_id"]: f"{d['filename']} ({d['n_pages']} p.)" for d in docs}
    selected = st.multiselect("Work on", options=list(labels), default=list(labels)[:1] if len(labels) == 1 else None,
                              format_func=labels.get, placeholder="Choose document(s)")
    if docs:
        with st.expander("Manage"):
            to_del = st.selectbox("Delete a document", [None] + list(labels),
                                  format_func=lambda d: "-" if d is None else labels[d])
            if to_del and st.button("Delete", type="secondary"):
                delete_document(to_del)
                st.rerun()

max_pages = max((d["n_pages"] for d in docs if d["doc_id"] in selected), default=1)
tab_chat, tab_quiz = st.tabs(["💬 Ask the material", "📝 MCQ quiz"])


# ============================================================================ Q&A tab
def _cite_line(answer: str, sources: list[dict]) -> str:
    cited = re.findall(r"\[([^\[\]]+? p\.\d+)\]", answer)
    refs = list(dict.fromkeys(cited)) or list(dict.fromkeys(f"{s['filename']} p.{s['page']}" for s in sources))
    return "📎 " + " · ".join(refs)


def _markdown(text: str, target=st):
    """Arabic answers read right-to-left."""
    if rag.detect_language(text) == "ar":
        # keep "[file.pdf p.5]" as an isolated LTR run so bidi doesn't flip its brackets
        text = re.sub(r"(\[[^\[\]]+? p\.\d+\])", r'<bdi dir="ltr">\1</bdi>', text)
        target.markdown(f'<div dir="rtl" style="text-align: right">\n\n{text}\n\n</div>', unsafe_allow_html=True)
    else:
        target.markdown(text)


def _render_sources(sources: list[dict]):
    with st.expander(f"Sources ({len(sources)} retrieved chunks)"):
        for i, s in enumerate(sources, 1):
            st.markdown(f"**{i}. {s['filename']} — page {s['page']}**  ·  similarity {s['score']:.2f}")
            st.text(s["text"])


with tab_chat:
    top = st.columns([6, 1])
    top[0].caption("Answers come only from the selected documents, with page citations.")
    if top[1].button("Clear chat", use_container_width=True):
        ss.messages = []
        st.rerun()

    # Messages go in a container created *before* the input, so new turns render above the input box.
    convo = st.container()
    for m in ss.messages:
        with convo.chat_message(m["role"]):
            _markdown(m["content"])
            if m.get("sources"):
                st.caption(_cite_line(m["content"], m["sources"]))
                _render_sources(m["sources"])

    question = st.chat_input("Ask a question about your lectures (English or Arabic)…", disabled=not selected)
    if not selected:
        st.info("Select at least one document in the sidebar.")
    if question:
        history = [{"role": m["role"], "content": m["content"]} for m in ss.messages]
        with convo.chat_message("user"):
            _markdown(question)
        with convo.chat_message("assistant"):
            req = rag.prepare(question, selected, history)
            slot = st.empty()
            try:
                if req.sources:
                    with slot:
                        answer = llm.strip_think(st.write_stream(llm.stream(req.system, req.user)))
                else:
                    answer = req.not_found_text
                _markdown(answer, slot)  # re-render the final text (RTL for Arabic)
            except LLMError as e:
                st.error(str(e))
                answer = None
            sources = [] if (answer is None or rag.is_not_found(answer)) else req.sources
            if sources:
                st.caption(_cite_line(answer, sources))
                _render_sources(sources)
        ss.messages.append({"role": "user", "content": question})
        if answer:
            ss.messages.append({"role": "assistant", "content": answer, "sources": sources})


# ============================================================================ Quiz tab
with tab_quiz:
    if not selected:
        st.info("Select at least one document in the sidebar.")
    else:
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
                qs = generate_quiz(selected, n_q, difficulty, topic or None,
                                   page_range if use_range else None,
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
                    ss.quiz_id += 1
                    ss.submitted = False

        if ss.quiz:
            quiz: list[MCQ] = ss.quiz
            st.divider()
            answers = {}
            for i, q in enumerate(quiz):
                st.markdown(f"**{i + 1}. {q.question}**")
                answers[i] = st.radio(f"q{i}", options=LETTERS, index=None, label_visibility="collapsed",
                                      format_func=lambda L, q=q: f"{L}) {q.options[LETTERS.index(L)]}",
                                      key=f"ans_{ss.quiz_id}_{i}", disabled=ss.submitted)
                if ss.submitted:
                    if answers[i] == q.correct:
                        st.success(f"Correct - {q.explanation}  _(source: page {q.source_page})_")
                    else:
                        picked = "no answer" if answers[i] is None else f"you chose {answers[i]}"
                        st.error(f"Answer: **{q.correct}) {q.correct_text}** ({picked}). {q.explanation}  "
                                 f"_(source: page {q.source_page})_")
            c1, c2, _ = st.columns([1, 1, 4])
            if not ss.submitted and c1.button("Submit answers", type="primary"):
                ss.submitted = True
                st.rerun()
            if ss.submitted:
                score = sum(answers[i] == q.correct for i, q in enumerate(quiz))
                st.metric("Score", f"{score} / {len(quiz)}", f"{100 * score / len(quiz):.0f}%")
                if c2.button("Retake"):
                    ss.quiz_id += 1
                    ss.submitted = False
                    st.rerun()

            st.subheader("Export")
            title = "StudyRAG quiz - " + ", ".join(labels[d].rsplit(" (", 1)[0] for d in selected)
            e1, e2, e3 = st.columns(3)
            e1.download_button("JSON", to_json(quiz, title), "quiz.json", "application/json", use_container_width=True)
            e2.download_button("Markdown (with key)", to_markdown(quiz, title), "quiz.md", "text/markdown",
                               use_container_width=True)
            e3.download_button("Printable PDF (with key)", to_pdf(quiz, title), "quiz.pdf", "application/pdf",
                               use_container_width=True)
