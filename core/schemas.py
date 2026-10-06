"""Pydantic models for MCQ output validation."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

LETTERS = ["A", "B", "C", "D"]
# Distractors like these are lazy and make the "exactly one correct answer" property ambiguous.
BANNED_OPTIONS = {"all of the above", "none of the above", "both a and b", "all of these", "none of these"}
_PREFIX = re.compile(r"^\s*(?:\(?[A-Da-d][\).:\-]|[A-Da-d]\s*-)\s+")


class MCQ(BaseModel):
    question: str = Field(min_length=8)
    options: list[str] = Field(min_length=4, max_length=4)
    correct: Literal["A", "B", "C", "D"]
    explanation: str = Field(min_length=3)
    source_page: int = Field(ge=1)
    # Filled in by mcq.attribute_pages (never by the LLM): which document the page belongs to.
    source_unit: str = "page"   # page | slide | part
    source_doc: str = ""
    source_file: str = ""

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
    def source_ref(self) -> str:
        """'p.3', 'slide 3' or 'part 3'."""
        return f"p.{self.source_page}" if self.source_unit == "page" else f"{self.source_unit} {self.source_page}"

    @property
    def correct_text(self) -> str:
        return self.options[LETTERS.index(self.correct)]


class MCQBatch(BaseModel):
    questions: list[MCQ]


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
