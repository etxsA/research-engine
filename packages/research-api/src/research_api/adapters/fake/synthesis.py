"""A speech server that needs no model: a short soft tone as a valid WAV, and one text that fails.

The tone lasts about as long as the text takes to say, so the player has something to play, to
count and to stop. A text that contains ``simulate-unavailable`` fails as an unreachable speech
server does, for the error screens.
"""

from __future__ import annotations

import io
import math
import struct
import wave

from ...domain.errors import UnavailableError
from ...domain.speech import Language

FAIL_MARKER = "simulate-unavailable"
SAMPLE_RATE = 22_050
SECONDS_PER_CHAR = 0.03
MIN_S = 0.4
MAX_S = 4.0
FADE_S = 0.04
PITCH_HZ: dict[Language, float] = {"es": 220.0, "en": 262.0}


class FakeSynthesizer:
    async def synthesize(self, text: str, *, language: Language, voice: str) -> bytes:
        if FAIL_MARKER in text:
            raise UnavailableError("The speech server cannot be reached.", retry_after_s=30)
        seconds = min(MAX_S, max(MIN_S, len(text) * SECONDS_PER_CHAR))
        return tone(seconds, PITCH_HZ[language])


def tone(seconds: float, hertz: float) -> bytes:
    """A quiet sine of ``seconds`` with a short fade at both ends, as mono 16-bit PCM."""
    frames = int(seconds * SAMPLE_RATE)
    fade = int(FADE_S * SAMPLE_RATE)
    samples = []
    for i in range(frames):
        envelope = min(1.0, i / fade, (frames - 1 - i) / fade)
        samples.append(int(4000 * envelope * math.sin(2 * math.pi * hertz * i / SAMPLE_RATE)))
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(SAMPLE_RATE)
        clip.writeframes(struct.pack(f"<{frames}h", *samples))
    return out.getvalue()
