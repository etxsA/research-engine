"""Speech to text: what a clip may be, and the limits a request keeps.

A clip is a 16 kHz mono 16-bit PCM WAV, which is what the browser records and what the speech
server takes without converting. Nothing here is kept: the audio and its text live only for the
length of one request.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Final

from .auth import LANGUAGES, Language
from .errors import InvalidInputError

__all__ = ["LANGUAGES", "Language"]

SAMPLE_RATE: Final = 16_000
SAMPLE_BYTES: Final = 2
WAV_TYPES: Final = frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"})

DEFAULT_MAX_AUDIO_BYTES: Final = 2 * 1024 * 1024
DEFAULT_MAX_AUDIO_S: Final = 60.0
DEFAULT_TIMEOUT_S: Final = 30.0
DEFAULT_PER_MEMBER_OPEN: Final = 2
DEFAULT_GLOBAL_OPEN: Final = 4

WRONG_TYPE: Final = "Send the clip as audio/wav: 16 kHz, mono, 16-bit PCM."


@dataclass(frozen=True)
class SpeechLimits:
    """What one request and the desk keep to. ``*_open`` count transcriptions waiting or running."""

    max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES
    max_audio_s: float = DEFAULT_MAX_AUDIO_S
    timeout_s: float = DEFAULT_TIMEOUT_S
    per_member_open: int = DEFAULT_PER_MEMBER_OPEN
    global_open: int = DEFAULT_GLOBAL_OPEN


@dataclass(frozen=True)
class Transcript:
    text: str
    language: Language | None
    duration_s: float


def is_wav_type(content_type: str) -> bool:
    return content_type.partition(";")[0].strip().lower() in WAV_TYPES


def clip_seconds(audio: bytes, *, max_s: float) -> float:
    """The length of a clip in seconds, or an ``InvalidInputError`` when it is not a 16 kHz mono
    16-bit PCM WAV, holds no sound, or is longer than ``max_s``."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as clip:
            format_ok = (
                clip.getnchannels() == 1
                and clip.getsampwidth() == SAMPLE_BYTES
                and clip.getframerate() == SAMPLE_RATE
                and clip.getcomptype() == "NONE"
            )
            frames = len(clip.readframes(clip.getnframes())) // SAMPLE_BYTES if format_ok else 0
    except (wave.Error, EOFError):
        raise InvalidInputError(WRONG_TYPE) from None
    if not format_ok:
        raise InvalidInputError(WRONG_TYPE)
    if frames == 0:
        raise InvalidInputError("The clip holds no sound.")
    seconds = frames / SAMPLE_RATE
    if seconds > max_s:
        raise InvalidInputError(f"The clip is longer than {max_s:g} seconds.")
    return seconds
