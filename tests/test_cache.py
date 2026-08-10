import hashlib
from unittest.mock import AsyncMock, patch

import pytest

import cache
from cache import CachedTranscription


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear_all()
    yield
    cache.clear_all()


# --- L1: in-memory by file_unique_id ---

def test_store_and_get_transcription():
    cache.store_transcription("file_abc", "hello world")
    cached = cache.get_transcription("file_abc")
    assert cached == CachedTranscription("hello world", None)


def test_store_and_get_transcription_with_language():
    cache.store_transcription("file_abc", "привіт", "uk")
    cached = cache.get_transcription("file_abc")
    assert cached == CachedTranscription("привіт", "uk")


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


# --- L2: hash_file ---

def test_hash_file_matches_sha256(tmp_path):
    payload = b"some audio bytes" * 100
    p = tmp_path / "audio.bin"
    p.write_bytes(payload)
    assert cache.hash_file(str(p)) == hashlib.sha256(payload).hexdigest()


def test_hash_file_streams_large_file(tmp_path):
    # 5 MB — exercises the chunked read path
    payload = b"\xab" * (5 * 1024 * 1024)
    p = tmp_path / "big.bin"
    p.write_bytes(payload)
    assert cache.hash_file(str(p)) == hashlib.sha256(payload).hexdigest()


# --- L2: graceful degradation when pool is None ---

@pytest.mark.asyncio
async def test_get_by_hash_returns_none_when_pool_unavailable():
    with patch("cache.analytics_pool.get", return_value=None):
        assert await cache.get_by_hash("abc123") is None


@pytest.mark.asyncio
async def test_store_by_hash_is_noop_when_pool_unavailable():
    # Should not raise even though pool is missing.
    with patch("cache.analytics_pool.get", return_value=None):
        await cache.store_by_hash("abc123", "transcript")


# --- L2: graceful degradation on Postgres errors ---

@pytest.mark.asyncio
async def test_get_by_hash_swallows_postgres_errors():
    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(side_effect=RuntimeError("neon down"))
    with patch("cache.analytics_pool.get", return_value=fake_pool):
        assert await cache.get_by_hash("abc123") is None


@pytest.mark.asyncio
async def test_store_by_hash_swallows_postgres_errors():
    fake_pool = AsyncMock()
    fake_pool.execute = AsyncMock(side_effect=RuntimeError("neon down"))
    with patch("cache.analytics_pool.get", return_value=fake_pool):
        await cache.store_by_hash("abc123", "transcript")  # must not raise


# --- L2: hit/miss flow against a mocked pool ---

@pytest.mark.asyncio
async def test_get_by_hash_returns_text_on_hit():
    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(
        return_value={"text": "cached transcript", "detected_language": "en"}
    )
    with patch("cache.analytics_pool.get", return_value=fake_pool):
        result = await cache.get_by_hash("abc123")
    assert result == CachedTranscription("cached transcript", "en")
    fake_pool.fetchrow.assert_awaited_once()
    # Hash is the first SQL arg
    assert fake_pool.fetchrow.await_args.args[1] == "abc123"


@pytest.mark.asyncio
async def test_get_by_hash_returns_none_on_miss():
    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(return_value=None)
    with patch("cache.analytics_pool.get", return_value=fake_pool):
        assert await cache.get_by_hash("missing") is None


@pytest.mark.asyncio
async def test_store_by_hash_passes_hash_text_and_language():
    fake_pool = AsyncMock()
    fake_pool.execute = AsyncMock(return_value="INSERT 0 1")
    with patch("cache.analytics_pool.get", return_value=fake_pool):
        await cache.store_by_hash("hash_xyz", "the transcript", "uk")
    fake_pool.execute.assert_awaited_once()
    args = fake_pool.execute.await_args.args
    assert args[1] == "hash_xyz"
    assert args[2] == "the transcript"
    assert args[3] == "uk"


@pytest.mark.asyncio
async def test_store_by_hash_defaults_language_to_none():
    fake_pool = AsyncMock()
    fake_pool.execute = AsyncMock(return_value="INSERT 0 1")
    with patch("cache.analytics_pool.get", return_value=fake_pool):
        await cache.store_by_hash("hash_xyz", "the transcript")
    fake_pool.execute.assert_awaited_once()
    assert fake_pool.execute.await_args.args[3] is None


# --- Partially degraded transcripts (placeholder fragments) ---
# Regression guards for 10.08.2026: a transcript that lost chunks carries
# "[фрагмент N: не вдалося розпізнати]" placeholders and must never be cached
# or served from cache — otherwise the loss is irreversible for 14 days (L2).

_PARTIAL = "перша половина тексту\n[фрагмент 2: не вдалося розпізнати]"
# Variant produced by split-chunk salvaging (WIP in gemini_service): extra
# detail between the fragment number and the colon.
_PARTIAL_SPLIT = "текст\n[фрагмент 1, частина 2: не вдалося розпізнати]"


def test_cache_refuses_to_store_partially_degraded_text():
    cache.store_transcription("uniq_part", _PARTIAL, "uk")
    assert cache.get_transcription("uniq_part") is None


def test_degraded_marker_variant_with_part_number_is_detected():
    cache.store_transcription("uniq_split", _PARTIAL_SPLIT, "uk")
    assert cache.get_transcription("uniq_split") is None


def test_degraded_l1_entry_from_before_guard_is_evicted():
    # Force a pre-guard partial entry straight into L1, bypassing the write guard.
    cache.transcription_cache["uniq_old"] = CachedTranscription(_PARTIAL, "uk")
    assert cache.get_transcription("uniq_old") is None
    assert "uniq_old" not in cache.transcription_cache


def test_text_mentioning_fragments_is_not_mistaken_for_marker():
    # A transcript that merely talks about fragments must still be cached.
    text = "у другому фрагменті лекції не вдалося розпізнати акцент доповідача"
    cache.store_transcription("uniq_talk", text, "uk")
    assert cache.get_transcription("uniq_talk").text == text


@pytest.mark.asyncio
async def test_get_by_hash_ignores_degraded_row():
    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(
        return_value={"text": _PARTIAL, "detected_language": "uk"}
    )
    with patch("cache.analytics_pool.get", return_value=fake_pool):
        assert await cache.get_by_hash("abc123") is None


@pytest.mark.asyncio
async def test_store_by_hash_skips_degraded_text():
    fake_pool = AsyncMock()
    fake_pool.execute = AsyncMock()
    with patch("cache.analytics_pool.get", return_value=fake_pool):
        await cache.store_by_hash("abc123", _PARTIAL, "uk")
    fake_pool.execute.assert_not_awaited()


def test_store_by_hash_sql_heals_blank_and_degraded_rows():
    # The ON CONFLICT heal must target both poisoned shapes, or old rows are
    # never replaced by a clean retry (get_by_hash misses them forever and every
    # re-send pays for Gemini again).
    assert "btrim(nocorny_voice.transcription_cache.text) = ''" in cache._STORE_BY_HASH_SQL
    assert "LIKE '%[фрагмент %: не вдалося розпізнати]%'" in cache._STORE_BY_HASH_SQL
