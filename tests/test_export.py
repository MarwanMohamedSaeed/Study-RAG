import json

import pymupdf

from core.export import to_json, to_markdown, to_pdf
from core.schemas import MCQ

QS = [
    MCQ(question="What is the length of the UDP header in bytes?", options=["20 bytes", "32 bytes", "8 bytes", "16 bytes"],
        correct="C", explanation="The UDP header is 8 bytes.", source_page=3),
    MCQ(question="متى يُفضَّل استخدام بروتوكول UDP؟",
        options=["عندما تكون الموثوقية أهم", "عندما يكون التأخير المنخفض أهم", "عند نقل الملفات", "عند المصافحة الثلاثية"],
        correct="B", explanation="عندما يكون التأخير المنخفض أهم من فقدان بعض الحزم.", source_page=10),
]


def test_json_roundtrip():
    data = json.loads(to_json(QS, "T"))
    assert [MCQ.model_validate(q) for q in data["questions"]] == QS


def test_markdown_has_answer_key_after_questions():
    md = to_markdown(QS, "T")
    assert md.index("## Answer key") > md.index("D) 16 bytes")
    assert "1. **C** (8 bytes)" in md and "_(p.10)_" in md


def test_pdf_is_small_and_contains_text():
    pdf = to_pdf(QS * 10, "T")
    assert len(pdf) < 300_000  # fonts are subset
    doc = pymupdf.open("pdf", pdf)
    text = "".join(p.get_text() for p in doc).replace("\xa0", " ")  # MuPDF HTML uses NBSPs
    assert "Answer key" in text and "UDP header" in text
