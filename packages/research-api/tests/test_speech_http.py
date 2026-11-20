"""``POST /speech/transcriptions`` over the fake speech server: every limit and error path."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import LOOPBACK, MEMBER, SEED_FILE, make_wav, sign_in_as_member

from research_api.adapters.fake.speech import CANNED, FAIL_MARKER
from research_api.bootstrap import Container, Settings, build_container
from research_api.bootstrap.seed import load_seed_users
from research_api.domain.speech import Language
from research_api.entrypoints.cli import openapi_document
from research_api.entrypoints.http import build_app
from research_api.ports.speech import Heard

URL = "/api/v1/speech/transcriptions"
WAV = {"Content-Type": "audio/wav"}
PROBLEM = "application/problem+json"
ClientFor = Callable[..., TestClient]


@pytest.fixture
def member(client: TestClient) -> TestClient:
    return sign_in_as_member(client)


def post(client: TestClient, body: bytes | None = None, **kwargs: Any) -> Any:
    kwargs.setdefault("headers", WAV)
    return client.post(URL, content=make_wav(1) if body is None else body, **kwargs)


def test_a_clip_is_turned_into_text(member: TestClient) -> None:
    reply = post(member)
    assert reply.status_code == 200
    assert reply.json() == {"text": CANNED["es"], "language": "es", "duration_s": 1.0}
    assert reply.headers["cache-control"] == "no-store"


def test_the_language_hint_picks_the_language(member: TestClient) -> None:
    reply = post(member, params={"lang": "en"})
    assert reply.json() == {"text": CANNED["en"], "language": "en", "duration_s": 1.0}


def test_a_type_with_parameters_is_fine(member: TestClient) -> None:
    assert post(member, headers={"Content-Type": "audio/x-wav; charset=binary"}).status_code == 200


def test_a_language_other_than_es_or_en_is_a_422(member: TestClient) -> None:
    reply = post(member, params={"lang": "fr"})
    assert reply.status_code == 422 and reply.headers["content-type"] == PROBLEM


@pytest.mark.parametrize("kind", ["audio/webm", "application/json", "text/plain", ""])
def test_a_type_other_than_wav_is_a_422(member: TestClient, kind: str) -> None:
    reply = post(member, headers={"Content-Type": kind} if kind else {})
    assert reply.status_code == 422 and reply.headers["content-type"] == PROBLEM
    assert "audio/wav" in reply.json()["detail"]


@pytest.mark.parametrize(
    "clip", [make_wav(1, rate=44_100), make_wav(1, channels=2), b"RIFFjunk", make_wav(0)]
)
def test_a_body_that_is_not_a_16_khz_mono_wav_is_a_422(member: TestClient, clip: bytes) -> None:
    assert post(member, clip).status_code == 422


def test_an_empty_body_is_a_422_that_says_the_recording_is_empty(member: TestClient) -> None:
    reply = post(member, b"")
    assert reply.status_code == 422 and reply.headers["content-type"] == PROBLEM
    assert "empty" in reply.json()["detail"]


def test_a_clip_over_the_length_limit_is_a_422(member: TestClient) -> None:
    reply = post(member, make_wav(61))
    assert reply.status_code == 422 and "60 seconds" in reply.json()["detail"]
    assert post(member, make_wav(60)).status_code == 200


def test_a_body_over_the_byte_cap_is_a_413(member: TestClient) -> None:
    reply = post(member, b"\0" * (2 * 1024 * 1024 + 1))
    assert reply.status_code == 413 and reply.headers["content-type"] == PROBLEM


def test_the_cap_holds_for_a_chunked_body_with_no_length_declared(member: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(40):
            yield b"\0" * (64 * 1024)

    reply = member.post(URL, content=chunks(), headers=WAV)
    assert reply.status_code == 413


def test_a_member_must_be_signed_in_and_the_body_is_not_read_first(client: TestClient) -> None:
    reply = post(client, b"\0" * (2 * 1024 * 1024 + 1))
    assert reply.status_code == 401 and reply.headers["content-type"] == PROBLEM


def test_a_write_without_the_csrf_header_is_a_403(client: TestClient) -> None:
    assert client.post("/api/v1/auth/login", json=MEMBER).status_code == 204
    reply = post(client)
    assert reply.status_code == 403 and "CSRF" in reply.json()["detail"]


def test_a_write_from_another_origin_is_a_403(member: TestClient) -> None:
    reply = post(member, headers={**WAV, "Origin": "http://evil.example"})
    assert reply.status_code == 403


def test_a_get_is_not_a_route(member: TestClient) -> None:
    assert member.get(URL).status_code == 405


def test_the_fake_speech_server_can_be_made_to_fail(member: TestClient) -> None:
    reply = post(member, make_wav(1) + FAIL_MARKER)
    assert reply.status_code == 503 and reply.headers["retry-after"] == "30"


def test_without_a_speech_section_the_route_is_a_503_and_status_says_so(
    container: Container, client_for: ClientFor
) -> None:
    off = dataclasses.replace(container, speech=None)
    client = sign_in_as_member(client_for(build_app(off)))
    reply = post(client)
    assert reply.status_code == 503 and reply.headers["content-type"] == PROBLEM
    assert client.get("/api/v1/status").json()["speech"] == {"stt": "unavailable"}


def test_status_says_speech_is_ready(member: TestClient) -> None:
    assert member.get("/api/v1/status").json()["speech"] == {"stt": "ready"}


class Held:
    """A speech server that waits until the test lets it go."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    async def transcribe(self, audio: bytes, *, language: Language | None) -> Heard:
        self.entered.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return Heard("late", "es")


def test_too_many_open_clips_of_one_member_is_a_429_with_retry_after(
    client_for: ClientFor,
) -> None:
    held = Held()
    container = build_container(
        Settings.model_validate(
            {
                "speech": {
                    "stt_endpoint": "http://127.0.0.1:8004/v1",
                    "stt_api_key_file": "/not/read/in/fake/mode",
                    "per_member_open": 1,
                }
            }
        ),
        fake=True,
        seed=load_seed_users(SEED_FILE),
        cheap_hashing=True,
        transcriber=held,
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
    assert first[0].status_code == 200 and first[0].json()["text"] == "late"


def test_the_contract_describes_the_route() -> None:
    document = json.loads(openapi_document())
    operation = document["paths"][URL]["post"]
    assert operation["operationId"] == "transcribeSpeech"
    assert operation["requestBody"]["content"] == {
        "audio/wav": {"schema": {"type": "string", "format": "binary"}}
    }
    assert {"413", "422", "429", "503"} <= set(operation["responses"])
    status = document["components"]["schemas"]["Status"]["properties"]["speech"]
    assert status == {"$ref": "#/components/schemas/SpeechStatus"}
