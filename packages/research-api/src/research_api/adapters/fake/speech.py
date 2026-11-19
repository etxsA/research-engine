"""A speech server that needs no model: canned text by language, and one input that fails.

A clip that ends with the bytes ``simulate-unavailable`` (after the sound, where a WAV reader
does not look) fails as an unreachable speech server does, for the error screens.
"""

from __future__ import annotations

from ...domain.errors import UnavailableError
from ...domain.speech import Language
from ...ports.speech import Heard

FAIL_MARKER = b"simulate-unavailable"

CANNED: dict[Language, str] = {
    "es": "Qué es el tiempo T2 de un transmon y por qué es más corto que dos veces T1",
    "en": "What is the T2 time of a transmon and why is it shorter than twice T1",
}


class FakeTranscriber:
    async def transcribe(self, audio: bytes, *, language: Language | None) -> Heard:
        if audio.endswith(FAIL_MARKER):
            raise UnavailableError("The speech server cannot be reached.", retry_after_s=30)
        heard: Language = language or "es"
        return Heard(text=CANNED[heard], language=heard)
