from core.ingest import chunk_pages, extract_pages, split_text


def test_short_text_is_single_chunk():
    assert split_text("A short paragraph about TCP and UDP.") == ["A short paragraph about TCP and UDP."]


def test_empty_text_gives_no_chunks():
    assert split_text("   \n\n ") == []


def test_chunks_respect_size_and_overlap():
    sentences = [f"Sentence number {i} explains a networking concept in detail." for i in range(80)]
    text = " ".join(sentences)
    chunks = split_text(text, chunk_size=800, overlap=150)
    assert len(chunks) > 3
    assert all(len(c) <= 800 for c in chunks)
    # neighbours share text (overlap) ...
    for a, b in zip(chunks, chunks[1:]):
        assert a[-60:].split()[-1] in b
    # ... and nothing is lost
    for s in sentences:
        assert any(s in c for c in chunks)


def test_prefers_paragraph_boundaries():
    para = "word " * 100  # ~500 chars
    chunks = split_text(para.strip() + "\n\n" + para.strip(), chunk_size=800, overlap=0)
    assert len(chunks) == 2


def test_unbreakable_text_is_hard_split():
    chunks = split_text("x" * 2000, chunk_size=800, overlap=0)
    assert [len(c) for c in chunks] == [800, 800, 400]


def test_arabic_text_is_chunked():
    ar = "بروتوكول TCP يضمن وصول البيانات بالترتيب. " * 60
    chunks = split_text(ar, chunk_size=800, overlap=150)
    assert len(chunks) > 1 and all(len(c) <= 800 for c in chunks)


def test_pdf_pages_keep_numbers_and_detect_blank(sample_pdf):
    pages = extract_pages(sample_pdf)
    chunks, empty = chunk_pages(pages)
    assert len(pages) == 10
    assert empty == [8]  # the simulated scanned page
    assert {c["page"] for c in chunks} == set(range(1, 11)) - {8}
    assert any("المصافحة" in c["text"] for c in chunks if c["page"] == 10)
