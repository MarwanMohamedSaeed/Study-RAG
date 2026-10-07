"""Question generation for every type: pick chunks -> prompt LLM in batches -> validate ->
attribute pages -> dedupe -> blind check. One loop; each type is described by a Spec."""
from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass
from typing import Callable

import numpy as np
from pydantic import ValidationError

from core import llm, prompts
from core.ingest import get_embedder
from core.retriever import all_chunks, retrieve, sample_spread
from core.grading import fill_matches, normalize
from core.schemas import (LETTERS, LLM_FILL_SCHEMA, LLM_FILL_VERIFY_SCHEMA, LLM_MCQ_SCHEMA, LLM_SHORT_SCHEMA,
                          LLM_TF_SCHEMA, LLM_TF_VERIFY_SCHEMA, LLM_VERIFY_SCHEMA, MCQ, QUESTION_TYPES,
                          FillBlank, ShortAnswer, TrueFalse)

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


def parse_items(raw: str, model=MCQ, list_key: str = "questions",
                valid_pages: set[int] | None = None) -> tuple[list, list[str]]:
    """Validate each item separately so one bad question doesn't sink the batch.
    Returns (valid items, error messages). Raises ValueError if the output is not usable JSON."""
    try:
        data = _extract_json(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})") from e
    items = data.get(list_key, data.get("questions")) if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError(f'JSON must have a "{list_key}" list')
    good, errors = [], []
    for i, item in enumerate(items):
        try:
            q = model.model_validate(item)
        except ValidationError as e:
            errors.append(f"item {i + 1}: {e.errors()[0]['msg']}")
            continue
        if valid_pages and q.source_page not in valid_pages:
            # models sometimes cite an excerpt number instead of a page; snap to the nearest real page
            q.source_page = min(valid_pages, key=lambda p: abs(p - q.source_page))
        good.append(q)
    return good, errors


def parse_mcqs(raw: str, valid_pages: set[int] | None = None) -> tuple[list[MCQ], list[str]]:
    return parse_items(raw, MCQ, "questions", valid_pages)


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


def _build_user_prompt(spec: "Spec", chunks: list[dict], n: int, difficulty: str, topic: str | None,
                       avoid: list) -> str:
    avoid_block = ""
    if avoid:
        avoid_block = "Do NOT repeat these existing questions:\n" + "\n".join(f"- {q.question}" for q in avoid[-15:]) + "\n"
    topic_line = f"Focus on the topic: {topic.strip()}\n" if topic and topic.strip() else ""
    return spec.user.format(n=n, difficulty_guide=prompts.DIFFICULTY_GUIDE[difficulty],
                            topic_line=topic_line, avoid_block=avoid_block, excerpts=_excerpts(chunks))


def _review(system: str, user: str, schema: dict, list_key: str) -> list[dict] | None:
    """Run a blind-check prompt; None means the review itself was unusable (callers then fail open)."""
    try:
        data = _extract_json(llm.generate(system, user, json_schema=schema, temperature=0.0))
        items = data.get(list_key)
        return items if isinstance(items, list) else None
    except (ValueError, KeyError, TypeError, AttributeError):  # JSONDecodeError is a ValueError
        return None


def verify(questions: list[MCQ], chunks: list[dict]) -> list[MCQ]:
    """Blind-solve pass: keep a question only if the model, without seeing the key, finds exactly
    one correct option and it is the keyed one. Fails open (keeps all) if the review is unusable."""
    if not questions:
        return []
    listing = "\n\n".join(f"{i}. {q.question}\n" + "\n".join(f"   {L}) {o}" for L, o in zip(LETTERS, q.options))
                          for i, q in enumerate(questions, 1))
    items = _review(prompts.MCQ_VERIFY_SYSTEM, prompts.MCQ_VERIFY_USER.format(excerpts=_excerpts(chunks), questions=listing),
                    LLM_VERIFY_SCHEMA, "reviews")
    if items is None:
        return questions
    reviews = {int(r["number"]): {str(x).upper() for x in r.get("correct_options", [])} for r in items if "number" in r}
    return [q for i, q in enumerate(questions, 1) if reviews.get(i) == {q.correct}]


