"""Study tools built on the same chunks as RAG and MCQs:
cheat sheet, page explanation, bilingual glossary and concept map.

Results are cached in the progress database (store.study_notes), keyed by tool, document and
options, so reopening a cheat sheet is instant. Pass refresh=True to regenerate.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Iterator

import numpy as np
from pydantic import ValidationError

from core import llm, prompts, store
from core.ingest import fmt_ref, get_embedder
from core.mcq import _extract_json
from core.retriever import all_chunks
from core.schemas import LLM_CONCEPT_SCHEMA, LLM_GLOSSARY_SCHEMA, ConceptMap, GlossaryTerm

SINGLE_PASS_CHARS = 9000     # material up to this size goes to the LLM in one call
NOTES_BATCH_CHARS = 6000     # otherwise: notes per ~6000 chars, then one cheat sheet from the notes
GLOSSARY_BATCH_CHUNKS = 4    # excerpts per glossary call
GLOSSARY_TERMS_PER_CALL = 8

ProgressCb = Callable[[float, str], None]
LANGUAGES = {"English": "en", "العربية": "ar"}


def language_rule(lang: str) -> str:
    return prompts.ARABIC_TERMS_RULE if lang == "ar" else "Write in English."


def language_reminder(lang: str) -> str:
    return prompts.ARABIC_REMINDER if lang == "ar" else ""


def _material(chunks: list[dict]) -> str:
    return "\n\n".join(prompts.STUDY_EXCERPT_ITEM.format(ref=c["ref"], text=c["text"]) for c in chunks)


def _batches(chunks: list[dict], max_chars: int) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for c in chunks:
        if cur and size + len(c["text"]) > max_chars:
            out.append(cur)
            cur, size = [], 0
        cur.append(c)
        size += len(c["text"])
    return out + ([cur] if cur else [])


# --------------------------------------------------------------------------- cheat sheet
def cheat_sheet(doc_id: str, filename: str, lang: str = "en", page_range: tuple[int, int] | None = None,
                progress: ProgressCb | None = None, refresh: bool = False) -> str:
    """One-page Markdown summary with [p.N] citations. Map-reduce for long documents."""
    progress = progress or (lambda f, m: None)
    key = store.note_key("cheatsheet", [doc_id], lang=lang, pages=page_range and f"{page_range[0]}-{page_range[1]}")
    if not refresh and (cached := store.get_note(key)):
        return cached
    chunks = all_chunks([doc_id], page_range)
    if not chunks:
        return ""
    material = _material(chunks)
    if len(material) > SINGLE_PASS_CHARS:
        batches = _batches(chunks, NOTES_BATCH_CHARS)
        notes = []
        for i, b in enumerate(batches):
            progress(0.8 * i / len(batches), f"Taking notes ({i + 1}/{len(batches)})")
            notes.append(llm.generate(prompts.NOTES_SYSTEM, prompts.NOTES_USER.format(excerpts=_material(b))))
        material = "\n".join(notes)
    progress(0.85, "Writing the cheat sheet")
    sheet = llm.generate(prompts.CHEATSHEET_SYSTEM.format(language_rule=language_rule(lang)),
                         prompts.CHEATSHEET_USER.format(title=filename, material=material) + language_reminder(lang),
                         temperature=0.2)
    sheet = sheet.strip()
    store.save_note(key, "cheatsheet", [doc_id], sheet)
    progress(1.0, "Done")
    return sheet


# --------------------------------------------------------------------------- explain a page
def page_text(doc_id: str, page: int) -> tuple[str, str]:
    """(unit-aware reference, full text) of one page/slide/part, rebuilt from its chunks."""
    chunks = all_chunks([doc_id], (page, page))
    if not chunks:
        return fmt_ref("page", page), ""
    # neighbouring chunks overlap; drop the repeated prefix when stitching them back together
    text = chunks[0]["text"]
    for c in chunks[1:]:
        k = next((k for k in range(min(len(text), len(c["text"])), 0, -1) if text.endswith(c["text"][:k])), 0)
        text += ("" if k else "\n") + c["text"][k:]
    return chunks[0]["ref"], text


def explain_page(doc_id: str, filename: str, page: int, lang: str = "ar",
                 refresh: bool = False) -> tuple[str, Iterator[str] | str]:
    """Returns (cache key, cached text or a token stream). The caller saves the streamed text."""
    key = store.note_key("explain", [doc_id], page=page, lang=lang)
    if not refresh and (cached := store.get_note(key)):
        return key, cached
    ref, text = page_text(doc_id, page)
    if not text:
        return key, ""
    system = prompts.EXPLAIN_SYSTEM.format(language_rule=language_rule(lang))
    user = prompts.EXPLAIN_USER.format(ref=ref, filename=filename, text=text) + language_reminder(lang)
    return key, llm.stream(system, user, temperature=0.3)


# --------------------------------------------------------------------------- glossary
def _attribute(items: list, texts: list[str], chunks: list[dict]) -> list:
    """Point each item at the chunk most similar to its text (same idea as mcq.attribute_pages)."""
    if not items:
        return items
    model = get_embedder()
    c_emb = model.encode([f"passage: {c['text']}" for c in chunks], normalize_embeddings=True)
    q_emb = model.encode([f"query: {t}" for t in texts], normalize_embeddings=True)
    best = np.argmax(q_emb @ c_emb.T, axis=1)
    return [(item, chunks[int(b)]) for item, b in zip(items, best)]


def parse_glossary(raw: str) -> list[GlossaryTerm]:
    try:
        data = _extract_json(raw)
    except ValueError:
        return []
    terms = []
    for item in data.get("terms", []) if isinstance(data, dict) else []:
        try:
            terms.append(GlossaryTerm.model_validate(item))
        except ValidationError:
            continue
    return terms


def glossary(doc_id: str, max_terms: int = 30, progress: ProgressCb | None = None,
             refresh: bool = False) -> list[GlossaryTerm]:
    progress = progress or (lambda f, m: None)
    key = store.note_key("glossary", [doc_id], n=max_terms)
    if not refresh and (cached := store.get_note(key)):
        return [GlossaryTerm.model_validate(t) for t in json.loads(cached)]
    chunks = all_chunks([doc_id])
    batches = [chunks[i:i + GLOSSARY_BATCH_CHUNKS] for i in range(0, len(chunks), GLOSSARY_BATCH_CHUNKS)]
    found: dict[str, GlossaryTerm] = {}
    for i, b in enumerate(batches):
        if len(found) >= max_terms:
            break
        progress(i / len(batches), f"Finding terms ({i + 1}/{len(batches)})")
        raw = llm.generate(prompts.GLOSSARY_SYSTEM,
                           prompts.GLOSSARY_USER.format(excerpts=_material(b), n=GLOSSARY_TERMS_PER_CALL),
                           json_schema=LLM_GLOSSARY_SCHEMA, temperature=0.2)
        terms = [t for t in parse_glossary(raw) if t.term.casefold() not in found]
        for t, chunk in _attribute(terms, [f"{t.term}: {t.definition_en}" for t in terms], b):
            found.setdefault(t.term.casefold(), t.model_copy(update={"page": chunk["page"], "unit": chunk["unit"]}))
    result = sorted(found.values(), key=lambda t: t.term.casefold())[:max_terms]
    store.save_note(key, "glossary", [doc_id], json.dumps([t.model_dump() for t in result], ensure_ascii=False))
    progress(1.0, "Done")
    return result


# --------------------------------------------------------------------------- concept map
def concept_map(doc_id: str, filename: str, n_concepts: int = 12, progress: ProgressCb | None = None,
                refresh: bool = False) -> ConceptMap | None:
    """Built from the (cached) English cheat sheet, which is already short and cited."""
    progress = progress or (lambda f, m: None)
    key = store.note_key("conceptmap", [doc_id], n=n_concepts)
    if not refresh and (cached := store.get_note(key)):
        return ConceptMap.model_validate_json(cached)
    progress(0.1, "Reading the cheat sheet")
    notes = cheat_sheet(doc_id, filename, "en", progress=lambda f, m: progress(0.1 + 0.5 * f, m))
    if not notes:
        return None
    user = prompts.CONCEPT_USER.format(notes=notes)
    system = prompts.CONCEPT_SYSTEM.format(n=n_concepts)
    cmap = None
    for attempt in range(2):  # one retry on invalid output, like the MCQ generator
        progress(0.65 + 0.15 * attempt, "Drawing the concept map")
        try:
            cmap = ConceptMap.model_validate(_extract_json(
                llm.generate(system, user, json_schema=LLM_CONCEPT_SCHEMA, temperature=0.2)))
        except (ValueError, ValidationError) as e:
            user += prompts.MCQ_RETRY_SUFFIX.format(error=str(e)[:300])
            continue
        lonely = cmap.isolated()
        if len(lonely) <= len(cmap.nodes) // 4:
            break
        # Small models often list concepts but link only a few: ask once more, naming the loose ones.
        user += prompts.MCQ_RETRY_SUFFIX.format(
            error=f"these concepts have no links: {', '.join(lonely)}. Connect every concept to at least one other.")
    if cmap:
        cmap = cmap.connected_only()  # a concept with no links adds nothing to a map
        if len(cmap.nodes) < 3:
            cmap = None
    if cmap:
        store.save_note(key, "conceptmap", [doc_id], cmap.model_dump_json())
    progress(1.0, "Done")
    return cmap


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_dot(cmap: ConceptMap, unit: str = "page") -> str:
    """Graphviz DOT for st.graphviz_chart (rendered in the browser, no Graphviz install needed)."""
    lines = ["digraph G {", "  rankdir=LR; bgcolor=transparent;",
             '  node [shape=box, style="rounded,filled", fillcolor="#E7EAFB", color="#3346C4", fontname="Helvetica", fontsize=11];',
             '  edge [color="#8A93A6", fontcolor="#5B6475", fontname="Helvetica", fontsize=9];']
    for n in cmap.nodes:
        # DOT's "\n" (backslash + n) is a line break inside a label: name on top, citation below
        label = _q(n.label)[:-1] + (f"\\n({fmt_ref(unit, n.page)})" if n.page else "") + '"'
        lines.append(f"  {_q(n.id)} [label={label}];")
    for e in cmap.edges:
        lines.append(f"  {_q(e.source)} -> {_q(e.target)} [label={_q(e.label)}];")
    return "\n".join(lines + ["}"])


def to_mermaid(cmap: ConceptMap) -> str:
    """Mermaid text, for pasting into notes (Obsidian, Notion, GitHub, mermaid.live)."""
    safe = {n.id: re.sub(r"\W", "_", n.id) for n in cmap.nodes}
    lines = ["flowchart LR"]
    lines += [f'  {safe[n.id]}["{n.label.replace(chr(34), chr(39))}"]' for n in cmap.nodes]
    lines += [f'  {safe[e.source]} -->|{e.label.replace("|", "/") or "relates to"}| {safe[e.target]}' for e in cmap.edges]
    return "\n".join(lines)
