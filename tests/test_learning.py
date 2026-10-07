import io
import zipfile
from datetime import date

import pytest
from pydantic import ValidationError

from core import cards, grading, store
from core.export import anki_package, cards_csv, to_markdown, to_pdf
from core.mcq import generate_mixed, generate_quiz
from core.schemas import MCQ, FillBlank, ShortAnswer, TrueFalse, load_question
from core.srs import CardState, preview, review


@pytest.fixture
def db(tmp_path, monkeypatch):
    from core import config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "learn.db"))


@pytest.fixture
def lecture(indexed, chroma, monkeypatch):
    import core.retriever as r
    monkeypatch.setattr(r, "get_client", lambda *a, **k: chroma)
    return indexed


SRC = {"source_page": 3, "source_doc": "doc_x", "source_file": "x.pdf"}
TF = TrueFalse(statement="The UDP header is 8 bytes long.", answer=True, explanation="It is 8 bytes.", **SRC)
FILL = FillBlank(sentence="The UDP header is ____ bytes long.", answer="8", alternatives=["eight"], **SRC)
SHORT = ShortAnswer(question="Why is UDP used for DNS queries?",
                    reference="UDP has no connection setup delay and a small header, so short queries are fast.",
                    key_points=["no connection setup", "small header"], **SRC)
MC = MCQ(question="How long is the UDP header?", options=["8 bytes", "20 bytes", "16 bytes", "32 bytes"],
         correct="A", explanation="UDP header is 8 bytes.", **SRC)


# ---------------------------------------------------------------- question models
def test_fill_normalizes_blank_and_hides_answer():
    assert FILL.sentence == "The UDP header is _____ bytes long."
    with pytest.raises(ValidationError):   # answer visible in the sentence
        FillBlank(sentence="UDP header: 8 bytes, so _____ bytes.", answer="8", **SRC)
    with pytest.raises(ValidationError):   # two blanks
        FillBlank(sentence="_____ uses port _____ by default.", answer="DNS", **SRC)
    with pytest.raises(ValidationError):   # blank too long to be a term
        FillBlank(sentence="UDP is used because _____.", answer="it has no setup delay and small headers", **SRC)


@pytest.mark.parametrize("model, data", [
    (TrueFalse, {"statement": "Is UDP connectionless?", "answer": True, "explanation": "yes"}),
    (TrueFalse, {"statement": "True or false: UDP is reliable.", "answer": False, "explanation": "no"}),
    (ShortAnswer, {"question": "UDP header size.", "reference": "It is 8 bytes long.", "key_points": ["8"]}),
])
def test_bad_question_types_rejected(model, data):
    with pytest.raises(ValidationError):
        model.model_validate({**data, **SRC})


def test_load_question_dispatches_by_kind():
    for q in (MC, TF, FILL, SHORT):
        assert load_question(q.model_dump_json()) == q
    legacy = MC.model_dump()
    legacy.pop("kind")                    # rows saved before Phase 2
    assert isinstance(load_question(legacy), MCQ)


# ---------------------------------------------------------------- grading
@pytest.mark.parametrize("given, ok", [("8", True), (" eight ", True), ("8.", True), ("9", False), ("", False)])
def test_fill_matching(given, ok):
    assert grading.fill_matches(FILL, given) is ok


def test_fill_tolerates_small_typos_and_articles():
    q = FillBlank(sentence="OSPF computes routes with _____ algorithm.", answer="Dijkstra's", **SRC)
    assert grading.fill_matches(q, "dijkstras") and grading.fill_matches(q, "the Dijkstra's")
    assert not grading.fill_matches(q, "Bellman-Ford")


def test_grade_each_type():
    assert grading.grade(MC, "A").score == 1 and grading.grade(MC, "B").score == 0
    assert grading.grade(TF, "True").score == 1 and grading.grade(TF, "False").score == 0
    assert grading.grade(FILL, "eight").correct and grading.grade(SHORT, "  ").score == 0


