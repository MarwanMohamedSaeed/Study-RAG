"""Flashcards from three sources: the glossary, the lecture itself (LLM), and your quiz mistakes."""
from __future__ import annotations

import json
from typing import Callable

from pydantic import ValidationError

from core import llm, prompts, store, study
from core.mcq import _extract_json
from core.retriever import sample_spread
from core.schemas import LLM_CARDS_SCHEMA, MCQ, Flashcard, GlossaryTerm

CARDS_PER_CALL = 6
CHUNKS_PER_CALL = 3

ProgressCb = Callable[[float, str], None]


def from_glossary(doc_id: str, filename: str, progress: ProgressCb | None = None) -> int:
    """Term on the front; English definition plus the Arabic term and definition on the back.
    Reuses the glossary you already built for this document (any size) before generating one."""
    cached = store.latest_note("glossary", doc_id)
    terms = ([GlossaryTerm.model_validate(t) for t in json.loads(cached)] if cached
             else study.glossary(doc_id, progress=progress))
    return store.add_cards([{"doc_id": doc_id, "filename": filename, "front": t.term,
                             "back": f"{t.definition_en}\n\n{t.arabic}: {t.definition_ar}",
                             "source_page": t.page, "source_unit": t.unit} for t in terms], "glossary")


def parse_cards(raw: str) -> list[Flashcard]:
    try:
        data = _extract_json(raw)
    except ValueError:
        return []
    out = []
    for item in data.get("cards", []) if isinstance(data, dict) else []:
        try:
            out.append(Flashcard.model_validate(item))
        except ValidationError:
            continue
    return out


def generate_cards(doc_id: str, filename: str, n: int = 20, progress: ProgressCb | None = None) -> int:
    """LLM-written cards from chunks spread across the lecture; source page set by embedding match."""
    progress = progress or (lambda f, m: None)
    chunks = sample_spread([doc_id], max(CHUNKS_PER_CALL, round(n / CARDS_PER_CALL * CHUNKS_PER_CALL)))
    batches = [chunks[i:i + CHUNKS_PER_CALL] for i in range(0, len(chunks), CHUNKS_PER_CALL)]
    made: dict[str, dict] = {}
    for i, b in enumerate(batches):
        if len(made) >= n:
            break
        progress(i / len(batches), f"Writing cards ({len(made)}/{n})")
        raw = llm.generate(prompts.CARDS_SYSTEM, prompts.CARDS_USER.format(excerpts=study._material(b), n=CARDS_PER_CALL),
                           json_schema=LLM_CARDS_SCHEMA, temperature=0.3)
        cards = [c for c in parse_cards(raw) if c.front.casefold() not in made]
        for c, chunk in study._attribute(cards, [f"{c.front} {c.back}" for c in cards], b):
            made.setdefault(c.front.casefold(), {"doc_id": doc_id, "filename": filename, "front": c.front,
                                                 "back": c.back, "source_page": chunk["page"],
                                                 "source_unit": chunk["unit"]})
    progress(1.0, "Saving cards")
    return store.add_cards(list(made.values())[:n], "generated")


def from_mistakes(doc_ids: list[str] | None = None) -> int:
    """Every question you last answered wrong becomes a card: the question, then the right answer."""
    cards = []
    for q in store.mistakes(doc_ids, limit=200):
        front = q.question
        if isinstance(q, MCQ):  # keep the options, otherwise "Which of these..." makes no sense
            front += "\n" + "\n".join(f"{L}) {o}" for L, o in zip("ABCD", q.options))
        back = q.correct_text + (f"\n\n{q.explanation}" if q.explanation else "")
        cards.append({"doc_id": q.source_doc or None, "filename": q.source_file or None, "front": front,
                      "back": back, "source_page": q.source_page, "source_unit": q.source_unit})
    return store.add_cards(cards, "mistake")
