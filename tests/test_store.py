import sqlite3

import pytest

from core import store
from core.schemas import MCQ


def q(page: int, correct: str = "A", doc: str = "doc_x", text: str = "") -> MCQ:
    return MCQ(question=text or f"Question about page {page}?", options=["one", "two", "three", "four"],
               correct=correct, explanation="Because.", source_page=page, source_doc=doc, source_file="x.pdf")


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "progress.db")


def test_migrations_run_once_and_set_version(db):
    store.connect(db).close()
    store.connect(db).close()  # second open must not re-run CREATE TABLE
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(store.MIGRATIONS)


def test_record_and_read_back(db):
    qs = [q(2), q(3, "B"), q(3, "C")]
    attempt = store.record_attempt(qs, ["A", "B", None], ["doc_x"], ["x.pdf"], "easy", "UDP", path=db)
    [row] = store.list_attempts(path=db)
    assert row["id"] == attempt and row["score"] == 2 and row["n_questions"] == 3
    assert row["doc_names"] == ["x.pdf"] and row["topic"] == "UDP"
    results = store.attempt_results(attempt, path=db)
    assert [r["chosen"] for r in results] == ["A", "B", None]
    assert MCQ.model_validate_json(results[1]["mcq_json"]) == qs[1]  # full question kept for re-asking


def test_totals_and_weakest_pages_first(db):
    store.record_attempt([q(2), q(3), q(3)], ["A", "B", "B"], ["doc_x"], ["x.pdf"], path=db)
    store.record_attempt([q(2), q(3)], ["A", "A"], ["doc_x"], ["x.pdf"], path=db)
    t = store.totals(path=db)
    assert (t["quizzes"], t["answered"], t["correct"]) == (2, 5, 3)
    stats = store.page_stats(path=db)
    assert [(s["page"], s["answered"], s["correct"]) for s in stats] == [(3, 3, 1), (2, 2, 2)]


def test_page_stats_filters_by_document(db):
    store.record_attempt([q(1, doc="doc_a"), q(1, doc="doc_b")], ["A", "B"], ["doc_a", "doc_b"], ["a", "b"], path=db)
    assert {s["doc_id"] for s in store.page_stats(["doc_b"], path=db)} == {"doc_b"}


def test_empty_db_totals(db):
    assert store.totals(path=db)["accuracy"] is None


def test_answers_must_match_questions(db):
    with pytest.raises(ValueError):
        store.record_attempt([q(1)], [], ["doc_x"], ["x.pdf"], path=db)
