"""StudyRAG - Streamlit entry point.  Run:  streamlit run app.py

This file is the shell shared by every page: the sidebar (LLM status, upload, document
picker) and the page menu. Each page lives in views/ and reads the shared state below.
"""
from __future__ import annotations

import tempfile
import time
import uuid
from pathlib import Path

import streamlit as st

from core import config, llm, retriever, store
from core.ingest import (SUPPORTED_TYPES, delete_document, delete_expired_uploads, get_embedder, ingest_file,
                         list_documents, reembed_outdated, visible_documents)
from views.ui import demo_banner, inject_css, label, sidebar_brand

ASSETS = Path(__file__).resolve().parent / "assets"
st.set_page_config(page_title="StudyRAG", page_icon=str(ASSETS / "logo_icon.svg"), layout="wide")
st.logo(str(ASSETS / "logo.svg"), size="large", icon_image=str(ASSETS / "logo_icon.svg"))
inject_css()


@st.cache_resource(show_spinner="Loading the embedding model (the first run downloads it)…")
def _warm_embedder():
    return get_embedder()


@st.cache_resource(show_spinner="Preparing the sample lectures…")
def _preload_samples():
    """Demo mode: index the bundled sample lectures once per server."""
    from samples.make_benchmark_corpus import corpus
    if not any(not d["owner"] for d in list_documents()):   # visitors' uploads don't count
        for path in corpus():
            ingest_file(path.read_bytes(), path.name)
    return True


@st.cache_resource(show_spinner="Updating the search index for the current embedding model…")
def _reembed_outdated():
    """Once per server start: documents indexed with another model/backend are re-embedded."""
    return reembed_outdated()


@st.cache_data(ttl=3600, show_spinner=False)
def _cleanup_uploads(_hour_bucket: int) -> int:
    """Demo: delete visitors' uploads older than DEMO_UPLOAD_HOURS (at most once an hour)."""
    return delete_expired_uploads(config.DEMO_UPLOAD_HOURS)


