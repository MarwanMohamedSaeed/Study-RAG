import json
import random

import pytest
from pydantic import ValidationError

from core.mcq import attribute_pages, dedupe, parse_mcqs, shuffle_options
from core.schemas import MCQ

GOOD = {
    "question": "Which port does HTTPS use by default?",
    "options": ["80", "443", "22", "53"],
    "correct": "B",
    "explanation": "HTTPS uses well-known port 443.",
    "source_page": 2,
}


def make(**kw):
    return {**GOOD, **kw}


def test_valid_mcq():
    q = MCQ.model_validate(GOOD)
    assert q.correct_text == "443"


@pytest.mark.parametrize("bad", [
    make(options=["80", "443", "22"]),                        # 3 options
    make(options=["80", "443", "443", "53"]),                 # duplicate -> two correct answers
    make(options=["80", "443", "22", "All of the above"]),    # lazy distractor
    make(correct="E"),
    make(source_page=0),
    make(question=""),
    make(question="Which port does HTTPS use?\nA) 80\nB) 443\nC) 22\nD) 53"),  # options pasted into question
    make(options=["80", "Port 443, the well-known port reserved for HTTPS traffic", "22", "53"]),  # giveaway
])
def test_invalid_mcq_rejected(bad):
    with pytest.raises(ValidationError):
        MCQ.model_validate(bad)


@pytest.mark.parametrize("given, expected", [("b", "B"), ("B)", "B"), ("Option B", "B"), ("443", "B")])
def test_correct_is_normalized(given, expected):
    assert MCQ.model_validate(make(correct=given)).correct == expected


def test_option_prefixes_are_stripped():
    q = MCQ.model_validate(make(options=["A) 80", "B) 443", "C. 22", "(D) 53"]))
    assert q.options == ["80", "443", "22", "53"]


def test_parse_keeps_good_items_and_snaps_pages():
    raw = "```json\n" + json.dumps({"questions": [make(source_page=99), make(correct="Z")]}) + "\n```"
    good, errors = parse_mcqs(raw, valid_pages={2, 3})
    assert len(good) == 1 and len(errors) == 1
    assert good[0].source_page == 3


def test_parse_rejects_non_json():
    with pytest.raises(ValueError):
        parse_mcqs("Sure! Here are your questions: ...")


def test_shuffle_keeps_correct_answer():
    q = MCQ.model_validate(GOOD)
    for seed in range(10):
        s = shuffle_options(q, random.Random(seed))
        assert s.correct_text == "443" and sorted(s.options) == sorted(q.options)


def test_source_page_comes_from_best_matching_chunk():
    chunks = [{"page": 3, "text": "The UDP header is only 8 bytes long and contains four fields."},
              {"page": 9, "text": "A UDP socket is created with SOCK_DGRAM instead of SOCK_STREAM."}]
    q = MCQ.model_validate(make(question="How is a UDP socket created in Python?",
                                options=["With SOCK_DGRAM", "With SOCK_STREAM", "With listen()", "With accept()"],
                                correct="A", source_page=1))  # model claimed page 1 ("excerpt 1")
    assert attribute_pages([q], chunks)[0].source_page == 9


def test_dedupe_drops_paraphrases():
    a = MCQ.model_validate(GOOD)
    b = MCQ.model_validate(make(question="Which port does HTTPS use by default ?"))
    c = MCQ.model_validate(make(question="What is the size of the UDP header in bytes?",
                                options=["8", "20", "16", "32"], correct="A"))
    kept = dedupe([a, b, c])
    assert [q.question for q in kept] == [a.question, c.question]
