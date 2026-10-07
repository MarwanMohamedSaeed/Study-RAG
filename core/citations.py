"""Citation checking for RAG answers: catch citations that point at an unrelated source, and flag
sentences that no retrieved source supports.

Measured on 50 benchmark questions (eval/citations.py), the model's own citations were already
correct once retrieval used hybrid search + re-ranking. A first version that "corrected" citations
with embedding similarity made them WORSE (100% -> 94% gold), partly because the multilingual
embeddings prefer a page in the same language as the sentence. So the checker is conservative:
  - it judges support with the cross-encoder re-ranker (reads sentence + source together, no
    language bias), falling back to embeddings if the re-ranker is unavailable;
  - it only replaces a citation when the cited source clearly does NOT support the sentence and
    another retrieved source clearly does;
  - it never rewrites uncited sentences; unsupported sentences are flagged for the student instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from core import rag

# Cross-encoder logits (mMiniLM re-ranker): > 0 means "relevant", negative means unrelated.
# Tuned on eval/citations.py: 0 false corrections on 48 clean answers at every setting tried; with
# these values 56% of deliberately injected wrong citations are repaired.
UNRELATED_BELOW = 0.0     # cited source scoring below this does not support the sentence
CLEAR_SUPPORT = 2.0       # a replacement must score at least this
SUPPORTED = 0.0           # best source below this -> sentence listed as not clearly supported
# (The UI does not show "unsupported": 3 of 4 flags on the benchmark were true statements.)
MIN_WORDS = 4

_SPLIT = re.compile(r"(?<=[.!?؟])\s+|\n+")


@dataclass
class CitationCheck:
    text: str                                                        # the answer with fixed citations
    corrected: list[tuple[str, str]] = field(default_factory=list)   # (old label, new label)
    unsupported: list[str] = field(default_factory=list)             # sentences no source supports
    scorer: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.corrected)


def _label(c: dict) -> str:
    return f"{c['filename']} {c['ref']}"


def _claim(segment: str) -> str:
    """The sentence without its citations or list markers."""
    text = rag.CITE_RE.sub("", segment)
    return re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*|\*\*", "", text).strip(" .;:")


def _support_matrix(claims: list[str], sources: list[dict]) -> tuple[np.ndarray, str]:
    """claims x sources support scores, with the re-ranker if it can be loaded."""
    from core import retriever
    try:
        model = retriever._load_reranker()
        pairs = [(c, s["text"]) for c in claims for s in sources]
        return np.array(model.predict(pairs)).reshape(len(claims), len(sources)), "cross-encoder"
    except Exception:
        from core.ingest import get_embedder
        emb = get_embedder()
        q = emb.encode([f"query: {c}" for c in claims], normalize_embeddings=True)
        p = emb.encode([f"passage: {s['text']}" for s in sources], normalize_embeddings=True)
        # map cosine (~0.7 unrelated .. ~0.9 same fact) onto the logit-like thresholds above
        return (q @ p.T - 0.8) * 40, "embeddings"


def check(answer: str, sources: list[dict]) -> CitationCheck:
    if not answer or not sources or rag.is_not_found(answer):
        return CitationCheck(answer)
    segments = [s for s in _SPLIT.split(answer) if s.strip()]
    claims = [(s, _claim(s)) for s in segments if len(_claim(s).split()) >= MIN_WORDS]
    if not claims:
        return CitationCheck(answer)
    labels = [_label(c) for c in sources]
    scores, scorer = _support_matrix([c for _, c in claims], sources)

    result = CitationCheck(answer, scorer=scorer)
    for (segment, claim), row in zip(claims, scores):
        best = int(np.argmax(row))
        best_label, best_score = labels[best], float(row[best])
        cited = list(dict.fromkeys(rag.CITE_RE.findall(segment)))
        new_segment = segment
        for lab in cited:
            own = max((float(row[i]) for i, l in enumerate(labels) if l == lab), default=None)
            unrelated = own is None or own < UNRELATED_BELOW     # None: the model invented the label
            if unrelated and best_score >= CLEAR_SUPPORT and best_label not in cited:
                new_segment = new_segment.replace(f"[{lab}]", f"[{best_label}]")
                result.corrected.append((lab, best_label))
        if best_score < SUPPORTED:
            result.unsupported.append(claim)
        if new_segment != segment:
            result.text = result.text.replace(segment, new_segment, 1)
    return result
