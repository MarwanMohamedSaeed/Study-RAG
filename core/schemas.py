"""Pydantic models that validate LLM output: questions of every type, glossary, concept map, cards."""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

LETTERS = ["A", "B", "C", "D"]
# Distractors like these are lazy and make the "exactly one correct answer" property ambiguous.
BANNED_OPTIONS = {"all of the above", "none of the above", "both a and b", "all of these", "none of these"}
_PREFIX = re.compile(r"^\s*(?:\(?[A-Da-d][\).:\-]|[A-Da-d]\s*-)\s+")


class Sourced(BaseModel):
    """Where a question or card comes from. Set by the code (attribute_pages), never trusted from the LLM."""
    source_page: int = Field(ge=1)
    source_unit: str = "page"   # page | slide | part
    source_doc: str = ""
    source_file: str = ""

    @property
    def source_ref(self) -> str:
        """'p.3', 'slide 3' or 'part 3'."""
        return f"p.{self.source_page}" if self.source_unit == "page" else f"{self.source_unit} {self.source_page}"


class MCQ(Sourced):
    kind: Literal["mcq"] = "mcq"
    question: str = Field(min_length=8)
    options: list[str] = Field(min_length=4, max_length=4)
    correct: Literal["A", "B", "C", "D"]
    explanation: str = Field(min_length=3)

    @field_validator("question", "explanation")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("question")
    @classmethod
    def _no_inline_options(cls, v: str) -> str:
        # small models sometimes paste "A) ... B) ..." into the question text as well
        if len(re.findall(r"(?:^|\n|\s)\(?[A-D]\)\s", v)) >= 2:
            raise ValueError("question text must not contain the options")
        return v

    @field_validator("options")
    @classmethod
    def _clean_options(cls, v: list[str]) -> list[str]:
        cleaned = [_PREFIX.sub("", str(o)).strip() for o in v]
        if any(not o for o in cleaned):
            raise ValueError("options must be non-empty")
        if len({o.casefold() for o in cleaned}) != 4:
            raise ValueError("options must be 4 distinct answers (exactly one correct)")
        if any(o.casefold().rstrip(".") in BANNED_OPTIONS for o in cleaned):
            raise ValueError("'all/none of the above' style options are not allowed")
        return cleaned

    @model_validator(mode="before")
    @classmethod
    def _normalize_correct(cls, data):
        """Accept 'a', 'A)', 'Option A' or the full option text and map it to a letter."""
        if isinstance(data, dict) and isinstance(data.get("correct"), str):
            c = data["correct"].strip()
            m = re.fullmatch(r"(?:option\s*)?\(?([A-Da-d])[\).:]?", c, flags=re.I)
            if m:
                data["correct"] = m.group(1).upper()
            elif isinstance(data.get("options"), list):
                texts = [_PREFIX.sub("", str(o)).strip().casefold() for o in data["options"]]
                if c.casefold() in texts:
                    data["correct"] = LETTERS[texts.index(c.casefold())]
        return data

    @model_validator(mode="after")
    def _no_giveaway(self):
        # a correct option far longer than every distractor gives the answer away
        distractors = [o for L, o in zip(LETTERS, self.options) if L != self.correct]
        if len(self.correct_text) > 2 * max(len(d) for d in distractors) + 10:
            raise ValueError("correct option is much longer than the distractors (giveaway)")
        return self

    @property
    def correct_text(self) -> str:
        return self.options[LETTERS.index(self.correct)]


class MCQBatch(BaseModel):
    questions: list[MCQ]


# =============================================================================
# Other question types. All expose .question, .correct_text and .explanation,
# so de-duplication, page attribution, storage and progress work for every type.
# =============================================================================
BLANK = "_____"
_BLANK_RE = re.compile(r"_{3,}|\[blank\]|\(blank\)|\.{4,}", flags=re.I)


class TrueFalse(Sourced):
    kind: Literal["tf"] = "tf"
    statement: str = Field(min_length=12)
    answer: bool
    explanation: str = Field(min_length=3)

    @field_validator("statement", "explanation")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("statement")
    @classmethod
    def _plain_statement(cls, v: str) -> str:
        if v.endswith("?"):
            raise ValueError("must be a statement, not a question")
        if re.match(r"(?i)^(true or false|t/f)\b", v):
            raise ValueError("write only the statement, without 'True or false'")
        return v

    @property
    def question(self) -> str:
        return self.statement

    @property
    def correct_text(self) -> str:
        return "True" if self.answer else "False"


