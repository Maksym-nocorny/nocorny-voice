"""Cheap "did anybody speak?" probe via ffmpeg volumedetect.

A video note recorded by accident, or a voice message where the sender never
said a word, used to reach the transcription engine, which then invented
text for it (Gemini once sent a user a list of timestamps for an empty
circle). This probe runs BEFORE any engine on every file and rejects audio
whose loudest sample is still quieter than EMPTY_AUDIO_MAX_DB. The Whisper
worker adds a real voice-activity detector on top; this check is the one
that also protects the Gemini-only path.

Conservative on purpose: room tone and breathing peak around -45..-60 dB and
pass through to the engine; only genuinely silent recordings are cut here.
Any ffmpeg failure returns None and the caller proceeds as if the probe had
not run — a broken probe must never lose a real transcription.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

from config import EMPTY_AUDIO_MAX_DB
from gemini_service import _ffmpeg_binary

logger = logging.getLogger(__name__)

_MAX_RE = re.compile(r"max_volume:\s*(-?[\d.]+|-inf)\s*dB")
_MEAN_RE = re.compile(r"mean_volume:\s*(-?[\d.]+|-inf)\s*dB")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2})(?:\.(\d+))?")


@dataclass
class Loudness:
    max_db: float
    mean_db: float
    # Container duration ffmpeg printed while decoding, seconds; None if absent.
    # Forwarded/re-encoded clips often arrive from Telegram with duration=0,
    # and the worker wait budget scales with duration — this fills the gap.
    duration_sec: Optional[int] = None

    @property
    def is_empty(self) -> bool:
        return self.max_db < EMPTY_AUDIO_MAX_DB


def parse_volumedetect(stderr: str) -> Optional[Loudness]:
    m = _MAX_RE.search(stderr)
    if not m:
        return None
    mean = _MEAN_RE.search(stderr)
    duration = None
    d = _DURATION_RE.search(stderr)
    if d:
        duration = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + int(d.group(3))
        if d.group(4) and int(d.group(4)[:1]) >= 5:
            duration += 1
    return Loudness(max_db=_db(m.group(1)), mean_db=_db(mean.group(1)) if mean else -99.0,
                    duration_sec=duration)


def _db(raw: str) -> float:
    return -99.0 if raw == "-inf" else float(raw)


async def measure(file_path: str, timeout_sec: float = 60.0) -> Optional[Loudness]:
    ffmpeg = _ffmpeg_binary()
    if not ffmpeg:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-nostats", "-i", file_path, "-vn", "-af", "volumedetect",
            "-f", "null", "-",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("audio_probe_timeout")
            return None
    except Exception as e:  # noqa: BLE001 — spawn failure: proceed without probe
        logger.warning("audio_probe_spawn_failed exc=%s", e)
        return None
    if proc.returncode != 0:
        logger.info("audio_probe_failed rc=%s", proc.returncode)
        return None
    loud = parse_volumedetect((stderr or b"").decode("utf-8", errors="replace"))
    if loud is None:
        logger.info("audio_probe_unparsed")
        return None
    logger.info("audio_probe max_db=%.1f mean_db=%.1f duration=%s empty=%s",
                loud.max_db, loud.mean_db, loud.duration_sec, loud.is_empty)
    return loud
