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
        lines.append(f"{i}. **{q.correct}** ({q.correct_text}) - {q.explanation} _({q.source_ref})_")
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
                     f"{e(q.explanation)} <i>({q.source_ref})</i></p>")
    return "".join(parts)


QUIZ_CSS = ("body {font-size: 11pt;} h1 {font-size: 18pt;} h2 {font-size: 15pt;} .meta {color: #555;}"
            ".q {margin-bottom: 10pt;} .opt {margin: 1pt 0 1pt 16pt;} .key {margin: 3pt 0;}")
NOTES_CSS = ("body {font-size: 10.5pt;} h1 {font-size: 17pt;} h2 {font-size: 13pt; margin-top: 10pt;} "
             "li {margin-bottom: 2pt;} table {border-collapse: collapse;} td, th {border: 1px solid #999; padding: 3pt;}")


def to_pdf(questions: list[MCQ], title: str = "Quiz") -> bytes:
    return html_to_pdf(_html(questions, title), QUIZ_CSS)


def markdown_to_pdf(md_text: str, title: str) -> bytes:
    """Cheat sheets and explanations: Markdown -> HTML -> paginated A4 PDF."""
    import markdown
    body = markdown.markdown(md_text, extensions=["tables", "sane_lists"])
    return html_to_pdf(f"<h1>{html.escape(title)}</h1><div dir='{_dir(md_text)}'>{body}</div>", NOTES_CSS)


def glossary_csv(terms) -> bytes:
    """UTF-8 with BOM so Excel shows the Arabic columns correctly."""
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Term", "Arabic", "Definition (English)", "Definition (Arabic)", "Source"])
    for t in terms:
        w.writerow([t.term, t.arabic, t.definition_en, t.definition_ar, t.ref])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def glossary_markdown(terms, title: str) -> str:
    rows = [f"| **{t.term}** | {t.arabic} | {t.definition_en} | {t.definition_ar} | {t.ref} |" for t in terms]
    return "\n".join([f"# {title}", "", "| Term | Arabic | Definition | التعريف | Source |",
                      "|---|---|---|---|---|", *rows, ""])


def html_to_pdf(html_text: str, css: str) -> bytes:
    font = next((p for p in _FONT_CANDIDATES if p.exists()), None)
    archive = None
    if font:
        archive = fitz.Archive(str(font.parent))
        css = f"@font-face {{font-family: qf; src: url({font.name});}} * {{font-family: qf;}} " + css
    story = fitz.Story(html=html_text, user_css=css, archive=archive)
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