class FillBlank(Sourced):
    kind: Literal["fill"] = "fill"
    sentence: str = Field(min_length=12)
    answer: str = Field(min_length=1, max_length=60)
    alternatives: list[str] = Field(default_factory=list, max_length=5)
    explanation: str = Field(default="", max_length=400)

    @field_validator("sentence")
    @classmethod
    def _one_blank(cls, v: str) -> str:
        v = _BLANK_RE.sub(BLANK, " ".join(v.split()))
        if v.count(BLANK) != 1:
            raise ValueError(f"the sentence must contain exactly one blank written as {BLANK}")
        return v

    @field_validator("answer")
    @classmethod
    def _short_answer(cls, v: str) -> str:
        v = v.strip().strip(".")
        if len(v.split()) > 5:
            raise ValueError("the blank must be filled by at most 5 words")
        return v

    @model_validator(mode="after")
    def _answer_not_visible(self):
        if self.answer.casefold() in self.sentence.casefold():
            raise ValueError("the answer must not appear in the sentence")
        return self

    @property
    def question(self) -> str:
        return self.sentence

    @property
    def correct_text(self) -> str:
        return self.answer


class ShortAnswer(Sourced):
    kind: Literal["short"] = "short"
    question: str = Field(min_length=10)
    reference: str = Field(min_length=10)          # model answer, from the lecture
    key_points: list[str] = Field(min_length=1, max_length=5)
    explanation: str = ""

    @field_validator("question")
    @classmethod
    def _is_question(cls, v: str) -> str:
        v = " ".join(v.split())
        if not (v.endswith("?") or re.match(r"(?i)^(explain|describe|compare|why|how|what|define|list|give)\b", v)):
            raise ValueError("must be a question or an instruction such as 'Explain ...'")
        return v

    @property
    def correct_text(self) -> str:
        return self.reference


Question = MCQ | TrueFalse | FillBlank | ShortAnswer
QUESTION_MODELS = {"mcq": MCQ, "tf": TrueFalse, "fill": FillBlank, "short": ShortAnswer}
QUESTION_TYPES = {"mcq": "Multiple choice", "tf": "True / False", "fill": "Fill in the blank", "short": "Short answer"}


def load_question(data: dict | str) -> Question:
    """Rebuild a stored question of any type (rows saved before Phase 2 have no 'kind': they are MCQs)."""
    if isinstance(data, str):
        data = json.loads(data)
    return QUESTION_MODELS[data.get("kind", "mcq")].model_validate(data)


class Flashcard(BaseModel):
    front: str = Field(min_length=5, max_length=300)
    back: str = Field(min_length=2, max_length=600)
    page: int = Field(default=1, ge=1)

    @field_validator("front", "back")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _not_same(self):
        if self.front.casefold() == self.back.casefold():
            raise ValueError("front and back must differ")
        return self


LLM_VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer"},
                    "correct_options": {"type": "array", "items": {"type": "string", "enum": LETTERS}},
                },
                "required": ["number", "correct_options"],
            },
        }
    },
    "required": ["reviews"],
}

# =============================================================================
# Study tools: glossary and concept map
# =============================================================================
_ARABIC = re.compile(r"[؀-ۿ]")


class GlossaryTerm(BaseModel):
    term: str = Field(min_length=2, max_length=60)
    arabic: str = Field(min_length=1, max_length=80)
    definition_en: str = Field(min_length=10)
    definition_ar: str = Field(min_length=5)
    page: int = Field(ge=1)
    unit: str = "page"            # set by the code, like MCQ.source_unit

    @field_validator("term", "arabic", "definition_en", "definition_ar")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("arabic", "definition_ar")
    @classmethod
    def _must_be_arabic(cls, v: str) -> str:
        if not _ARABIC.search(v):
            raise ValueError("must be written in Arabic")
        return v

    @property
    def ref(self) -> str:
        return f"p.{self.page}" if self.unit == "page" else f"{self.unit} {self.page}"


class ConceptNode(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=2, max_length=60)
    page: int | None = None


class ConceptEdge(BaseModel):
    source: str
    target: str
    label: str = Field(default="", max_length=40)


