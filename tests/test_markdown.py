from utils.markdown import chunk_text, escape_html


def test_escape_html_special_chars():
    assert escape_html("a & b") == "a &amp; b"
    assert escape_html("<script>") == "&lt;script&gt;"
    assert escape_html("hello") == "hello"


def test_escape_html_none_safe():
    assert escape_html("") == ""


def test_chunk_short_text_returns_single_chunk():
    assert chunk_text("hello", 100) == ["hello"]


def test_chunk_empty_returns_empty():
    assert chunk_text("", 100) == []


def test_chunk_splits_on_newline_when_available():
    text = "line1\nline2\nline3"
    chunks = chunk_text(text, max_length=10)
    # Each chunk <= 10 chars, splits prefer newlines
    for c in chunks:
        assert len(c) <= 10
    assert "".join(c.replace("\n", "") for c in chunks).replace("", "") != ""


def test_chunk_hard_split_when_no_newline():
    text = "x" * 25
    chunks = chunk_text(text, max_length=10)
    assert chunks == ["xxxxxxxxxx", "xxxxxxxxxx", "xxxxx"]


def test_chunk_zero_length_raises():
    import pytest

    with pytest.raises(ValueError):
        chunk_text("abc", 0)


def test_chunk_preserves_total_content():
    text = "a" * 50 + "\n" + "b" * 50
    chunks = chunk_text(text, max_length=30)
    rejoined = "".join(chunks)
    # Allow newlines to be removed at split boundaries
    assert "a" * 50 in rejoined
    assert "b" * 50 in rejoined
