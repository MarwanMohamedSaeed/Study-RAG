"""Exam simulation: a mixed-difficulty question set across several documents, plus a report."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Callable

from core.mcq import dedupe, generate_quiz
from core.schemas import MCQ

MIXES = {
    "Balanced (30% easy · 50% medium · 20% hard)": {"easy": .3, "medium": .5, "hard": .2},
    "Warm-up (60% easy · 40% medium)": {"easy": .6, "medium": .4, "hard": 0},
    "Challenge (20% medium · 80% hard)": {"easy": 0, "medium": .2, "hard": .8},
}

ProgressCb = Callable[[float, str], None]


def split_counts(n: int, mix: dict[str, float]) -> dict[str, int]:
    """Largest-remainder rounding, so the counts always add up to n."""
    raw = {k: n * w for k, w in mix.items()}
    counts = {k: math.floor(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True)[: n - sum(counts.values())]:
        counts[k] += 1
    return {k: v for k, v in counts.items() if v}


def build_exam(doc_ids: list[str], n: int, mix: dict[str, float], progress: ProgressCb | None = None,
               seed: int | None = None) -> tuple[list[MCQ], list[str]]:
    """Returns (questions, difficulty of each question), in shuffled order."""
    progress = progress or (lambda f, m: None)
    counts = split_counts(n, mix)
    done, pairs = 0, []
    for i, (level, k) in enumerate(counts.items()):
        # Each level samples the lectures at shifted points, so levels don't reuse the same chunks.
        qs = generate_quiz(doc_ids, k, level, seed=seed, phase=i / len(counts),
                           progress=lambda f, m, d=done, lv=level, k=k: progress((d + f * k) / n, f"[{lv}] {m}"))
        kept = dedupe(qs, [q for q, _ in pairs])  # no repeats across difficulty levels
        pairs += [(q, level) for q in kept]
        done += k
    random.Random(seed).shuffle(pairs)
    progress(1.0, f"Exam ready: {len(pairs)} questions")
    return [q for q, _ in pairs], [d for _, d in pairs]


def report(questions: list[MCQ], difficulties: list[str], answers: list[str | None]) -> dict:
    """Score overall, per difficulty and per page (weakest first)."""
    by_level: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_page: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    for q, d, a in zip(questions, difficulties, answers):
        ok = int(a == q.correct)
        by_level[d][0] += ok
        by_level[d][1] += 1
        page_key = (q.source_file, q.source_ref)
        by_page[page_key][0] += ok
        by_page[page_key][1] += 1
    order = ["easy", "medium", "hard"]
    return {
        "score": sum(a == q.correct for q, a in zip(questions, answers)),
        "total": len(questions),
        "unanswered": sum(a is None for a in answers),
        "by_level": [{"level": lv, "correct": c, "total": t} for lv in order if lv in by_level
                     for c, t in [by_level[lv]]],
        "weak_pages": sorted(({"file": f, "ref": r, "correct": c, "total": t}
                              for (f, r), (c, t) in by_page.items() if c < t),
                             key=lambda p: (p["correct"] / p["total"], -p["total"])),
    }
