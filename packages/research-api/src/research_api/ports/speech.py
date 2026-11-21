"""Port: the speech-to-text server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain.speech import Language


@dataclass(frozen=True)
class Heard:
    """What the server made of a clip. ``language`` is None when it does not say."""

    text: str
    language: Language | None = None


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, *, language: Language | None) -> Heard:
        """The text of a 16 kHz mono WAV, given ``language`` as a hint. ``UnavailableError`` when
        the server cannot be reached or is not ready, ``TimeoutError`` when it takes too long,
        ``UpstreamError`` (no body in the message) when it refuses."""
        ...


class Synthesizer(Protocol):
    async def synthesize(self, text: str, *, language: Language, voice: str) -> bytes:
        """The speech of ``text`` as a mono 16-bit PCM WAV. ``UnavailableError`` when the server
        cannot be reached or is not ready, ``TimeoutError`` when it takes too long,
        ``UpstreamError`` (no text in the message) when it refuses."""
        ...
