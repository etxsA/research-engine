"""The speech desk: limits, the lane of its own, the timeout, and what it lets go of."""

from __future__ import annotations

import asyncio

import pytest
from helpers import arun, make_wav

from research_api.application.speech import SpeechDesk
from research_api.domain.errors import (
    InvalidInputError,
    RateLimitedError,
    UnavailableError,
    UpstreamError,
)
from research_api.domain.speech import Language, SpeechLimits
from research_api.ports.speech import Heard

CLIP = make_wav(1)


class Scripted:
    """A transcriber a test holds: each call waits for ``release`` and records itself."""

    def __init__(self, *, heard: Heard | None = None, error: Exception | None = None) -> None:
        self.release = asyncio.Event()
        self.started = 0
        self.running = 0
        self.most_at_once = 0
        self.hints: list[Language | None] = []
        self._heard = heard or Heard("hola", "es")
        self._error = error

    async def transcribe(self, audio: bytes, *, language: Language | None) -> Heard:
        self.started += 1
        self.running += 1
        self.most_at_once = max(self.most_at_once, self.running)
        self.hints.append(language)
        try:
            await self.release.wait()
        finally:
            self.running -= 1
        if self._error is not None:
            raise self._error
        return self._heard


def desk(transcriber: Scripted, **limits: float) -> SpeechDesk:
    return SpeechDesk(transcriber=transcriber, limits=SpeechLimits(**limits))  # type: ignore[arg-type]


async def until(condition: object, *, tries: int = 200) -> None:
    for _ in range(tries):
        if condition():  # type: ignore[operator]
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the condition never held")


@arun
async def test_a_clip_is_transcribed_and_its_length_is_reported() -> None:
    server = Scripted(heard=Heard("hello", "en"))
    server.release.set()
    transcript = await desk(server).transcribe("ana", make_wav(2.5), language=None)
    assert (transcript.text, transcript.language, transcript.duration_s) == ("hello", "en", 2.5)


@arun
async def test_the_hint_stands_in_when_the_server_names_no_language() -> None:
    server = Scripted(heard=Heard("hola", None))
    server.release.set()
    speech = desk(server)
    assert (await speech.transcribe("ana", CLIP, language="es")).language == "es"
    assert (await speech.transcribe("ana", CLIP, language=None)).language is None
    assert server.hints == ["es", None]


@arun
async def test_a_bad_clip_is_refused_before_it_takes_a_place() -> None:
    server = Scripted()
    speech = desk(server, per_member_open=1)
    with pytest.raises(InvalidInputError):
        await speech.transcribe("ana", b"junk")
    server.release.set()
    await speech.transcribe("ana", CLIP)
    assert server.started == 1


@arun
async def test_a_member_may_have_only_so_many_open() -> None:
    server = Scripted()
    speech = desk(server, per_member_open=2, global_open=10)
    first = [asyncio.create_task(speech.transcribe("ana", CLIP)) for _ in range(2)]
    await until(lambda: server.started == 1)
    with pytest.raises(RateLimitedError) as error:
        await speech.transcribe("ana", CLIP)
    assert error.value.retry_after_s == 5
    other = asyncio.create_task(speech.transcribe("beto", CLIP))  # another member is not held back
    server.release.set()
    await asyncio.gather(*first, other)
    await speech.transcribe("ana", CLIP)  # places came back
    await speech.stop()


@arun
async def test_the_desk_as_a_whole_has_a_limit_too() -> None:
    server = Scripted()
    speech = desk(server, per_member_open=5, global_open=2)
    held = [asyncio.create_task(speech.transcribe(name, CLIP)) for name in ("ana", "beto")]
    await until(lambda: server.started == 1)
    with pytest.raises(UnavailableError) as error:
        await speech.transcribe("carla", CLIP)
    assert error.value.retry_after_s == 5
    server.release.set()
    await asyncio.gather(*held)
    await speech.stop()


@arun
async def test_clips_run_one_at_a_time_in_the_speech_lane() -> None:
    server = Scripted()
    speech = desk(server, per_member_open=3, global_open=3)
    tasks = [asyncio.create_task(speech.transcribe("ana", CLIP)) for _ in range(3)]
    await until(lambda: server.started == 1)
    server.release.set()
    await asyncio.gather(*tasks)
    assert server.most_at_once == 1
    await speech.stop()


@arun
async def test_a_slow_server_is_a_503_and_gives_its_place_back() -> None:
    server = Scripted()
    speech = desk(server, timeout_s=0.05, per_member_open=1)
    with pytest.raises(UnavailableError, match="took too long"):
        await speech.transcribe("ana", CLIP)
    await until(lambda: server.running == 0)
    server.release.set()
    await speech.transcribe("ana", CLIP)
    await speech.stop()


@arun
async def test_a_clip_that_waits_too_long_in_line_is_a_503_and_the_places_come_back() -> None:
    server = Scripted()
    speech = desk(server, timeout_s=0.1, per_member_open=2, global_open=4)
    running = asyncio.create_task(speech.transcribe("ana", CLIP))
    await until(lambda: server.started == 1)
    with pytest.raises(UnavailableError, match="took too long"):
        await speech.transcribe("beto", CLIP)
    with pytest.raises(UnavailableError):
        await running
    server.release.set()
    await speech.transcribe("beto", CLIP)
    await speech.transcribe("ana", CLIP)
    await speech.stop()


@arun
async def test_a_caller_that_hangs_up_gives_its_place_back() -> None:
    server = Scripted()
    speech = desk(server, per_member_open=1, timeout_s=30)
    waiting = asyncio.create_task(speech.transcribe("ana", CLIP))
    await until(lambda: server.started == 1)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await until(lambda: server.running == 0)  # the running call is cancelled with its work
    server.release.set()
    await speech.transcribe("ana", CLIP)
    await speech.stop()


@arun
async def test_a_failure_of_the_server_reaches_the_caller_and_frees_the_place() -> None:
    server = Scripted(error=UpstreamError("The speech server refused the request."))
    server.release.set()
    speech = desk(server, per_member_open=1)
    for _ in range(2):
        with pytest.raises(UpstreamError):
            await speech.transcribe("ana", CLIP)
    await speech.stop()


@arun
async def test_a_server_that_raises_a_timeout_itself_is_a_503() -> None:
    server = Scripted(error=TimeoutError("slow"))
    server.release.set()
    with pytest.raises(UnavailableError, match="took too long"):
        await desk(server).transcribe("ana", CLIP)


@arun
async def test_stopping_a_desk_that_never_ran_is_harmless() -> None:
    await desk(Scripted()).stop()
