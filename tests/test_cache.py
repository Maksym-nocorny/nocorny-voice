import pytest

import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear_all()
    yield
    cache.clear_all()


def test_store_and_get_transcription():
    cache.store_transcription("file_abc", "hello world")
    assert cache.get_transcription("file_abc") == "hello world"


def test_get_missing_returns_none():
    assert cache.get_transcription("nope") is None


def test_remove_transcription():
    cache.store_transcription("file_abc", "x")
    cache.remove_transcription("file_abc")
    assert cache.get_transcription("file_abc") is None


def test_clear_all_empties_cache():
    cache.store_transcription("a", "1")
    cache.store_transcription("b", "2")
    cache.clear_all()
    assert cache.get_transcription("a") is None
    assert cache.get_transcription("b") is None
