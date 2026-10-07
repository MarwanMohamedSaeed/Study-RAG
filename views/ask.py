"""Ask the material: grounded Q&A with page citations."""
import streamlit as st

from core import citations, llm, rag
from core.llm import LLMError
from views.ui import demo_guard, page_header
from views.ui import markdown as _markdown

ss = st.session_state
selected = ss.selected_docs
AVATAR = {"user": "🎓", "assistant": "📚"}
# One-click starters for an empty chat; they work on any lecture, and one shows off Arabic.
SUGGESTIONS = [
    "Summarize the main ideas of this lecture",
    "What are the key definitions I should memorize?",
    "ما هي أهم المفاهيم في هذه المحاضرة؟",
    "Explain the hardest concept with a simple example",
]


def _cite_line(answer: str, sources: list[dict]) -> str:
    cited = rag.CITE_RE.findall(answer)
    refs = list(dict.fromkeys(cited)) or list(dict.fromkeys(f"{s['filename']} {s['ref']}" for s in sources))
    return "📎 " + " · ".join(refs)


def _fix_line(fixes: list[tuple[str, str]]) -> str:
    return "🔧 Citation check fixed " + " · ".join(f"{old} → {new}" for old, new in fixes)


def _render_sources(sources: list[dict]):
    with st.expander(f"Sources ({len(sources)} retrieved chunks)"):
        for i, s in enumerate(sources, 1):
            st.markdown(f"**{i}. {s['filename']} — {s['ref']}**  ·  similarity {s['score']:.2f}")
            st.text(s["text"])


top = st.columns([6, 1], vertical_alignment="center")
with top[0]:
    page_header("💬", "Ask the material",
                "Answers come only from your selected documents, with page citations. English or Arabic.")
if ss.messages and top[1].button("Clear chat", icon=":material/delete_sweep:", use_container_width=True):
    ss.messages = []
    st.rerun()

# Messages go in a container created *before* the input, so new turns render above the input box.
convo = st.container()
typed = st.chat_input("Ask a question about your lectures (English or Arabic)…", disabled=not selected)
# A clicked suggestion arrives as the pills' state on the next run; the welcome screen is gone by then.
question = typed or ss.pop("suggest", None)
if not ss.messages and selected and not question:
    convo.html('<div class="srag-welcome"><div class="srag-welcome-icon">✨</div><h3>What do you want to understand?</h3>'
               '<p>Ask anything about your lectures. Every answer points to the exact page it came from, '
               'so you can check it.</p></div>')
    convo.pills("Try one of these", SUGGESTIONS, label_visibility="collapsed", key="suggest")
for m in ss.messages:
    with convo.chat_message(m["role"], avatar=AVATAR[m["role"]]):
        _markdown(m["content"])
        if m.get("sources"):
            st.caption(_cite_line(m["content"], m["sources"]))
            if m.get("fixes"):
                st.caption(_fix_line(m["fixes"]))
            _render_sources(m["sources"])

if not selected:
    st.info("Select at least one document in the sidebar.")
if question:
    demo_guard(1)
    history = [{"role": m["role"], "content": m["content"]} for m in ss.messages]
    with convo.chat_message("user", avatar=AVATAR["user"]):
        _markdown(question)
    with convo.chat_message("assistant", avatar=AVATAR["assistant"]):
        req = rag.prepare(question, selected, history)
        slot = st.empty()
        try:
            if req.sources:
                with slot:
                    answer = rag.clean_citations(llm.strip_think(st.write_stream(llm.stream(req.system, req.user))))
            else:
                answer = req.not_found_text
            _markdown(answer, slot)  # re-render the final text (RTL for Arabic)
        except LLMError as e:
            st.error(str(e))
            answer = None
        sources = [] if (answer is None or rag.is_not_found(answer)) else req.sources
        fixes = []
        if sources:
            # Safety net: a citation pointing at an unrelated source is replaced by the one that supports it.
            checked = citations.check(answer, sources)
            if checked.corrected:
                answer, fixes = checked.text, checked.corrected
                _markdown(answer, slot)
            st.caption(_cite_line(answer, sources))
            if fixes:
                st.caption(_fix_line(fixes))
            _render_sources(sources)
    ss.messages.append({"role": "user", "content": question})
    if answer:
        ss.messages.append({"role": "assistant", "content": answer, "sources": sources, "fixes": fixes})
