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


def record_attempt(questions: list[MCQ], answers: list[str | None], doc_ids: list[str],
                   doc_names: list[str], difficulty: str | None = None, topic: str | None = None,
                   path: str | None = None, mode: str = "quiz", duration_s: int | None = None,
                   difficulties: list[str] | None = None) -> int:
    """Save one submitted quiz or exam. Returns the attempt id.
    `difficulties` gives a per-question level when an exam mixes them."""
    if len(questions) != len(answers):
        raise ValueError("one answer (or None) per question is required")
    per_q = difficulties or [difficulty] * len(questions)
    score = sum(a == q.correct for q, a in zip(questions, answers))
    with closing(connect(path)) as conn, conn:
        cur = conn.execute(
            "INSERT INTO quiz_attempts (created_at, doc_ids, doc_names, difficulty, topic, n_questions, score, "
            "mode, duration_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), json.dumps(doc_ids), json.dumps(doc_names, ensure_ascii=False), difficulty, topic or None,
             len(questions), score, mode, duration_s))
        attempt_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO question_results (attempt_id, doc_id, filename, source_page, source_unit, question, "
            "correct, chosen, is_correct, difficulty, mcq_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(attempt_id, q.source_doc or None, q.source_file or None, q.source_page, q.source_unit, q.question,
              q.correct, a, int(a == q.correct), d, q.model_dump_json())
             for q, a, d in zip(questions, answers, per_q)])
    return attempt_id


# ---------------------------------------------------------------- cached study material
def note_key(kind: str, doc_ids: list[str], **options) -> str:
    opts = ",".join(f"{k}={v}" for k, v in sorted(options.items()) if v not in (None, ""))
    return f"{kind}|{','.join(sorted(doc_ids))}|{opts}"


def get_note(key: str, path: str | None = None) -> str | None:
    with closing(connect(path)) as conn:
        row = conn.execute("SELECT content FROM study_notes WHERE key = ?", (key,)).fetchone()
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