_warm_embedder()
_reembed_outdated()
ss = st.session_state
if config.DEMO_MODE:
    _preload_samples()
    # every visitor gets a private progress database (quiz history, flashcards) for this browser session
    ss.setdefault("sid", uuid.uuid4().hex)
    demo_dir = Path(tempfile.gettempdir()) / "studyrag-demo"
    demo_dir.mkdir(exist_ok=True)
    store.use_db(str(demo_dir / f"{ss.sid}.db"))
    ss.setdefault("demo_used", 0)
    _cleanup_uploads(int(time.time() // 3600))
ss.setdefault("messages", [])        # chat history: {role, content, sources?}
ss.setdefault("ingested", set())     # (name, size) of uploads already processed this session
ss.setdefault("quiz", None)          # list[MCQ]
ss.setdefault("quiz_meta", {})       # difficulty / topic / docs of the current quiz
ss.setdefault("quiz_id", 0)
ss.setdefault("answers", {})         # question index -> chosen letter (survives page switches)
ss.setdefault("submitted", False)

pages = st.navigation([
    st.Page("views/ask.py", title="Ask the material", icon=":material/forum:", default=True),
    st.Page("views/quiz.py", title="Quiz", icon=":material/quiz:"),
    st.Page("views/study.py", title="Study tools", icon=":material/menu_book:"),
    st.Page("views/flashcards.py", title="Flashcards", icon=":material/style:"),
    st.Page("views/exam.py", title="Exam simulation", icon=":material/timer:"),
    st.Page("views/progress.py", title="Progress", icon=":material/insights:"),
])

with st.sidebar:
    llm_ok, llm_msg = llm.health()
    sidebar_brand(llm.provider_label(), llm_ok, config.RETRIEVAL_MODE)
    if not llm_ok:
        st.error(llm_msg)
    if retriever.reranker_error:
        st.warning("The re-ranker could not be loaded, so search is using hybrid mode without it. "
                   f"({retriever.reranker_error})")

    if config.DEMO_MODE:
        ss.demo_slot = st.empty()   # filled by demo_banner(); refreshed whenever an action is spent
        demo_banner()
        mine = [d for d in list_documents() if d["owner"] == ss.sid]
        uploads = []
        if config.DEMO_UPLOADS > 0:
            label("Your lectures")
            uploads = st.file_uploader(
                f"Try your own lecture (up to {config.DEMO_UPLOADS} files, {config.DEMO_UPLOAD_MB} MB each)",
                type=SUPPORTED_TYPES, accept_multiple_files=True, max_upload_size=config.DEMO_UPLOAD_MB,
                help=f"Your files are visible only to you, in this browser session, and are deleted automatically "
                     f"after {config.DEMO_UPLOAD_HOURS:g} hours. Don't upload confidential material: its text is sent "
                     f"to a hosted AI model to answer your questions.")
            st.caption(f"🔒 Private to your browser session · deleted after {config.DEMO_UPLOAD_HOURS:g} h")
            ss.setdefault("upload_skipped", set())
            new = [u for u in uploads or [] if (u.name, u.size) not in ss.ingested | ss.upload_skipped]
            too_big = [u for u in new if u.size > config.DEMO_UPLOAD_MB * 1024 * 1024]
            if too_big:
                st.warning(f"Over {config.DEMO_UPLOAD_MB} MB, skipped in the demo: "
                           + ", ".join(u.name for u in too_big))
            fits = [u for u in new if u not in too_big]
            room = max(0, config.DEMO_UPLOADS - len(mine))
            if len(fits) > room:
                st.warning(f"The demo allows {config.DEMO_UPLOADS} uploads per visitor"
                           + (f"; only the first {room} were added." if room else ". Delete one under Manage first."))
            uploads = fits[:room]
            # skipped files are remembered so the warning isn't repeated on every rerun (retried after a delete)
            ss.upload_skipped |= {(u.name, u.size) for u in new if u not in uploads}
    else:
        label("Your lectures")
        uploads = st.file_uploader("Upload lectures (PDF, PowerPoint, Word)", type=SUPPORTED_TYPES,
                                   accept_multiple_files=True)
    for up in uploads or []:
        key = (up.name, up.size)
        if key in ss.ingested:
            continue
        bar = st.progress(0.0, text=f"Processing {up.name}")
        try:
            res = ingest_file(up.getvalue(), up.name, progress=lambda f, m: bar.progress(min(f, 1.0), text=m),
                              owner=ss.sid if config.DEMO_MODE else None)
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
            if "selected_docs" in ss and res.doc_id not in ss.selected_docs:
                ss.selected_docs = ss.selected_docs + [res.doc_id]   # work on what you just uploaded
            note = "already indexed" if res.already_indexed else f"{res.n_chunks} chunks from {res.n_pages} {res.unit}s"
            st.success(f"{up.name}: {note}")

    docs = list_documents()
    if config.DEMO_MODE:   # shared sample lectures + this visitor's own uploads only
        docs = visible_documents(docs, ss.sid)
    label("Documents")
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
    deletable = [d["doc_id"] for d in docs if not config.DEMO_MODE or d["owner"] == ss.get("sid")]
    if deletable:
        with st.expander("Manage", icon=":material/settings:"):
            to_del = st.selectbox("Delete a document", [None] + deletable,
                                  format_func=lambda d: "-" if d is None else labels[d])
            if to_del and st.button("Delete", type="secondary"):
                delete_document(to_del)
                gone = next(d["filename"] for d in docs if d["doc_id"] == to_del)
                ss.ingested = {k for k in ss.ingested if k[0] != gone}   # so the same file can be re-uploaded
                ss.pop("upload_skipped", None)
                st.rerun()

# Shared state for the pages
ss.doc_labels = labels
ss.doc_names = {d["doc_id"]: d["filename"] for d in docs}
ss.doc_units = {d["doc_id"]: d["unit"] for d in docs}
ss.doc_pages = {d["doc_id"]: d["n_pages"] for d in docs}
ss.max_pages = max((d["n_pages"] for d in docs if d["doc_id"] in ss.selected_docs), default=1)

pages.run()