class ConceptMap(BaseModel):
    nodes: list[ConceptNode] = Field(min_length=3, max_length=25)
    edges: list[ConceptEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _clean_graph(self):
        # unique ids; keep edges that join two different, existing nodes; no duplicate edges
        seen, nodes = set(), []
        for n in self.nodes:
            if n.id not in seen:
                seen.add(n.id)
                nodes.append(n)
        pairs, edges = set(), []
        for e in self.edges:
            if e.source in seen and e.target in seen and e.source != e.target and (e.source, e.target) not in pairs:
                pairs.add((e.source, e.target))
                edges.append(e)
        if len(nodes) < 3:
            raise ValueError("a concept map needs at least 3 distinct concepts")
        if not edges:
            raise ValueError("a concept map needs at least one valid link between concepts")
        self.nodes, self.edges = nodes, edges
        return self

    def isolated(self) -> list[str]:
        linked = {e.source for e in self.edges} | {e.target for e in self.edges}
        return [n.id for n in self.nodes if n.id not in linked]

    def connected_only(self) -> "ConceptMap":
        lonely = set(self.isolated())
        return self.model_copy(update={"nodes": [n for n in self.nodes if n.id not in lonely]})


LLM_GLOSSARY_SCHEMA = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"}, "arabic": {"type": "string"},
                    "definition_en": {"type": "string"}, "definition_ar": {"type": "string"},
                    "page": {"type": "integer"},
                },
                "required": ["term", "arabic", "definition_en", "definition_ar", "page"],
            },
        }
    },
    "required": ["terms"],
}

LLM_CONCEPT_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "label": {"type": "string"}, "page": {"type": "integer"}},
            "required": ["id", "label"]}},
        "edges": {"type": "array", "items": {"type": "object", "properties": {
            "source": {"type": "string"}, "target": {"type": "string"}, "label": {"type": "string"}},
            "required": ["source", "target", "label"]}},
    },
    "required": ["nodes", "edges"],
}

def _list_schema(key: str, props: dict, required: list[str]) -> dict:
    return {"type": "object", "required": [key], "properties": {key: {
        "type": "array", "items": {"type": "object", "properties": props, "required": required}}}}


_STR, _INT = {"type": "string"}, {"type": "integer"}
LLM_TF_SCHEMA = _list_schema("statements", {"statement": _STR, "answer": {"type": "boolean"}, "explanation": _STR,
                                            "source_page": _INT}, ["statement", "answer", "explanation", "source_page"])
LLM_FILL_SCHEMA = _list_schema("blanks", {"sentence": _STR, "answer": _STR, "alternatives": {"type": "array", "items": _STR},
                                          "explanation": _STR, "source_page": _INT},
                               ["sentence", "answer", "alternatives", "explanation", "source_page"])
LLM_SHORT_SCHEMA = _list_schema("short_answers", {"question": _STR, "reference": _STR,
                                                  "key_points": {"type": "array", "items": _STR}, "source_page": _INT},
                                ["question", "reference", "key_points", "source_page"])
LLM_TF_VERIFY_SCHEMA = _list_schema("judgements", {"number": _INT, "verdict": {"type": "string",
                                    "enum": ["true", "false", "not stated"]}}, ["number", "verdict"])
LLM_FILL_VERIFY_SCHEMA = _list_schema("fills", {"number": _INT, "answer": _STR}, ["number", "answer"])
LLM_CARDS_SCHEMA = _list_schema("cards", {"front": _STR, "back": _STR, "page": _INT}, ["front", "back", "page"])
LLM_GRADE_SCHEMA = {"type": "object", "required": ["score", "feedback", "missing"], "properties": {
    "score": {"type": "number", "enum": [0, 0.5, 1]}, "feedback": _STR, "missing": {"type": "array", "items": _STR}}}

# Lenient schema sent to the LLM (Ollama uses it for constrained decoding).
# Kept free of our custom validators so the grammar stays simple.
LLM_MCQ_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 4},
                    "correct": {"type": "string", "enum": LETTERS},
                    "explanation": {"type": "string"},
                    "source_page": {"type": "integer"},
                },
                "required": ["question", "options", "correct", "explanation", "source_page"],
            },
        }
    },
    "required": ["questions"],
}
