import hashlib
from unittest.mock import AsyncMock, patch

import pytest

import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear_all()
    yield
    cache.clear_all()


# --- L1: in-memory by file_unique_id ---

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
    fake_pool.fetchval = AsyncMock(side_effect=RuntimeError("neon down"))
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
    fake_pool.fetchval = AsyncMock(return_value="cached transcript")
    with patch("cache.analytics_pool.get", return_value=fake_pool):
        result = await cache.get_by_hash("abc123")
    assert result == "cached transcript"
    fake_pool.fetchval.assert_awaited_once()
    # Hash is the first SQL arg
    assert fake_pool.fetchval.await_args.args[1] == "abc123"


@pytest.mark.asyncio
async def test_get_by_hash_returns_none_on_miss():
    fake_pool = AsyncMock()
    fake_pool.fetchval = AsyncMock(return_value=None)
    with patch("cache.analytics_pool.get", return_value=fake_pool):
        assert await cache.get_by_hash("missing") is None


@pytest.mark.asyncio
async def test_store_by_hash_passes_hash_and_text():
    fake_pool = AsyncMock()
    fake_pool.execute = AsyncMock(return_value="INSERT 0 1")
    with patch("cache.analytics_pool.get", return_value=fake_pool):
        await cache.store_by_hash("hash_xyz", "the transcript")
    fake_pool.execute.assert_awaited_once()
    args = fake_pool.execute.await_args.args
    assert args[1] == "hash_xyz"
    assert args[2] == "the transcript"