def verify_tf(questions: list[TrueFalse], chunks: list[dict]) -> list[TrueFalse]:
    """Keep a statement only if a blind judge agrees it is true/false (not 'not stated')."""
    if not questions:
        return []
    listing = "\n".join(f"{i}. {q.statement}" for i, q in enumerate(questions, 1))
    items = _review(prompts.TF_VERIFY_SYSTEM, prompts.TF_VERIFY_USER.format(excerpts=_excerpts(chunks), questions=listing),
                    LLM_TF_VERIFY_SCHEMA, "judgements")
    if items is None:
        return questions
    verdicts = {int(r["number"]): str(r.get("verdict", "")).strip().lower() for r in items if "number" in r}
    return [q for i, q in enumerate(questions, 1) if verdicts.get(i) == ("true" if q.answer else "false")]


def verify_fill(questions: list[FillBlank], chunks: list[dict]) -> list[FillBlank]:
    """Keep a sentence only if a blind solver fills the blank with an accepted answer (i.e. unambiguous)."""
    if not questions:
        return []
    listing = "\n".join(f"{i}. {q.sentence}" for i, q in enumerate(questions, 1))
    items = _review(prompts.FILL_VERIFY_SYSTEM, prompts.FILL_VERIFY_USER.format(excerpts=_excerpts(chunks),
                                                                              questions=listing),
                    LLM_FILL_VERIFY_SCHEMA, "fills")
    if items is None:
        return questions
    fills = {int(r["number"]): str(r.get("answer", "")) for r in items if "number" in r}
    return [q for i, q in enumerate(questions, 1) if fill_matches(q, fills.get(i))]


def grounded_fill(questions: list[FillBlank], chunks: list[dict]) -> list[FillBlank]:
    """Code check, no LLM: the blank's answer must literally appear in the excerpts it was written from.
    Otherwise the model used outside knowledge, and the same model's blind check can't catch that."""
    text = f" {normalize(' '.join(c['text'] for c in chunks))} "
    return [q for q in questions if any(f" {normalize(a)} " in text for a in [q.answer, *q.alternatives] if normalize(a))]


def attribute_pages(questions: list, chunks: list[dict]) -> list:
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


@dataclass(frozen=True)
class Spec:
    """Everything that differs between question types; the generation loop is shared."""
    model: type
    list_key: str
    system: str
    user: str
    schema: dict
    verify: Callable | None = None


SPECS = {
    "mcq": Spec(MCQ, "questions", prompts.MCQ_SYSTEM, prompts.MCQ_USER, LLM_MCQ_SCHEMA, verify),
    "tf": Spec(TrueFalse, "statements", prompts.TF_SYSTEM, prompts.TF_USER, LLM_TF_SCHEMA, verify_tf),
    "fill": Spec(FillBlank, "blanks", prompts.FILL_SYSTEM, prompts.FILL_USER, LLM_FILL_SCHEMA, verify_fill),
    # Short answers have no single key to re-solve; they are graded against the lecture instead.
    "short": Spec(ShortAnswer, "short_answers", prompts.SHORT_SYSTEM, prompts.SHORT_USER, LLM_SHORT_SCHEMA),
}


def _ask(spec: Spec, chunks: list[dict], n: int, difficulty: str, topic: str | None, avoid: list) -> list:
    """One LLM call + validation, with exactly one retry on invalid output."""
    user = _build_user_prompt(spec, chunks, n, difficulty, topic, avoid)
    pages = {c["page"] for c in chunks}
    temperature = 0.4
    for attempt in range(2):
        raw = llm.generate(spec.system, user, json_schema=spec.schema, temperature=temperature)
        try:
            good, errors = parse_items(raw, spec.model, spec.list_key, pages)
        except ValueError as e:
            good, errors = [], [str(e)]
        if good or attempt == 1:
            return attribute_pages(good, chunks)
        user += prompts.MCQ_RETRY_SUFFIX.format(error="; ".join(errors[:3]))
        temperature = 0.2
    return []


