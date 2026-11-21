"""The synthesis desk: the voice, the per-member limit, the timeout, the places given back."""

from __future__ import annotations

import asyncio

import pytest
from helpers import arun

from research_api.application.synthesis import SynthesisDesk
from research_api.domain.errors import (
    InvalidInputError,
    RateLimitedError,
    UnavailableError,
    UpstreamError,
)
from research_api.domain.speech import Language
from research_api.domain.synthesis import SynthesisLimits, Voices


class Scripted:
    """A synthesizer a test holds: each call waits for ``release`` and records itself."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.release = asyncio.Event()
        self.calls: list[tuple[str, Language, str]] = []
        self.running = 0
        self._error = error

    async def synthesize(self, text: str, *, language: Language, voice: str) -> bytes:
        self.calls.append((text, language, voice))
        self.running += 1
        try:
            await self.release.wait()
        finally:
            self.running -= 1
        if self._error is not None:
            raise self._error
        return b"RIFF....WAVE"


def desk(synth: Scripted, **limits: float) -> SynthesisDesk:
    return SynthesisDesk(
        synthesizer=synth,
        limits=SynthesisLimits(**limits),  # type: ignore[arg-type]
        voices=Voices(es="voz-es", en="voice-en"),
    )


async def until(condition: object, *, tries: int = 200) -> None:
    for _ in range(tries):
        if condition():  # type: ignore[operator]
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the condition never held")


@arun
async def test_the_cleaned_text_and_the_voice_of_its_language_reach_the_server() -> None:
    synth = Scripted()
    synth.release.set()
    one = desk(synth)
    assert (
        await one.synthesize("ana", "Es **corto** (40a228002007ca7f pages 3-3)") == b"RIFF....WAVE"
    )
    await one.synthesize("ana", "It is $x$", language=None)
    await one.synthesize("ana", "El texto", language="en")
    assert synth.calls == [
        ("Es corto", "es", "voz-es"),
        ("It is equation", "en", "voice-en"),
        ("El texto", "en", "voice-en"),
    ]


@arun
async def test_text_that_cannot_be_read_never_reaches_the_server() -> None:
    synth = Scripted()
    one = desk(synth, max_chars=10)
    for text in ("", "(40a228002007ca7f pages 3-3)", "a" * 11):
        with pytest.raises(InvalidInputError):
            await one.synthesize("ana", text)
    assert synth.calls == []


@arun
async def test_a_member_past_the_limit_is_refused_and_others_are_not() -> None:
    synth = Scripted()
    one = desk(synth, per_member_open=1)
    first = asyncio.ensure_future(one.synthesize("ana", "uno"))
    await until(lambda: synth.running == 1)
    with pytest.raises(RateLimitedError) as refused:
        await one.synthesize("ana", "dos")
    assert refused.value.retry_after_s == 5
    other = asyncio.ensure_future(one.synthesize("bo", "tres"))
    await until(lambda: synth.running == 2)
    synth.release.set()
    await asyncio.gather(first, other)
    await one.synthesize("ana", "cuatro")  # the places came back


@arun
async def test_a_slow_server_is_a_503_and_the_place_comes_back() -> None:
    synth = Scripted()
    one = desk(synth, timeout_s=0.05, per_member_open=1)
    with pytest.raises(UnavailableError, match="too long"):
        await one.synthesize("ana", "uno")
    assert synth.running == 0
    synth.release.set()
    await one.synthesize("ana", "dos")


@arun
async def test_a_caller_that_leaves_gives_the_place_back() -> None:
    synth = Scripted()
    one = desk(synth, per_member_open=1)
    task = asyncio.ensure_future(one.synthesize("ana", "uno"))
    await until(lambda: synth.running == 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    synth.release.set()
    await one.synthesize("ana", "dos")


@arun
async def test_the_failure_of_the_server_passes_through_and_gives_the_place_back() -> None:
    for error in (UnavailableError("down", retry_after_s=30), UpstreamError("no")):
        synth = Scripted(error=error)
        synth.release.set()
        one = desk(synth, per_member_open=1)
        for _ in range(2):
            with pytest.raises(type(error)):
                await one.synthesize("ana", "uno")
