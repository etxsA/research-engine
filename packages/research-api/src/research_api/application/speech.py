"""Use case: turn a clip into text.

Transcriptions run one at a time in a lane of their own, never the GPU lane: the speech server is
on the CPU, and a clip must not wait behind a benchmark question. Few may be open at once, per
member and in all; past that the answer is a refusal with ``Retry-After``. Nothing is kept: no
audio, no text.
"""

from __future__ import annotations

import asyncio
from collections import Counter

from ..domain.errors import RateLimitedError, UnavailableError
from ..domain.speech import Language, SpeechLimits, Transcript, clip_seconds
from ..ports.speech import Heard, Transcriber
from .lanes import Lane

RETRY_AFTER_S = 5


class SpeechDesk:
    def __init__(self, *, transcriber: Transcriber, limits: SpeechLimits) -> None:
        self._transcriber = transcriber
        self.limits = limits
        self._lane = Lane("speech")
        self._open: Counter[str] = Counter()

    async def stop(self) -> None:
        await self._lane.stop()

    async def transcribe(
        self, member: str, audio: bytes, *, language: Language | None = None
    ) -> Transcript:
        duration = clip_seconds(audio, max_s=self.limits.max_audio_s)
        self._admit(member)
        loop = asyncio.get_running_loop()
        outcome: asyncio.Future[Heard] = loop.create_future()

        async def work() -> None:
            try:
                if outcome.done():  # the caller gave up while the clip waited
                    return
                call = asyncio.ensure_future(self._transcriber.transcribe(audio, language=language))
                outcome.add_done_callback(lambda _: call.cancel())  # the caller left: stop asking
                try:
                    async with asyncio.timeout(self.limits.timeout_s):
                        heard = await call
                except asyncio.CancelledError:
                    if outcome.cancelled():
                        return
                    raise
                if not outcome.done():
                    outcome.set_result(heard)
            except TimeoutError:
                if not outcome.done():
                    outcome.set_exception(_took_too_long())
            except Exception as e:
                if not outcome.done():
                    outcome.set_exception(e)
            finally:
                self._leave(member)

        ticket = self._lane.submit(work)
        try:
            heard = await asyncio.wait_for(outcome, self.limits.timeout_s)
        except TimeoutError:
            raise _took_too_long() from None
        finally:
            outcome.cancel()
            if self._lane.cancel(ticket):  # never started: nobody will release its place
                self._leave(member)
        return Transcript(text=heard.text, language=heard.language or language, duration_s=duration)

    def _admit(self, member: str) -> None:
        if sum(self._open.values()) >= self.limits.global_open:
            raise UnavailableError(
                "Speech recognition is busy; try again in a few seconds.",
                retry_after_s=RETRY_AFTER_S,
            )
        if self._open[member] >= self.limits.per_member_open:
            raise RateLimitedError(
                "Wait for your open transcriptions to finish.", retry_after_s=RETRY_AFTER_S
            )
        self._open[member] += 1

    def _leave(self, member: str) -> None:
        self._open[member] -= 1
        if self._open[member] <= 0:
            del self._open[member]


def _took_too_long() -> UnavailableError:
    return UnavailableError(
        "Speech recognition took too long; try a shorter clip.", retry_after_s=RETRY_AFTER_S
    )