def generate_quiz(doc_ids: list[str], n: int = 10, difficulty: str = "medium", topic: str | None = None,
                  page_range: tuple[int, int] | None = None, progress: ProgressCb | None = None,
                  seed: int | None = None, verify_answers: bool = True, phase: float = 0.0,
                  kind: str = "mcq", focus: set[tuple[str, int]] | None = None, avoid: list | None = None) -> list:
    """Generate n questions of one type. `focus` limits the material to these (doc_id, page) pairs
    (used to practise weak pages); `avoid` are questions already written (e.g. other types in a
    mixed quiz), which the model is told not to repeat and which count for de-duplication."""
    avoid = avoid or []
    if difficulty not in prompts.DIFFICULTY_GUIDE:
        raise ValueError(f"difficulty must be one of {list(prompts.DIFFICULTY_GUIDE)}")
    spec = SPECS[kind]
    progress = progress or (lambda f, m: None)
    rng = random.Random(seed)
    n_batches = math.ceil(n / BATCH_QUESTIONS)
    if focus:
        pool = [c for c in all_chunks(doc_ids, page_range) if (c["doc_id"], c["page"]) in focus]
        # start each question type (phase) at a different point, or they all see the same first excerpts
        k = int(phase * len(pool))
        pool = pool[k:] + pool[:k]
    else:
        pool = select_chunks(doc_ids, (n_batches + MAX_EXTRA_ROUNDS) * CHUNKS_PER_BATCH, topic, page_range,
                             n_main=n_batches * CHUNKS_PER_BATCH, phase=phase)
    if not pool:
        return []
    # Cycle through the pool in order so successive batches cover different parts of the material.
    questions: list = []
    empty_rounds = 0
    for round_ in range(n_batches + MAX_EXTRA_ROUNDS):
        if len(questions) >= n or empty_rounds >= MAX_EMPTY_ROUNDS:
            break
        start = (round_ * CHUNKS_PER_BATCH) % len(pool)
        chunks = (pool + pool)[start:start + min(CHUNKS_PER_BATCH, len(pool))]
        pages = ", ".join(str(p) for p in sorted({c["page"] for c in chunks}))
        progress(len(questions) / n, f"Writing questions ({len(questions)}/{n} done, pages {pages})")
        # Always ask for a full batch: verification rejects some, extras are trimmed at the end.
        batch = _ask(spec, chunks, BATCH_QUESTIONS, difficulty, topic, avoid + questions)
        if kind == "fill":
            batch = grounded_fill(batch, chunks)
        if kind == "mcq":
            # Shuffle before verifying so the reviewer can't exploit the model's "answer is A" bias.
            batch = [shuffle_options(q, rng) for q in batch]
        batch = dedupe(batch, avoid + questions)
        if verify_answers and batch and spec.verify:
            progress(len(questions) / n, f"Checking answers ({len(questions)}/{n} done)")
            batch = spec.verify(batch, chunks)
        questions += batch
        empty_rounds = 0 if batch else empty_rounds + 1
    progress(1.0, f"Generated {min(n, len(questions))} questions")
    return questions[:n]


def generate_mixed(doc_ids: list[str], n: int, difficulty: str, kinds: list[str], progress: ProgressCb | None = None,
                   seed: int | None = None, **kwargs) -> list:
    """A quiz mixing question types: n split evenly across `kinds`, then shuffled together."""
    from core.exam import split_counts
    progress = progress or (lambda f, m: None)
    counts = split_counts(n, {k: 1 / len(kinds) for k in kinds})
    out, done = [], 0
    for i, (kind, k) in enumerate(counts.items()):
        out += generate_quiz(doc_ids, k, difficulty, seed=seed, kind=kind, phase=i / len(counts),
                             progress=lambda f, m, d=done, k=k, kind=kind: progress((d + f * k) / n,
                                                                                    f"[{QUESTION_TYPES[kind]}] {m}"),
                             avoid=out, **kwargs)
        done += k
    random.Random(seed).shuffle(out)
    return out
