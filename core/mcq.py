"""MCQ generation: pick chunks -> prompt LLM in batches -> validate -> dedupe -> shuffle."""
from __future__ import annotations

import json
import math
import random
import re
from typing import Callable

import numpy as np
from pydantic import ValidationError

from core import llm, prompts
from core.ingest import get_embedder
from core.retriever import all_chunks, retrieve, sample_spread
from core.schemas import LETTERS, LLM_MCQ_SCHEMA, LLM_VERIFY_SCHEMA, MCQ

BATCH_QUESTIONS = 5       # questions requested per LLM call (small models do better in small batches)
CHUNKS_PER_BATCH = 3      # excerpts given per call (~2.4k chars of context)
DUP_THRESHOLD = 0.93      # cosine similarity above which two questions count as duplicates
MAX_EXTRA_ROUNDS = 4      # extra LLM calls allowed to make up for rejected/duplicate questions
MAX_EMPTY_ROUNDS = 2      # stop early when the material is exhausted (rounds that add nothing)
TOPIC_SCORE_MARGIN = 0.05 # topic mode: keep chunks within this similarity of the best hit

ProgressCb = Callable[[float, str], None]


# --------------------------------------------------------------------------- parsing & validation
def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            raise
        data = json.loads(m.group(0))
    if isinstance(data, list):
        data = {"questions": data}
    return data


def parse_mcqs(raw: str, valid_pages: set[int] | None = None) -> tuple[list[MCQ], list[str]]:
    """Validate each item separately so one bad question doesn't sink the batch.
    Returns (valid questions, error messages). Raises ValueError if the output is not usable JSON."""
    try:
        data = _extract_json(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})") from e
    items = data.get("questions")
    if not isinstance(items, list):
        raise ValueError('JSON must have a "questions" list')
    good, errors = [], []
    for i, item in enumerate(items):
        try:
            q = MCQ.model_validate(item)
        except ValidationError as e:
            errors.append(f"question {i + 1}: {e.errors()[0]['msg']}")
            continue
        if valid_pages and q.source_page not in valid_pages:
            # models sometimes cite an excerpt number instead of a page; snap to the nearest real page
            q.source_page = min(valid_pages, key=lambda p: abs(p - q.source_page))
        good.append(q)
    return good, errors


def shuffle_options(q: MCQ, rng: random.Random) -> MCQ:
    """Small models put the answer under 'A' far too often; shuffle so the key is balanced."""
    correct_text = q.correct_text
    opts = q.options[:]
    rng.shuffle(opts)
    return q.model_copy(update={"options": opts, "correct": LETTERS[opts.index(correct_text)]})


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.casefold()).strip()


def dedupe(questions: list[MCQ], existing: list[MCQ] | None = None,
           threshold: float = DUP_THRESHOLD) -> list[MCQ]:
    """Drop questions that are near-duplicates (embedding cosine >= threshold) of earlier ones."""
    existing = existing or []
    if not questions:
        return []
    texts = [q.question for q in existing + questions]
    embs = get_embedder().encode([f"query: {t}" for t in texts], normalize_embeddings=True)
    kept_idx = list(range(len(existing)))
    seen = {_norm(q.question) for q in existing}
    out = []
    for j, q in enumerate(questions, start=len(existing)):
        if _norm(q.question) in seen:
            continue
        if kept_idx and float(np.max(embs[kept_idx] @ embs[j])) >= threshold:
            continue
        kept_idx.append(j)
        seen.add(_norm(q.question))
        out.append(q)
    return out


# --------------------------------------------------------------------------- generation
def select_chunks(doc_ids: list[str], n_needed: int, topic: str | None,
                  page_range: tuple[int, int] | None, n_main: int | None = None, phase: float = 0.0) -> list[dict]:
    """Topic given -> most relevant chunks; otherwise chunks spread across the document.

    Without a topic, the first `n_main` chunks (used by the planned batches) are spread evenly over
    the whole document; the other chunks follow, for extra rounds only. Taking all chunks in reading
    order instead would make a short quiz cover only the first few pages."""
    if topic and topic.strip():
        hits = retrieve(topic, doc_ids, k=n_needed, page_range=page_range)
        if not hits:
            return []
        # On short documents top-k is "everything"; keep only chunks close to the best match.
        relevant = [h for h in hits if h["score"] >= hits[0]["score"] - TOPIC_SCORE_MARGIN]
        return relevant if len(relevant) >= CHUNKS_PER_BATCH else hits[:CHUNKS_PER_BATCH]
    main = sample_spread(doc_ids, n_main or n_needed, page_range, phase=phase)
    used = {(c["doc_id"], c["page"], c["chunk_index"]) for c in main}
    rest = [c for c in all_chunks(doc_ids, page_range) if (c["doc_id"], c["page"], c["chunk_index"]) not in used]
    return (main + rest)[:max(n_needed, len(main))]


def _excerpts(chunks: list[dict]) -> str:
    return "\n\n".join(prompts.MCQ_EXCERPT_ITEM.format(
        i=i + 1, where=f"{c.get('unit', 'page')} {c['page']}", text=c["text"]) for i, c in enumerate(chunks))


def _build_user_prompt(chunks: list[dict], n: int, difficulty: str, topic: str | None,
                       avoid: list[MCQ]) -> str:
    excerpts = _excerpts(chunks)
    avoid_block = ""
    if avoid:
        avoid_block = "Do NOT repeat these existing questions:\n" + "\n".join(f"- {q.question}" for q in avoid[-15:]) + "\n"
    topic_line = f"Focus on the topic: {topic.strip()}\n" if topic and topic.strip() else ""
    return prompts.MCQ_USER.format(n=n, difficulty_guide=prompts.DIFFICULTY_GUIDE[difficulty],
                                   topic_line=topic_line, avoid_block=avoid_block, excerpts=excerpts)


