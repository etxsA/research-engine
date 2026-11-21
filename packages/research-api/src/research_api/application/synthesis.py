"""Use case: read a piece of an answer aloud.

Unlike a transcription, a synthesis has no lane: the text server has its own GPU and its own queue,
and a sentence must not wait behind a benchmark question or a clip. At most a few are open per
member (the player asks for the next sentence while one plays); past that the answer is a refusal
with ``Retry-After``. Nothing is kept: no text, no audio.
"""

from __future__ import annotations

import asyncio
from collections import Counter

from ..domain.errors import RateLimitedError, UnavailableError
from ..domain.speech import Language
from ..domain.synthesis import SynthesisLimits, Voices, utterance
from ..ports.speech import Synthesizer

RETRY_AFTER_S = 5


class SynthesisDesk:
    def __init__(
        self, *, synthesizer: Synthesizer, limits: SynthesisLimits, voices: Voices
    ) -> None:
        self._synthesizer = synthesizer
        self.limits = limits
        self.voices = voices
        self._open: Counter[str] = Counter()

    async def synthesize(
        self, member: str, text: str, *, language: Language | None = None
    ) -> bytes:
        said = utterance(text, language, max_chars=self.limits.max_chars)
        if self._open[member] >= self.limits.per_member_open:
            raise RateLimitedError(
                "Wait for your open readings to finish.", retry_after_s=RETRY_AFTER_S
            )
        self._open[member] += 1
        try:
            async with asyncio.timeout(self.limits.timeout_s):
                return await self._synthesizer.synthesize(
                    said.text, language=said.language, voice=self.voices.of(said.language)
                )
        except TimeoutError:
            raise UnavailableError(
                "Reading aloud took too long; try again.", retry_after_s=RETRY_AFTER_S
            ) from None
        finally:
            self._open[member] -= 1
            if self._open[member] <= 0:
                del self._open[member]
