"""audio_probe: volumedetect parsing and the real ffmpeg path on synthetic audio."""
from __future__ import annotations

import os
import subprocess
import tempfile

import pytest

import audio_probe
from gemini_service import _ffmpeg_binary

_SAMPLE = """
  Duration: 00:05:12.60, start: 0.000000, bitrate: 32 kb/s
[Parsed_volumedetect_0 @ 0x1] n_samples: 96000
[Parsed_volumedetect_0 @ 0x1] mean_volume: -47.8 dB
[Parsed_volumedetect_0 @ 0x1] max_volume: -35.0 dB
"""


def test_parse_volumedetect(monkeypatch):
    monkeypatch.setattr(audio_probe, "EMPTY_AUDIO_MAX_DB", -50.0)
    loud = audio_probe.parse_volumedetect(_SAMPLE)
    assert loud is not None
    assert loud.max_db == -35.0 and loud.mean_db == -47.8
    assert loud.duration_sec == 313
    assert not loud.is_empty
    assert audio_probe.parse_volumedetect("garbage") is None


def test_parse_inf_is_silent(monkeypatch):
    monkeypatch.setattr(audio_probe, "EMPTY_AUDIO_MAX_DB", -50.0)
    loud = audio_probe.parse_volumedetect("max_volume: -inf dB\nmean_volume: -inf dB")
    assert loud is not None and loud.max_db == -99.0 and loud.is_empty


def _synth(filter_expr: str, path: str) -> None:
    subprocess.run([_ffmpeg_binary(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", filter_expr, "-t", "2", "-ac", "1", "-ar", "16000", path],
                   check=True, timeout=60)


@pytest.mark.skipif(not _ffmpeg_binary(), reason="ffmpeg not available")
@pytest.mark.asyncio
async def test_measure_distinguishes_silence_from_tone(monkeypatch):
    monkeypatch.setattr(audio_probe, "EMPTY_AUDIO_MAX_DB", -50.0)
    d = tempfile.mkdtemp()
    silent, tone = os.path.join(d, "s.wav"), os.path.join(d, "t.wav")
    _synth("anullsrc=r=16000:cl=mono", silent)
    _synth("sine=frequency=440:sample_rate=16000", tone)
    ls = await audio_probe.measure(silent)
    lt = await audio_probe.measure(tone)
    assert ls is not None and ls.is_empty
    assert lt is not None and not lt.is_empty and lt.max_db > -30


@pytest.mark.asyncio
async def test_measure_missing_file_returns_none():
    assert await audio_probe.measure("/nonexistent/file.ogg") is None
