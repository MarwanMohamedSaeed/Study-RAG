"""Ask the material: grounded Q&A with page citations."""
import streamlit as st

from core import llm, rag
from core.llm import LLMError
from views.ui import markdown as _markdown

ss = st.session_state
selected = ss.selected_docs


def _cite_line(answer: str, sources: list[dict]) -> str:
    cited = rag.CITE_RE.findall(answer)
    refs = list(dict.fromkeys(cited)) or list(dict.fromkeys(f"{s['filename']} {s['ref']}" for s in sources))
    return "📎 " + " · ".join(refs)


def _render_sources(sources: list[dict]):
    with st.expander(f"Sources ({len(sources)} retrieved chunks)"):
        for i, s in enumerate(sources, 1):
            st.markdown(f"**{i}. {s['filename']} — {s['ref']}**  ·  similarity {s['score']:.2f}")
            st.text(s["text"])


st.header("💬 Ask the material")
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
