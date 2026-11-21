"""The OpenAI-style reading client, against a fake transport: no socket, no speech server."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from helpers import arun

from research_api.adapters.fake.synthesis import tone
from research_api.adapters.models import OpenAiSynthesizer
from research_api.domain.errors import UnavailableError, UpstreamError

KEY = "tts-key-0123456789"
WAV = tone(0.1, 220)


def served(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAiSynthesizer:
    return OpenAiSynthesizer(
        "http://127.0.0.1:8005/v1/",
        "local-tts",
        KEY,
        timeout_s=5,
        transport=httpx.MockTransport(handler),
    )


@arun
async def test_the_text_goes_as_json_with_the_key_as_a_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=WAV, headers={"content-type": "audio/wav"})

    audio = await served(handler).synthesize("hola", language="es", voice="v-es")
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:8005/v1/audio/speech"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {
        "model": "local-tts",
        "input": "hola",
        "voice": "v-es",
        "response_format": "wav",
        "language": "es",
    }
    assert audio == WAV


@arun
async def test_a_timeout_is_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(TimeoutError):
        await served(handler).synthesize("hola", language="es", voice="v")


@arun
async def test_a_server_that_is_down_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(UnavailableError) as raised:
        await served(handler).synthesize("hola", language="es", voice="v")
    assert raised.value.retry_after_s == 30
    assert "hola" not in str(raised.value)


@arun
@pytest.mark.parametrize("status", [502, 503, 504])
async def test_a_server_that_is_not_ready_is_unavailable(status: int) -> None:
    with pytest.raises(UnavailableError):
        await served(lambda _: httpx.Response(status, text="secret body")).synthesize(
            "hola", language="es", voice="v"
        )


@arun
@pytest.mark.parametrize("status", [400, 401, 404, 422, 500])
async def test_any_other_refusal_is_an_upstream_error_without_the_body(status: int) -> None:
    with pytest.raises(UpstreamError) as raised:
        await served(lambda _: httpx.Response(status, text="the-body-and-hola")).synthesize(
            "hola", language="es", voice="v"
        )
    assert "the-body" not in str(raised.value) and "hola" not in str(raised.value)


@arun
@pytest.mark.parametrize("content", [b"", b"not a wav at all", b"RIFF"])
async def test_an_answer_that_is_not_a_wav_is_an_upstream_error(content: bytes) -> None:
    with pytest.raises(UpstreamError):
        await served(lambda _: httpx.Response(200, content=content)).synthesize(
            "hola", language="es", voice="v"
        )
