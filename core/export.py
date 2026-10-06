"""Quiz export: JSON, Markdown and printable PDF (questions first, answer key on a new page)."""
from __future__ import annotations

import html
import io
import json
from pathlib import Path

import pymupdf as fitz

from core.schemas import LETTERS, MCQ

# Fonts with Arabic glyphs, checked in order (Windows, Debian/Docker, macOS).
_FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),  # Docker image (fonts-dejavu-core)
    Path("/Library/Fonts/Arial Unicode.ttf"),
]


def to_json(questions: list[MCQ], title: str = "Quiz") -> str:
    return json.dumps({"title": title, "questions": [q.model_dump() for q in questions]},
                      ensure_ascii=False, indent=2)


def to_markdown(questions: list[MCQ], title: str = "Quiz") -> str:
    lines = [f"# {title}", "", f"_{len(questions)} questions_", ""]
    for i, q in enumerate(questions, 1):
        lines.append(f"**{i}. {q.question}**")
        lines.append("")
        lines += [f"- {L}) {o}" for L, o in zip(LETTERS, q.options)]
        lines.append("")
    lines += ["---", "", "## Answer key", ""]
    for i, q in enumerate(questions, 1):
        lines.append(f"{i}. **{q.correct}** ({q.correct_text}) - {q.explanation} _(p.{q.source_page})_")
    return "\n".join(lines) + "\n"


def _dir(text: str) -> str:
    return "rtl" if sum("\u0600" <= ch <= "\u06ff" for ch in text) > len(text) * 0.3 else "ltr"


def _html(questions: list[MCQ], title: str) -> str:
    e = html.escape
    parts = [f"<h1>{e(title)}</h1>", f"<p class='meta'>{len(questions)} questions &#183; Name: ____________________</p>"]
    for i, q in enumerate(questions, 1):
        opts = "".join(f"<p class='opt'>{L}) {e(o)}</p>" for L, o in zip(LETTERS, q.options))
        parts.append(f"<div class='q' dir='{_dir(q.question)}'><p><b>{i}. {e(q.question)}</b></p>{opts}</div>")
    parts.append("<h2 style='page-break-before: always'>Answer key</h2>")
    for i, q in enumerate(questions, 1):
        parts.append(f"<p class='key' dir='{_dir(q.explanation)}'><b>{i}. {q.correct}</b> &#8212; "
                     f"{e(q.explanation)} <i>(p.{q.source_page})</i></p>")
    return "".join(parts)


def to_pdf(questions: list[MCQ], title: str = "Quiz") -> bytes:
    font = next((p for p in _FONT_CANDIDATES if p.exists()), None)
    css = ("body {font-size: 11pt;} h1 {font-size: 18pt;} h2 {font-size: 15pt;} .meta {color: #555;}"
           ".q {margin-bottom: 10pt;} .opt {margin: 1pt 0 1pt 16pt;} .key {margin: 3pt 0;}")
    archive = None
    if font:
        archive = fitz.Archive(str(font.parent))
        css = f"@font-face {{font-family: qf; src: url({font.name});}} * {{font-family: qf;}} " + css
    story = fitz.Story(html=_html(questions, title), user_css=css, archive=archive)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    mediabox = fitz.paper_rect("a4")
    where = mediabox + (50, 50, -50, -50)
    more = True
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    # embed only the glyphs actually used (a full Arial is ~1 MB)
    doc = fitz.open("pdf", buf.getvalue())
    doc.subset_fonts()
    return doc.tobytes(garbage=4, deflate=True)
