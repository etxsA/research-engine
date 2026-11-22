"""``POST /speech/synthesis`` over the fake speech server: every limit and error path."""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
import threading
import time
import wave
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import LOOPBACK, MEMBER, SEED_FILE, sign_in_as_member

from research_api.adapters.fake.synthesis import FAIL_MARKER, tone
from research_api.bootstrap import Container, Settings, build_container
from research_api.bootstrap.seed import load_seed_users
from research_api.domain.speech import Language
from research_api.entrypoints.cli import openapi_document
from research_api.entrypoints.http import build_app

URL = "/api/v1/speech/synthesis"
PROBLEM = "application/problem+json"
ClientFor = Callable[..., TestClient]


@pytest.fixture
def member(client: TestClient) -> TestClient:
    return sign_in_as_member(client)


def post(client: TestClient, body: Any = None, **kwargs: Any) -> Any:
    return client.post(URL, json={"text": "Hola, mundo."} if body is None else body, **kwargs)


def test_a_sentence_is_read_as_a_wav(member: TestClient) -> None:
    reply = post(member, {"text": "Hola, mundo.", "language": "es"})
    assert reply.status_code == 200
    assert reply.headers["content-type"] == "audio/wav"
    assert reply.headers["cache-control"] == "no-store"
    with wave.open(io.BytesIO(reply.content), "rb") as clip:
        assert (clip.getnchannels(), clip.getsampwidth()) == (1, 2)
        assert clip.getnframes() > 0


def test_the_language_may_be_left_out_or_null(member: TestClient) -> None:
    assert post(member, {"text": "The qubit"}).status_code == 200
    assert post(member, {"text": "El qubit", "language": None}).status_code == 200


def test_markdown_citations_and_formulas_are_cleaned_before_the_length_is_measured(
    member: TestClient,
) -> None:
    text = "**Bold** $x$ (40a228002007ca7f pages 3-3) " + "a" * 380
    assert len(text) > 400
    assert post(member, {"text": text, "language": "en"}).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"text": ""},
        {"text": "   "},
        {"text": "(40a228002007ca7f pages 3-3)"},
        {"text": "a" * 401},
        {"text": "hi", "language": "fr"},
        {"text": 5},
        {},
        {"text": "hi", "extra": 1},
        {"text": "x" * 8_001},
    ],
)
def test_a_text_that_cannot_be_read_is_a_422(member: TestClient, body: dict[str, Any]) -> None:
    reply = post(member, body)
    assert reply.status_code == 422 and reply.headers["content-type"] == PROBLEM


def test_nothing_to_read_says_so(member: TestClient) -> None:
    reply = post(member, {"text": "---"})
    assert reply.status_code == 422 and "nothing to read" in reply.json()["detail"]


def test_a_body_over_the_byte_cap_is_a_413(member: TestClient) -> None:
    reply = member.post(URL, content=b'{"text":"' + b"a" * 200_000 + b'"}')
    assert reply.status_code == 413 and reply.headers["content-type"] == PROBLEM


def test_a_member_must_be_signed_in_and_the_body_is_not_read_first(client: TestClient) -> None:
    reply = client.post(URL, content=b"a" * 200_000)
    assert reply.status_code == 401 and reply.headers["content-type"] == PROBLEM


def test_a_write_without_the_csrf_header_is_a_403(client: TestClient) -> None:
    assert client.post("/api/v1/auth/login", json=MEMBER).status_code == 204
    reply = post(client)
    assert reply.status_code == 403 and "CSRF" in reply.json()["detail"]


def test_a_write_from_another_origin_is_a_403(member: TestClient) -> None:
    reply = post(member, headers={"Origin": "http://evil.example"})
    assert reply.status_code == 403


def test_a_get_is_not_a_route(member: TestClient) -> None:
    assert member.get(URL).status_code == 405


def test_the_fake_speech_server_can_be_made_to_fail(member: TestClient) -> None:
    reply = post(member, {"text": f"say {FAIL_MARKER}"})
    assert reply.status_code == 503 and reply.headers["retry-after"] == "30"
    assert reply.headers["content-type"] == PROBLEM


def test_without_a_reading_server_the_route_is_a_503_and_status_says_so(
    container: Container, client_for: ClientFor
) -> None:
    off = dataclasses.replace(container, synthesis=None)
    client = sign_in_as_member(client_for(build_app(off)))
    reply = post(client)
    assert reply.status_code == 503 and reply.headers["content-type"] == PROBLEM
    assert reply.headers["retry-after"] == "30"
    assert client.get("/api/v1/status").json()["speech"] == {"stt": "ready", "tts": "unavailable"}


def test_status_says_reading_is_ready(member: TestClient) -> None:
    assert member.get("/api/v1/status").json()["speech"] == {"stt": "ready", "tts": "ready"}


def test_a_503_without_a_session_is_still_a_401(
    container: Container, client_for: ClientFor
) -> None:
    off = dataclasses.replace(container, synthesis=None)
    assert post(client_for(build_app(off))).status_code == 401


class Held:
    """A reading server that waits until the test lets it go."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    async def synthesize(self, text: str, *, language: Language, voice: str) -> bytes:
        self.entered.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return tone(0.1, 220)


def test_too_many_open_readings_of_one_member_is_a_429_with_retry_after(
    client_for: ClientFor,
) -> None:
    held = Held()
    container = build_container(
        Settings.model_validate({"speech": {"per_member_open": 1}}),
        fake=True,
        seed=load_seed_users(SEED_FILE),
        cheap_hashing=True,
        synthesizer=held,
    )
    with client_for(build_app(container), LOOPBACK) as client:
        sign_in_as_member(client)
        first: list[Any] = []
        worker = threading.Thread(target=lambda: first.append(post(client)))
        worker.start()
        assert held.entered.wait(5)
        refused = post(client)
        held.release.set()
        worker.join(5)
    assert refused.status_code == 429 and refused.headers["retry-after"] == "5"
    assert first[0].status_code == 200


def test_the_contract_describes_the_route() -> None:
    document = json.loads(openapi_document())
    operation = document["paths"][URL]["post"]
    assert operation["operationId"] == "synthesizeSpeech"
    assert operation["responses"]["200"]["content"] == {
        "audio/wav": {"schema": {"type": "string", "format": "binary"}}
    }
    assert {"413", "422", "429", "503"} <= set(operation["responses"])
    schemas = document["components"]["schemas"]
    assert set(schemas["SynthesisRequest"]["properties"]) == {"text", "language"}
    assert "tts" in schemas["SpeechStatus"]["properties"]


def test_a_pathological_text_is_refused_or_cleaned_fast(member: TestClient) -> None:
    started = time.monotonic()
    for text in ("**a " * 5000, "[a " * 5000, "<a " * 5000, "\\(" * 4000):
        reply = post(member, {"text": text})
        assert reply.status_code == 422
    assert time.monotonic() - started < 1.0