def test_short_answer_graded_by_llm():
    good = grading.grade(SHORT, "Because UDP has no connection setup delay and a small header, queries are fast.")
    bad = grading.grade(SHORT, "Because of TCP.")
    assert good.score == 1 and bad.score == 0 and good.feedback


# ---------------------------------------------------------------- generation (fake LLM)
@pytest.mark.parametrize("kind, model", [("tf", TrueFalse), ("fill", FillBlank), ("short", ShortAnswer)])
def test_generate_each_type(lecture, kind, model):
    qs = generate_quiz([lecture.doc_id], 4, "easy", kind=kind, seed=1)
    assert 0 < len(qs) <= 4 and all(isinstance(q, model) for q in qs)
    assert all(q.source_doc == lecture.doc_id and q.source_page >= 1 for q in qs)


def test_true_false_blind_check_keeps_both_answers(lecture):
    qs = generate_quiz([lecture.doc_id], 4, "easy", kind="tf", seed=1)
    assert {q.answer for q in qs} == {True, False}


def test_fill_answer_must_be_in_the_excerpts():
    from core.mcq import grounded_fill
    chunks = [{"text": "TCP uses a three-way handshake. The minimum TCP header is 20 bytes."}]
    inside = FillBlank(sentence="The minimum TCP header is _____ bytes.", answer="twenty", alternatives=["20"], **SRC)
    outside = FillBlank(sentence="The UDP header is _____ bytes long.", answer="8", alternatives=["eight"], **SRC)
    assert grounded_fill([inside, outside], chunks) == [inside]


def test_mixed_quiz(lecture):
    qs = generate_mixed([lecture.doc_id], 6, "easy", ["mcq", "tf", "fill"], seed=2)
    assert {q.kind for q in qs} == {"mcq", "tf", "fill"}


def test_mixed_types_see_each_others_questions(lecture, monkeypatch):
    from core import mcq
    prompts_seen, real = [], mcq.llm.generate
    monkeypatch.setattr(mcq.llm, "generate", lambda s, u, *a, **k: (prompts_seen.append(u), real(s, u, *a, **k))[1])
    qs = generate_mixed([lecture.doc_id], 4, "easy", ["mcq", "tf"], seed=1, verify_answers=False)
    first_type_question = next(q for q in qs if q.kind == "mcq").question
    tf_prompts = [p for p in prompts_seen if "true/false statements" in p]
    assert tf_prompts and f"- {first_type_question}" in tf_prompts[0]


def test_focus_limits_pages(lecture):
    qs = generate_quiz([lecture.doc_id], 3, "easy", kind="tf", focus={(lecture.doc_id, 3)})
    assert qs and all(q.source_page == 3 for q in qs)


def test_focus_types_start_on_different_pages(lecture, monkeypatch):
    from core import mcq
    first_batches = []
    real = mcq._ask
    monkeypatch.setattr(mcq, "_ask", lambda spec, chunks, *a: (first_batches.append(chunks[0]["page"]), real(spec, chunks, *a))[1])
    focus = {(lecture.doc_id, p) for p in (1, 2, 3, 4, 5)}
    generate_mixed([lecture.doc_id], 3, "easy", ["mcq", "tf", "fill"], focus=focus, verify_answers=False)
    assert len(set(first_batches)) >= 2   # the three types don't all start on the same page


def test_export_mixed_types():
    md = to_markdown([MC, TF, FILL, SHORT], "Mixed")
    assert "- True" in md and "_____" in md and "**8** (also: eight)" in md and "key points:" in md
    assert len(to_pdf([MC, TF, FILL, SHORT], "Mixed")) > 1000


