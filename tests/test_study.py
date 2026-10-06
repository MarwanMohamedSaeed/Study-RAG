import json

import pytest
from pydantic import ValidationError

from core import exam, store, study
from core.schemas import MCQ, ConceptMap, GlossaryTerm


@pytest.fixture
def db(tmp_path, monkeypatch):
    from core import config
    path = str(tmp_path / "study.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


@pytest.fixture
def lecture(indexed, chroma, monkeypatch):
    import core.retriever as r
    monkeypatch.setattr(r, "get_client", lambda *a, **k: chroma)
    return indexed


# ---------------------------------------------------------------- glossary rules
TERM = {"term": "UDP", "arabic": "بروتوكول UDP", "definition_en": "A connectionless transport protocol.",
        "definition_ar": "بروتوكول نقل بدون اتصال.", "page": 3}


def test_glossary_term_valid():
    assert GlossaryTerm.model_validate(TERM).ref == "p.3"


@pytest.mark.parametrize("bad", [{**TERM, "arabic": "UDP protocol"},          # translation not in Arabic
                                 {**TERM, "definition_ar": "connectionless"},  # Arabic definition in English
                                 {**TERM, "definition_en": "short"}])
def test_glossary_term_rejected(bad):
    with pytest.raises(ValidationError):
        GlossaryTerm.model_validate(bad)


def test_parse_glossary_keeps_only_valid_terms():
    raw = json.dumps({"terms": [TERM, {**TERM, "arabic": "no arabic here"}]})
    assert [t.term for t in study.parse_glossary(raw)] == ["UDP"]
    assert study.parse_glossary("not json") == []


# ---------------------------------------------------------------- concept map rules
def test_concept_map_drops_bad_edges():
    cmap = ConceptMap.model_validate({
        "nodes": [{"id": "tcp", "label": "TCP"}, {"id": "udp", "label": "UDP"},
                  {"id": "port", "label": "Port number", "page": 2}, {"id": "tcp", "label": "dup"}],
        "edges": [{"source": "tcp", "target": "port", "label": "uses"},
                  {"source": "udp", "target": "port", "label": "uses"},
                  {"source": "tcp", "target": "port", "label": "again"},      # duplicate pair
                  {"source": "tcp", "target": "tcp", "label": "self"},        # self loop
                  {"source": "udp", "target": "ghost", "label": "missing"}]})  # unknown node
    assert [n.id for n in cmap.nodes] == ["tcp", "udp", "port"]
    assert [(e.source, e.target) for e in cmap.edges] == [("tcp", "port"), ("udp", "port")]
    dot = study.to_dot(cmap)
    assert dot.startswith("digraph") and '"tcp" -> "port"' in dot and "(p.2)" in dot
    assert "flowchart LR" in study.to_mermaid(cmap)


def test_concept_map_isolated_and_connected_only():
    cmap = ConceptMap.model_validate({
        "nodes": [{"id": i, "label": f"Concept {i}"} for i in "abcde"],
        "edges": [{"source": "a", "target": "b", "label": "x"}, {"source": "b", "target": "c", "label": "y"}]})
    assert cmap.isolated() == ["d", "e"]
    assert [n.id for n in cmap.connected_only().nodes] == ["a", "b", "c"]


def test_concept_map_retries_when_concepts_are_loose(lecture, db, monkeypatch):
    loose = json.dumps({"nodes": [{"id": i, "label": f"Concept {i}"} for i in "abcdef"],
                        "edges": [{"source": "a", "target": "b", "label": "x"}]})
    good = json.dumps({"nodes": [{"id": i, "label": f"Concept {i}"} for i in "abcd"],
                       "edges": [{"source": a, "target": b, "label": "x"} for a, b in ["ab", "bc", "cd"]]})
    prompts_seen, replies = [], iter([loose, good])
    real = study.llm.generate
    monkeypatch.setattr(study.llm, "generate", lambda s, u, json_schema=None, **k:
                        (prompts_seen.append(u), next(replies))[1] if json_schema else real(s, u, **k))
    cmap = study.concept_map(lecture.doc_id, "x.pdf", refresh=True)
    assert len(cmap.edges) == 3 and "no links: c, d, e, f" in prompts_seen[1]


def test_concept_map_needs_links():
    with pytest.raises(ValidationError):
        ConceptMap.model_validate({"nodes": [{"id": "a", "label": "A1"}, {"id": "b", "label": "B1"},
                                             {"id": "c", "label": "C1"}], "edges": []})


# ---------------------------------------------------------------- tools on the sample lecture (fake LLM)
def test_cheat_sheet_is_cited_and_cached(lecture, db, monkeypatch):
    sheet = study.cheat_sheet(lecture.doc_id, "networks_lecture.pdf")
    assert "## Key ideas" in sheet and "[p." in sheet
    monkeypatch.setattr(study.llm, "generate", lambda *a, **k: pytest.fail("should come from the cache"))
    assert study.cheat_sheet(lecture.doc_id, "networks_lecture.pdf") == sheet


def test_long_material_uses_notes_first(lecture, db, monkeypatch):
    calls = []
    real = study.llm.generate
    monkeypatch.setattr(study, "SINGLE_PASS_CHARS", 2000)
    monkeypatch.setattr(study, "NOTES_BATCH_CHARS", 3000)
    monkeypatch.setattr(study.llm, "generate", lambda s, *a, **k: calls.append(s[:20]) or real(s, *a, **k))
    study.cheat_sheet(lecture.doc_id, "x.pdf", refresh=True)
    assert len(calls) > 2 and all("study notes" in c or "take study" in c.lower() for c in calls[:-1])


def test_page_text_rebuilds_page_without_overlap(lecture):
    ref, text = study.page_text(lecture.doc_id, 4)
    assert ref == "p.4" and "three duplicate ACKs" in text
    assert text.count("TCP uses a single retransmission timer") == 1


def test_glossary_and_concept_map(lecture, db):
    terms = study.glossary(lecture.doc_id, max_terms=5)
    assert 0 < len(terms) <= 5 and all(t.page >= 1 for t in terms)
    cmap = study.concept_map(lecture.doc_id, "networks_lecture.pdf")
    assert cmap is not None and len(cmap.edges) >= 1


def test_notes_store_roundtrip(db):
    key = store.note_key("cheatsheet", ["b", "a"], lang="ar", pages=None)
    assert key == "cheatsheet|a,b|lang=ar"
    assert store.get_note(key) is None
    store.save_note(key, "cheatsheet", ["a", "b"], "# notes")
    assert store.get_note(key) == "# notes"


# ---------------------------------------------------------------- exam
def test_split_counts_always_sum_to_n():
    for n in range(5, 41):
        for mix in exam.MIXES.values():
            assert sum(exam.split_counts(n, mix).values()) == n
    assert exam.split_counts(10, {"easy": .3, "medium": .5, "hard": .2}) == {"easy": 3, "medium": 5, "hard": 2}


def test_build_exam_and_report(lecture):
    qs, levels = exam.build_exam([lecture.doc_id], 6, {"easy": .5, "medium": .5, "hard": 0}, seed=3)
    assert len(qs) == len(levels) and set(levels) <= {"easy", "medium"}
    answers = [q.correct if i % 2 == 0 else None for i, q in enumerate(qs)]
    rep = exam.report(qs, levels, answers)
    assert rep["score"] == (len(qs) + 1) // 2 and rep["unanswered"] == len(qs) // 2
    assert sum(r["total"] for r in rep["by_level"]) == len(qs)


def test_exam_attempt_saved_with_mode(db):
    q = MCQ(question="Which port does DNS use?", options=["53", "80", "22", "443"], correct="A",
            explanation="DNS uses 53.", source_page=2, source_unit="slide")
    store.record_attempt([q, q], ["A", None], ["d"], ["x.pptx"], mode="exam", duration_s=95,
                         difficulties=["easy", "hard"])
    [a] = store.list_attempts()
    assert (a["mode"], a["duration_s"]) == ("exam", 95)
    assert store.page_stats()[0]["unit"] == "slide"
