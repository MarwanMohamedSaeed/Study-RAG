"""StudyRAG - Streamlit entry point.  Run:  streamlit run app.py

This file is the shell shared by every page: the sidebar (LLM status, upload, document
picker) and the page menu. Each page lives in views/ and reads the shared state below.
"""
from __future__ import annotations

import streamlit as st

from core import llm
from core.ingest import SUPPORTED_TYPES, delete_document, get_embedder, ingest_file, list_documents

st.set_page_config(page_title="StudyRAG", page_icon="📚", layout="wide")


@st.cache_resource(show_spinner="Loading embedding model (first run downloads ~470 MB)...")
def _warm_embedder():
    return get_embedder()


_warm_embedder()
ss = st.session_state
ss.setdefault("messages", [])        # chat history: {role, content, sources?}
ss.setdefault("ingested", set())     # (name, size) of uploads already processed this session
ss.setdefault("quiz", None)          # list[MCQ]
ss.setdefault("quiz_meta", {})       # difficulty / topic / docs of the current quiz
ss.setdefault("quiz_id", 0)
ss.setdefault("answers", {})         # question index -> chosen letter (survives page switches)
ss.setdefault("submitted", False)

pages = st.navigation([
    st.Page("views/ask.py", title="Ask the material", icon="💬", default=True),
    st.Page("views/quiz.py", title="MCQ quiz", icon="📝"),
    st.Page("views/study.py", title="Study tools", icon="📋"),
    st.Page("views/exam.py", title="Exam simulation", icon="⏱️"),
    st.Page("views/progress.py", title="Progress", icon="📈"),
])

with st.sidebar:
    st.title("📚 StudyRAG")
    llm_ok, llm_msg = llm.health()
    st.caption(f"LLM: **{llm.provider_label()}** {'🟢' if llm_ok else '🔴'}")
    if not llm_ok:
        st.error(llm_msg)

    uploads = st.file_uploader("Upload lectures (PDF, PowerPoint, Word)", type=SUPPORTED_TYPES,
                               accept_multiple_files=True)
    for up in uploads or []:
        key = (up.name, up.size)
        if key in ss.ingested:
            continue
        bar = st.progress(0.0, text=f"Processing {up.name}")
        try:
            res = ingest_file(up.getvalue(), up.name, progress=lambda f, m: bar.progress(min(f, 1.0), text=m))
        except Exception as e:  # corrupt or password-protected file: report it, keep the app running
            bar.empty()
            ss.ingested.add(key)
            st.error(f"Could not read {up.name}: {e}")
            continue
        bar.empty()
        ss.ingested.add(key)
        if res.warning:
            st.warning(res.warning)
        if res.n_chunks:
            note = "already indexed" if res.already_indexed else f"{res.n_chunks} chunks from {res.n_pages} {res.unit}s"
            st.success(f"{up.name}: {note}")

    docs = list_documents()
    st.subheader("Documents")
    if not docs:
        st.info("Upload a lecture (PDF, PowerPoint or Word) to get started.")
    labels = {d["doc_id"]: f"{d['filename']} ({d['n_pages']} {'p.' if d['unit'] == 'page' else d['unit'] + 's'})"
              for d in docs}
    # Keep only selections that still exist (a document may have been deleted).
    if "selected_docs" in ss:
        ss.selected_docs = [d for d in ss.selected_docs if d in labels]
    elif labels:
        ss.selected_docs = list(labels)  # new session: start with everything selected
    st.multiselect("Work on", options=list(labels), format_func=labels.get,
                   placeholder="Choose document(s)", key="selected_docs")
    if docs:
        with st.expander("Manage"):
            to_del = st.selectbox("Delete a document", [None] + list(labels),
                                  format_func=lambda d: "-" if d is None else labels[d])
            if to_del and st.button("Delete", type="secondary"):
                delete_document(to_del)
                st.rerun()

# Shared state for the pages
ss.doc_labels = labels
ss.doc_names = {d["doc_id"]: d["filename"] for d in docs}
ss.doc_units = {d["doc_id"]: d["unit"] for d in docs}
ss.doc_pages = {d["doc_id"]: d["n_pages"] for d in docs}
ss.max_pages = max((d["n_pages"] for d in docs if d["doc_id"] in ss.selected_docs), default=1)

pages.run()
