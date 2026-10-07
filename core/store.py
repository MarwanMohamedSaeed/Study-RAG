"""Local progress database (SQLite): quiz attempts and per-question results.

Schema changes are numbered MIGRATIONS; SQLite's `PRAGMA user_version` records how many
have run, so later phases can add tables without losing existing history.
A connection is opened per call, which keeps it safe under Streamlit's multiple threads.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from core import config
from core.schemas import MCQ

MIGRATIONS = [
    # 1: quiz history
    """
    CREATE TABLE quiz_attempts (
        id          INTEGER PRIMARY KEY,
        created_at  TEXT    NOT NULL,          -- UTC ISO-8601
        doc_ids     TEXT    NOT NULL,          -- JSON list of Chroma collection names
        doc_names   TEXT    NOT NULL,          -- JSON list of file names (for display)
        difficulty  TEXT,
        topic       TEXT,
        n_questions INTEGER NOT NULL,
        score       INTEGER NOT NULL
    );
    CREATE TABLE question_results (
        id          INTEGER PRIMARY KEY,
        attempt_id  INTEGER NOT NULL REFERENCES quiz_attempts(id) ON DELETE CASCADE,
        doc_id      TEXT,
        filename    TEXT,
        source_page INTEGER,
        question    TEXT    NOT NULL,
        correct     TEXT    NOT NULL,
        chosen      TEXT,                      -- NULL = left unanswered
        is_correct  INTEGER NOT NULL,
        mcq_json    TEXT    NOT NULL           -- full question, so it can be re-asked later
    );
    CREATE INDEX ix_results_doc_page ON question_results(doc_id, source_page);
    """,
    # 2: exam mode + slide/part units + cache for generated study material
    """
    ALTER TABLE quiz_attempts ADD COLUMN mode TEXT NOT NULL DEFAULT 'quiz';   -- quiz | exam
    ALTER TABLE quiz_attempts ADD COLUMN duration_s INTEGER;                  -- time taken (exams)
    ALTER TABLE question_results ADD COLUMN source_unit TEXT NOT NULL DEFAULT 'page';
    ALTER TABLE question_results ADD COLUMN difficulty TEXT;                  -- per question (exams mix them)
    CREATE TABLE study_notes (
        key         TEXT PRIMARY KEY,          -- kind + documents + options, see note_key()
        kind        TEXT NOT NULL,             -- cheatsheet | glossary | conceptmap | explain
        doc_ids     TEXT NOT NULL,             -- JSON list
        content     TEXT NOT NULL,             -- markdown or JSON
        created_at  TEXT NOT NULL
    );
    """,
    # 3: question types with partial credit + flashcards for spaced repetition
    """
    ALTER TABLE question_results ADD COLUMN kind TEXT NOT NULL DEFAULT 'mcq';   -- mcq | tf | fill | short
    ALTER TABLE question_results ADD COLUMN score REAL;                        -- 0, 0.5 or 1 (NULL before v3)
    CREATE TABLE flashcards (
        id          INTEGER PRIMARY KEY,
        doc_id      TEXT,
        filename    TEXT,
        front       TEXT    NOT NULL,
        back        TEXT    NOT NULL,
        source_page INTEGER,
        source_unit TEXT    NOT NULL DEFAULT 'page',
        origin      TEXT    NOT NULL,          -- glossary | generated | mistake
        created_at  TEXT    NOT NULL,
        -- SM-2 state (see core/srs.py)
        ease        REAL    NOT NULL DEFAULT 2.5,
        interval    INTEGER NOT NULL DEFAULT 0,  -- days
        reps        INTEGER NOT NULL DEFAULT 0,  -- successful reviews in a row
        lapses      INTEGER NOT NULL DEFAULT 0,
        due         TEXT    NOT NULL,            -- ISO date
        last_review TEXT,
        UNIQUE (doc_id, front)
    );
    CREATE INDEX ix_cards_due ON flashcards(due);
    """,
]


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        with conn:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {i}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_attempt(questions: list, answers: list[str | None], doc_ids: list[str],
                   doc_names: list[str], difficulty: str | None = None, topic: str | None = None,
                   path: str | None = None, mode: str = "quiz", duration_s: int | None = None,
                   difficulties: list[str] | None = None, scores: list[float] | None = None) -> int:
    """Save one submitted quiz or exam. Returns the attempt id.
    `difficulties` gives a per-question level when an exam mixes them; `scores` (0 / 0.5 / 1 per
    question) comes from core.grading - without it, an answer scores 1 if it equals the MCQ letter."""
    if len(questions) != len(answers):
        raise ValueError("one answer (or None) per question is required")
    per_q = difficulties or [difficulty] * len(questions)
    scores = scores if scores is not None else [float(a == getattr(q, "correct", object())) for q, a in zip(questions, answers)]
    with closing(connect(path)) as conn, conn:
        cur = conn.execute(
            "INSERT INTO quiz_attempts (created_at, doc_ids, doc_names, difficulty, topic, n_questions, score, "
            "mode, duration_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), json.dumps(doc_ids), json.dumps(doc_names, ensure_ascii=False), difficulty, topic or None,
             len(questions), sum(scores), mode, duration_s))
        attempt_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO question_results (attempt_id, doc_id, filename, source_page, source_unit, question, "
            "correct, chosen, is_correct, difficulty, mcq_json, kind, score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(attempt_id, q.source_doc or None, q.source_file or None, q.source_page, q.source_unit, q.question,
              getattr(q, "correct", q.correct_text)[:500], a, int(s >= 0.5), d, q.model_dump_json(),
              getattr(q, "kind", "mcq"), s)
             for q, a, d, s in zip(questions, answers, per_q, scores)])
    return attempt_id


def mistakes(doc_ids: list[str] | None = None, limit: int = 50, path: str | None = None) -> list:
    """Questions whose MOST RECENT answer was wrong: once you get one right, it leaves the list."""
    from core.schemas import load_question
    sql = ("SELECT mcq_json FROM question_results WHERE id IN "
           "(SELECT MAX(id) FROM question_results GROUP BY question) AND is_correct = 0")
    params: list = []
    if doc_ids:
        sql += f" AND doc_id IN ({','.join('?' * len(doc_ids))})"
        params += doc_ids
    with closing(connect(path)) as conn:
        rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", params + [limit]).fetchall()
    return [load_question(r["mcq_json"]) for r in rows]


# ---------------------------------------------------------------- flashcards
def add_cards(cards: list[dict], origin: str, path: str | None = None) -> int:
    """Insert cards (dicts with doc_id, filename, front, back, source_page, source_unit).
    Cards with the same front for the same document are skipped. Returns how many were added."""
    today = datetime.now().date().isoformat()
    with closing(connect(path)) as conn, conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO flashcards (doc_id, filename, front, back, source_page, source_unit, origin, "
            "created_at, due) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(c.get("doc_id"), c.get("filename"), c["front"].strip(), c["back"].strip(), c.get("source_page"),
              c.get("source_unit", "page"), origin, _now(), today) for c in cards])
        return conn.total_changes - before


def list_cards(doc_ids: list[str] | None = None, due_only: bool = False, limit: int = 1000,
               path: str | None = None) -> list[dict]:
    sql, params = "SELECT * FROM flashcards WHERE 1=1", []
    if doc_ids:
        sql += f" AND doc_id IN ({','.join('?' * len(doc_ids))})"
        params += doc_ids
    if due_only:
        sql += " AND due <= ?"
        params.append(datetime.now().date().isoformat())
    with closing(connect(path)) as conn:
        # unseen cards first, then cards you just failed ("Again"), oldest review first
        return [dict(r) for r in conn.execute(sql + " ORDER BY due, last_review IS NOT NULL, last_review, id LIMIT ?",
                                              params + [limit]).fetchall()]


def update_card(card_id: int, ease: float, interval: int, reps: int, lapses: int, due: str,
                path: str | None = None) -> None:
    with closing(connect(path)) as conn, conn:
        conn.execute("UPDATE flashcards SET ease = ?, interval = ?, reps = ?, lapses = ?, due = ?, last_review = ? "
                     "WHERE id = ?", (ease, interval, reps, lapses, due, _now(), card_id))


def delete_cards(ids: list[int], path: str | None = None) -> None:
    with closing(connect(path)) as conn, conn:
        conn.executemany("DELETE FROM flashcards WHERE id = ?", [(i,) for i in ids])


def reviewed_today(path: str | None = None) -> int:
    today = datetime.now().astimezone().date().isoformat()
    with closing(connect(path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM flashcards WHERE last_review IS NOT NULL AND "
                            "date(last_review, 'localtime') = ?", (today,)).fetchone()[0]


# ---------------------------------------------------------------- cached study material
def note_key(kind: str, doc_ids: list[str], **options) -> str:
    opts = ",".join(f"{k}={v}" for k, v in sorted(options.items()) if v not in (None, ""))
    return f"{kind}|{','.join(sorted(doc_ids))}|{opts}"


def get_note(key: str, path: str | None = None) -> str | None:
    with closing(connect(path)) as conn:
        row = conn.execute("SELECT content FROM study_notes WHERE key = ?", (key,)).fetchone()
    return row["content"] if row else None


def latest_note(kind: str, doc_id: str, path: str | None = None) -> str | None:
    """Most recent cached result of a tool for one document, whatever options it was made with."""
    with closing(connect(path)) as conn:
        row = conn.execute("SELECT content FROM study_notes WHERE kind = ? AND doc_ids = ? ORDER BY created_at DESC "
                           "LIMIT 1", (kind, json.dumps([doc_id]))).fetchone()
    return row["content"] if row else None


def save_note(key: str, kind: str, doc_ids: list[str], content: str, path: str | None = None) -> None:
    with closing(connect(path)) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO study_notes (key, kind, doc_ids, content, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (key, kind, json.dumps(sorted(doc_ids)), content, _now()))


def list_attempts(limit: int = 100, path: str | None = None) -> list[dict]:
    with closing(connect(path)) as conn:
        rows = conn.execute("SELECT * FROM quiz_attempts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "doc_ids": json.loads(r["doc_ids"]), "doc_names": json.loads(r["doc_names"])} for r in rows]


def attempt_results(attempt_id: int, path: str | None = None) -> list[dict]:
    with closing(connect(path)) as conn:
        rows = conn.execute("SELECT * FROM question_results WHERE attempt_id = ? ORDER BY id",
                            (attempt_id,)).fetchall()
    return [dict(r) for r in rows]


def totals(path: str | None = None) -> dict:
    with closing(connect(path)) as conn:
        r = conn.execute("SELECT COUNT(DISTINCT attempt_id) AS quizzes, COUNT(*) AS answered, "
                         "COALESCE(SUM(is_correct), 0) AS correct FROM question_results").fetchone()
    return {"quizzes": r["quizzes"], "answered": r["answered"], "correct": r["correct"],
            "accuracy": (r["correct"] / r["answered"]) if r["answered"] else None}


def page_stats(doc_ids: list[str] | None = None, path: str | None = None) -> list[dict]:
    """Accuracy per (document, page), weakest first. The basis for weak-spot tracking."""
    sql = ("SELECT doc_id, filename, source_page AS page, source_unit AS unit, COUNT(*) AS answered, "
           "SUM(is_correct) AS correct FROM question_results WHERE source_page IS NOT NULL")
    params: list = []
    if doc_ids:
        sql += f" AND doc_id IN ({','.join('?' * len(doc_ids))})"
        params += doc_ids
    sql += " GROUP BY doc_id, filename, source_page, source_unit"
    with closing(connect(path)) as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["accuracy"] = r["correct"] / r["answered"]
    return sorted(rows, key=lambda r: (r["accuracy"], -r["answered"], r["page"]))
