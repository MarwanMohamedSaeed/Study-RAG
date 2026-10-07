"""RAG Q&A pipeline: retrieve -> build grounded prompt -> LLM -> answer + sources."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core import config, llm, prompts
from core.retriever import retrieve

_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")
LANG_NAME = {"en": "English", "ar": "Arabic"}
# A citation like [lecture.pdf p.3], [slides.pptx slide 4] or [notes.docx part 2]
CITE_RE = re.compile(r"\[([^\[\]]+? (?:p\.|slide |part )\d+)\]")


def detect_language(text: str) -> str:
    """'ar' if the text is mostly Arabic script, else 'en'."""
    return "ar" if len(_ARABIC.findall(text)) > len(_LATIN.findall(text)) else "en"


def build_context(chunks: list[dict]) -> str:
    return "\n\n".join(prompts.RAG_CONTEXT_ITEM.format(filename=c["filename"], ref=c["ref"], text=c["text"])
                       for c in chunks)


def format_history(history: list[dict] | None, max_turns: int = 2) -> str:
    """Last few Q/A turns so follow-ups like 'and for UDP?' make sense."""
    if not history:
        return ""
    turns = history[-2 * max_turns:]
    lines = [f"{'Student' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in turns]
    return "CONVERSATION SO FAR:\n" + "\n".join(lines) + "\n\n"


def clean_citations(answer: str) -> str:
    """Normalise citation brackets some models use (gpt-oss writes 【file p.3】) to [file p.3],
    so the citation line, the citation check and right-to-left rendering all recognise them."""
    return re.sub(r"[【〔［]\s*([^【】〔〕［］\n]+?)\s*[】〕］]", r"[\1]", answer)


def is_not_found(answer: str) -> bool:
    a = answer.strip().strip('"')
    return any(a.startswith(s[:25]) for s in prompts.NOT_FOUND.values())


@dataclass
class RagRequest:
    question: str
    lang: str
    system: str
    user: str
    sources: list[dict] = field(default_factory=list)

    @property
    def not_found_text(self) -> str:
        return prompts.NOT_FOUND[self.lang]


def _retrieval_query(question: str, history: list[dict] | None) -> str:
    # Short follow-up questions borrow the previous user question for retrieval.
    if history and len(question.split()) < 6:
        prev = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        return f"{prev} {question}".strip()
    return question


def prepare(question: str, doc_ids: list[str], history: list[dict] | None = None,
            k: int = config.TOP_K) -> RagRequest:
    lang = detect_language(question)
    sources = retrieve(_retrieval_query(question, history), doc_ids, k=k)
    system = prompts.RAG_SYSTEM.format(not_found=prompts.NOT_FOUND[lang], language=LANG_NAME[lang])
    user = prompts.RAG_USER.format(context=build_context(sources), history=format_history(history),
                                   question=question, language=LANG_NAME[lang])
    return RagRequest(question, lang, system, user, sources)


def answer(question: str, doc_ids: list[str], history: list[dict] | None = None,
           k: int = config.TOP_K) -> dict:
    """Non-streaming helper (used by eval/tests). The UI streams via prepare() + llm.stream()."""
    req = prepare(question, doc_ids, history, k)
    if not req.sources:
        return {"answer": req.not_found_text, "sources": [], "not_found": True}
    text = clean_citations(llm.generate(req.system, req.user).strip())
    return {"answer": text, "sources": req.sources, "not_found": is_not_found(text)}