# ---------------------------------------------------------------- mistakes
def test_mistakes_leave_the_list_once_answered_right(db):
    store.record_attempt([MC, TF, FILL], ["B", "True", "nine"], ["doc_x"], ["x.pdf"],
                         scores=[0, 1, 0])
    assert {q.kind for q in store.mistakes()} == {"mcq", "fill"}
    store.record_attempt([MC], ["A"], ["doc_x"], ["x.pdf"], scores=[1])
    assert [q.kind for q in store.mistakes()] == ["fill"]


def test_partial_credit_counts(db):
    store.record_attempt([SHORT, TF], ["half right", "False"], ["doc_x"], ["x.pdf"], scores=[0.5, 0])
    [a] = store.list_attempts()
    assert a["score"] == 0.5
    assert store.totals()["correct"] == 1   # half credit counts as correct in the statistics


# ---------------------------------------------------------------- spaced repetition (SM-2)
def test_sm2_intervals_grow():
    s, d = review(CardState(), 4, date(2026, 1, 1))
    assert (s.interval, s.reps, d) == (1, 1, date(2026, 1, 2))
    s, _ = review(s, 4)
    assert s.interval == 6
    s, _ = review(s, 4)
    assert s.interval == 15 and s.ease == 2.5     # 6 x 2.5


def test_sm2_lapse_and_ease_floor():
    s = CardState(ease=1.4, interval=30, reps=5)
    s, due = review(s, 1, date(2026, 1, 1))
    assert (s.interval, s.reps, s.lapses, due) == (0, 0, 1, date(2026, 1, 1)) and s.ease == 1.3  # again today
    assert review(CardState(), 5)[0].ease > 2.5 and review(CardState(), 3)[0].ease < 2.5


def test_preview_labels():
    p = preview(CardState(ease=2.5, interval=6, reps=2))
    assert p["Again"] == "again today" and p["Good"] == "15d" and p["Easy"] == "20d"
    new = preview(CardState())
    assert (new["Hard"], new["Good"], new["Easy"]) == ("1d", "1d", "4d")


# ---------------------------------------------------------------- flashcards
def test_cards_from_mistakes_dedupe_and_due(db):
    store.record_attempt([MC, FILL], ["B", "x"], ["doc_x"], ["x.pdf"], scores=[0, 0])
    assert cards.from_mistakes() == 2
    assert cards.from_mistakes() == 0               # same fronts are not added twice
    due = store.list_cards(due_only=True)
    assert len(due) == 2 and "A) 8 bytes" in [c for c in due if "UDP header?" in c["front"]][0]["front"]
    store.update_card(due[0]["id"], 2.5, 6, 2, 0, "2999-01-01")
    assert len(store.list_cards(due_only=True)) == 1


def test_glossary_cards_reuse_existing_glossary(lecture, db, monkeypatch):
    from core import study
    study.glossary(lecture.doc_id, max_terms=10)              # built earlier with a different size
    monkeypatch.setattr(study.llm, "generate", lambda *a, **k: pytest.fail("glossary should come from the cache"))
    assert cards.from_glossary(lecture.doc_id, "networks_lecture.pdf") > 0


def test_generated_cards(lecture, db):
    n = cards.generate_cards(lecture.doc_id, "networks_lecture.pdf", n=5)
    stored = store.list_cards([lecture.doc_id])
    assert 0 < n <= 5 and len(stored) == n and all(c["origin"] == "generated" for c in stored)


def test_anki_and_csv_export():
    data = [{"front": "UDP header size?", "back": "8 bytes", "doc_id": "d", "filename": "x.pdf",
             "source_page": 3, "source_unit": "page"},
            {"front": "ما هو TCP؟", "back": "بروتوكول موثوق", "doc_id": "d", "filename": "x.pdf",
             "source_page": 1, "source_unit": "slide"}]
    apkg = anki_package(data, "StudyRAG::x")
    assert zipfile.is_zipfile(io.BytesIO(apkg))
    csv_text = cards_csv(data).decode("utf-8-sig")
    assert "x.pdf p.3" in csv_text and "x.pdf slide 1" in csv_text and "ما هو TCP؟" in csv_text
