"""Grading answers for every question type. Short answers are graded by the LLM against the lecture."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from core import llm, prompts
from core.schemas import LLM_GRADE_SCHEMA, FillBlank, Question, ShortAnswer, TrueFalse

TYPO_RATIO = 0.85     # fill-in answers this similar to the key (and 5+ chars long) count as correct
_NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}


@dataclass
class Grade:
    score: float                 # 1, 0.5 (short answers only) or 0
    feedback: str = ""
    missing: list[str] = field(default_factory=list)

    @property
    def correct(self) -> bool:
        """For statistics, half credit counts as correct."""
        return self.score >= 0.5


def normalize(text: str) -> str:
    """Case, punctuation, articles, Arabic diacritics and number words don't matter."""
    t = unicodedata.normalize("NFKC", text).casefold()
    t = re.sub(r"[ً-ْ]", "", t)              # Arabic short-vowel marks
    t = re.sub(r"[^\w\s.]|(?<!\d)\.|\.(?!\d)", " ", t)  # punctuation, but keep decimal points
    words = [_NUMBER_WORDS.get(w, w) for w in t.split() if w not in {"the", "a", "an"}]
    return " ".join(words)


def fill_matches(q: FillBlank, given: str | None) -> bool:
    g = normalize(given or "")
    if not g:
        return False
    for target in [q.answer, *q.alternatives]:
        t = normalize(target)
        if g == t or (len(t) >= 5 and SequenceMatcher(None, g, t).ratio() >= TYPO_RATIO):
            return True
    return False


def grade(q: Question, given: str | None) -> Grade:
    if given is None or not str(given).strip():
        return Grade(0, "No answer.")
    if isinstance(q, TrueFalse):
        return Grade(float(given == q.correct_text))
    if isinstance(q, FillBlank):
        return Grade(float(fill_matches(q, given)))
    if isinstance(q, ShortAnswer):
        return grade_short(q, given)
    return Grade(float(given == q.correct))  # MCQ: given is a letter


def grade_short(q: ShortAnswer, given: str) -> Grade:
    from core.mcq import _extract_json   # local imports avoid an import cycle
    from core.study import page_text
    ref, lecture = page_text(q.source_doc, q.source_page) if q.source_doc else (q.source_ref, "")
    user = prompts.GRADE_USER.format(question=q.question, reference=q.reference, key_points="; ".join(q.key_points),
                                     ref=ref, lecture=lecture or "(not available)", answer=given.strip())
    for _ in range(2):  # one retry on unreadable output
        try:
            data = _extract_json(llm.generate(prompts.GRADE_SYSTEM, user, json_schema=LLM_GRADE_SCHEMA, temperature=0.0))
            score = min((0.0, 0.5, 1.0), key=lambda s: abs(s - float(data.get("score", 0))))
            return Grade(score, str(data.get("feedback", "")).strip(), [str(m) for m in data.get("missing", [])][:5])
        except (ValueError, TypeError, AttributeError):
            continue
    return Grade(0, "Automatic grading failed for this answer: compare it with the model answer below.")
