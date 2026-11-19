"""The OpenAI-style speech client, against a fake transport: no socket, no speech server."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from helpers import arun

from research_api.adapters.models import OpenAiTranscriber
from research_api.domain.errors import UnavailableError, UpstreamError

KEY = "stt-key-0123456789"
CLIP = b"RIFF-the-clip"


def served(handler: Callable[[httpx.Request], httpx.Response]) -> OpenAiTranscriber:
    return OpenAiTranscriber(
        "http://127.0.0.1:8004/v1/",
        "local-stt",
        KEY,
        timeout_s=5,
        transport=httpx.MockTransport(handler),
    )


def said(**body: object) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json=body)


@arun
async def test_the_clip_goes_as_a_multipart_upload_with_the_key_as_a_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"text": " hola mundo ", "language": "es"})

    heard = await served(handler).transcribe(CLIP, language="es")
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:8004/v1/audio/transcriptions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = request.content
    assert CLIP in body
    assert b'name="model"' in body and b"local-stt" in body
    assert b'name="language"' in body
    assert b"audio/wav" in body
    assert (heard.text, heard.language) == ("hola mundo", "es")


@arun
async def test_without_a_hint_no_language_is_sent() -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={"text": "hi"})

    heard = await served(handler).transcribe(CLIP, language=None)
    assert b'name="language"' not in seen[0]
    assert (heard.text, heard.language) == ("hi", None)


@pytest.mark.parametrize(
    ("said_language", "expected"),
    [("es", "es"), ("EN", "en"), ("Spanish", "es"), ("english", "en"), ("fr", None), (7, None)],
)
@arun
async def test_the_language_the_server_names_is_es_en_or_nothing(
    said_language: object, expected: str | None
) -> None:
    heard = await served(said(text="x", language=said_language)).transcribe(CLIP, language=None)
    assert heard.language == expected


@pytest.mark.parametrize("status", [502, 503, 504])
@arun
async def test_a_server_that_is_not_ready_is_unavailable(status: int) -> None:
    with pytest.raises(UnavailableError) as error:
        await served(lambda r: httpx.Response(status, text="secret words")).transcribe(
            CLIP, language=None
        )
    assert error.value.retry_after_s == 30
    assert "secret" not in error.value.detail


@pytest.mark.parametrize("status", [400, 401, 403, 422, 500])
@arun
async def test_a_refusal_is_an_upstream_error_with_no_body_and_no_key(status: int) -> None:
    with pytest.raises(UpstreamError) as error:
        await served(lambda r: httpx.Response(status, text="what she said")).transcribe(
            CLIP, language=None
        )
    assert "what she said" not in error.value.detail
    assert KEY not in error.value.detail


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"words": "x"}),
        httpx.Response(200, json=["x"]),
    ],
)
@arun
async def test_an_odd_answer_is_an_upstream_error(reply: httpx.Response) -> None:
    with pytest.raises(UpstreamError, match="unknown shape"):
        await served(lambda r: reply).transcribe(CLIP, language=None)


@arun
async def test_a_server_that_cannot_be_reached_is_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(UnavailableError, match="cannot be reached"):
        await served(refuse).transcribe(CLIP, language=None)


@arun
async def test_a_server_that_takes_too_long_is_a_timeout() -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(TimeoutError):
        await served(slow).transcribe(CLIP, language=None)