def verify(questions: list[MCQ], chunks: list[dict]) -> list[MCQ]:
    """Blind-solve pass: keep a question only if the model, without seeing the key, finds exactly
    one correct option and it is the keyed one. Fails open (keeps all) if the review is unusable."""
    if not questions:
        return []
    excerpts = _excerpts(chunks)
    listing = "\n\n".join(f"{i}. {q.question}\n" + "\n".join(f"   {L}) {o}" for L, o in zip(LETTERS, q.options))
                          for i, q in enumerate(questions, 1))
    try:
        raw = llm.generate(prompts.MCQ_VERIFY_SYSTEM,
                           prompts.MCQ_VERIFY_USER.format(excerpts=excerpts, questions=listing),
                           json_schema=LLM_VERIFY_SCHEMA, temperature=0.0)
        reviews = {int(r["number"]): {str(x).upper() for x in r.get("correct_options", [])}
                   for r in _extract_json(raw).get("reviews", [])}
    except (ValueError, KeyError, TypeError):  # JSONDecodeError is a ValueError
        return questions
    return [q for i, q in enumerate(questions, 1) if reviews.get(i) == {q.correct}]


def attribute_pages(questions: list[MCQ], chunks: list[dict]) -> list[MCQ]:
    """Set source_page from the chunk most similar to question + correct answer.
    Small models often write the excerpt *number* instead of its page, so we don't trust theirs."""
    if not questions or not chunks:
        return questions
    model = get_embedder()
    c_emb = model.encode([f"passage: {c['text']}" for c in chunks], normalize_embeddings=True)
    q_emb = model.encode([f"query: {q.question} {q.correct_text}" for q in questions], normalize_embeddings=True)
    best = np.argmax(q_emb @ c_emb.T, axis=1)
    return [q.model_copy(update={"source_page": chunks[int(b)]["page"],
                                 "source_unit": chunks[int(b)].get("unit", "page"),
                                 "source_doc": chunks[int(b)].get("doc_id", ""),
                                 "source_file": chunks[int(b)].get("filename", "")})
            for q, b in zip(questions, best)]


def _ask(chunks: list[dict], n: int, difficulty: str, topic: str | None, avoid: list[MCQ]) -> list[MCQ]:
    """One LLM call + validation, with exactly one retry on invalid output."""
    user = _build_user_prompt(chunks, n, difficulty, topic, avoid)
    pages = {c["page"] for c in chunks}
    temperature = 0.4
    for attempt in range(2):
        raw = llm.generate(prompts.MCQ_SYSTEM, user, json_schema=LLM_MCQ_SCHEMA, temperature=temperature)
        try:
            good, errors = parse_mcqs(raw, pages)
        except ValueError as e:
            good, errors = [], [str(e)]
        if good or attempt == 1:
            return attribute_pages(good, chunks)
        user += prompts.MCQ_RETRY_SUFFIX.format(error="; ".join(errors[:3]))
        temperature = 0.2
    return []


def generate_quiz(doc_ids: list[str], n: int = 10, difficulty: str = "medium", topic: str | None = None,
                  page_range: tuple[int, int] | None = None, progress: ProgressCb | None = None,
                  seed: int | None = None, verify_answers: bool = True, phase: float = 0.0) -> list[MCQ]:
    if difficulty not in prompts.DIFFICULTY_GUIDE:
        raise ValueError(f"difficulty must be one of {list(prompts.DIFFICULTY_GUIDE)}")
    progress = progress or (lambda f, m: None)
    rng = random.Random(seed)
    n_batches = math.ceil(n / BATCH_QUESTIONS)
    pool = select_chunks(doc_ids, (n_batches + MAX_EXTRA_ROUNDS) * CHUNKS_PER_BATCH, topic, page_range,
                         n_main=n_batches * CHUNKS_PER_BATCH, phase=phase)
    if not pool:
        return []
    # Cycle through the pool in order so successive batches cover different parts of the material.
    questions: list[MCQ] = []
    empty_rounds = 0
    for round_ in range(n_batches + MAX_EXTRA_ROUNDS):
        if len(questions) >= n or empty_rounds >= MAX_EMPTY_ROUNDS:
            break
        start = (round_ * CHUNKS_PER_BATCH) % len(pool)
        chunks = (pool + pool)[start:start + min(CHUNKS_PER_BATCH, len(pool))]
        pages = ", ".join(str(p) for p in sorted({c["page"] for c in chunks}))
        progress(len(questions) / n, f"Writing questions ({len(questions)}/{n} done, pages {pages})")
        # Always ask for a full batch: verification rejects some, extras are trimmed at the end.
        # Shuffle before verifying so the reviewer can't exploit the model's "answer is A" bias.
        batch = [shuffle_options(q, rng) for q in _ask(chunks, BATCH_QUESTIONS, difficulty, topic, questions)]
        batch = dedupe(batch, questions)
        if verify_answers and batch:
            progress(len(questions) / n, f"Checking answers ({len(questions)}/{n} done)")
            batch = verify(batch, chunks)
        questions += batch
        empty_rounds = 0 if batch else empty_rounds + 1
    progress(1.0, f"Generated {min(n, len(questions))} questions")
    return questions[:n]
