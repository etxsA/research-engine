"""What the service writes down: JSON lines, with no secret in them."""

from __future__ import annotations

import json
import logging
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from helpers import MovableClock, live_settings, make_wav, prepare_live

from research_api.adapters.fake import FakeDocumentReader
from research_api.adapters.fake.corpus import FakePage, FakePaper
from research_api.adapters.fake.synthesis import tone
from research_api.adapters.models import OpenAiSynthesizer, OpenAiTranscriber
from research_api.bootstrap import Settings, build_container
from research_api.bootstrap.seed import SeedUser
from research_api.entrypoints.http import build_app
from research_api.entrypoints.http.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from research_api.entrypoints.logs import (
    FIELDS,
    LOG_NAME,
    JsonFormatter,
    PrivateRotatingFileHandler,
    configure_logging,
)

ANA = {"username": "ana", "password": "test-password-ana-01"}
BETO = {"username": "beto", "password": "test-password-beto-02"}


def record(msg: str = "hello", **extra: Any) -> logging.LogRecord:
    made = logging.LogRecord("research_api.test", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(made, key, value)
    return made


# --- the formatter --------------------------------------------------------------------------


def test_a_line_is_json_with_time_level_logger_and_message() -> None:
    data = json.loads(JsonFormatter().format(record("hello")))
    assert data["msg"] == "hello"
    assert data["level"] == "INFO"
    assert data["logger"] == "research_api.test"
    assert data["time"].endswith("+00:00")


def test_only_the_listed_fields_are_copied() -> None:
    line = JsonFormatter().format(
        record(
            event="login.failure",
            username="ana",
            password="hunter2-hunter2",
            cookie="re_session=abc",
            token="t0k3n",
            body='{"question": "private"}',
        )
    )
    data = json.loads(line)
    assert data["username"] == "ana"
    assert set(data) <= {"time", "level", "logger", "msg", "exception", *FIELDS}
    for secret in ("hunter2", "re_session", "t0k3n", "private"):
        assert secret not in line


def test_an_exception_is_written_as_text_and_the_line_stays_one_line() -> None:
    try:
        raise ValueError("boom\nsecond line")
    except ValueError:
        made = logging.LogRecord(
            "research_api", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
    line = JsonFormatter().format(made)
    assert "\n" not in line
    assert "boom" in json.loads(line)["exception"]


def test_non_ascii_text_stays_readable() -> None:
    assert "María" in JsonFormatter().format(record("María"))


# --- the handlers ---------------------------------------------------------------------------


def test_without_a_folder_the_lines_go_to_stderr() -> None:
    configure_logging(None)
    logger = logging.getLogger("research_api")
    assert [type(h) for h in logger.handlers] == [logging.StreamHandler]
    assert logger.propagate is False
    assert logger.level == logging.INFO


def test_with_a_folder_the_lines_go_to_a_private_file(tmp_path: Path) -> None:
    folder = tmp_path / "logs"
    folder.mkdir(mode=0o700)
    configure_logging(folder)
    logging.getLogger("research_api.audit").info("hello", extra={"event": "x", "username": "ana"})
    for handler in logging.getLogger("research_api").handlers:
        handler.flush()
    path = folder / LOG_NAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["username"] == "ana"


def test_configuring_twice_does_not_double_the_lines(tmp_path: Path) -> None:
    configure_logging(tmp_path)
    configure_logging(tmp_path)
    assert len(logging.getLogger("research_api").handlers) == 1


def test_a_rotated_file_is_private_too(tmp_path: Path) -> None:
    path = tmp_path / "api.log"
    handler = PrivateRotatingFileHandler(path, maxBytes=200, backupCount=2, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("rotation-test")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        for i in range(20):
            logger.info("line %s %s", i, "x" * 40)
    finally:
        logger.removeHandler(handler)
        handler.close()
    files = sorted(tmp_path.iterdir())
    assert len(files) == 3  # api.log, api.log.1, api.log.2
    assert {stat.S_IMODE(f.stat().st_mode) for f in files} == {0o600}


# --- a whole session of use, then a search of the log for secrets ---------------------------


@pytest.fixture
def logged(tmp_path: Path, seed: list[SeedUser]) -> tuple[TestClient, Path, list[str]]:
    """A live-mode app logging to a file, one admin session, and every secret that was used."""
    settings = prepare_live(live_settings(tmp_path / "home", log_dir=tmp_path / "home" / "logs"))
    container = build_container(settings, cheap_hashing=True)
    for account in seed:
        container.auth.users.create(
            account.username,
            account.display_name,
            account.role,
            password=account.password,
            must_change_password=account.must_change_password,
        )
    assert settings.log_dir
    configure_logging(settings.log_dir)
    client = TestClient(
        build_app(container),
        base_url="http://127.0.0.1:8090",
        headers={"Origin": "http://127.0.0.1:8090"},
        raise_server_exceptions=False,
    )
    secrets = [ANA["password"], BETO["password"], "temporary-test-0001"]
    return client, settings.log_dir / LOG_NAME, secrets


def lines(path: Path) -> list[dict[str, Any]]:
    for handler in logging.getLogger("research_api").handlers:
        handler.flush()
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_no_password_token_or_cookie_reaches_the_log(
    logged: tuple[TestClient, Path, list[str]],
) -> None:
    client, path, secrets = logged
    other = TestClient(
        client.app,
        base_url="http://127.0.0.1:8090",
        headers={"Origin": "http://127.0.0.1:8090"},
        raise_server_exceptions=False,
    )
    other.post("/api/v1/auth/login", json=BETO)
    secrets += [other.cookies[SESSION_COOKIE], other.cookies[CSRF_COOKIE]]
    client.post("/api/v1/auth/login", json={**ANA, "password": "a-wrong-password-1"})
    client.post("/api/v1/auth/login", json=ANA)
    secrets += [client.cookies[SESSION_COOKIE], client.cookies[CSRF_COOKIE]]
    headers = {CSRF_HEADER: client.cookies[CSRF_COOKIE]}
    client.get("/api/v1/auth/me")
    client.get("/api/v1/health?question=what-is-the-secret-question")
    client.get("/api/v1/admin/sessions")
    target = next(r["id"] for r in client.get("/api/v1/admin/sessions").json() if not r["current"])
    client.delete(f"/api/v1/admin/sessions/{target}", headers=headers)
    client.post(
        "/api/v1/auth/password",
        json={"current": ANA["password"], "new": "the-brand-new-ana-secret"},
        headers=headers,
    )
    secrets += ["the-brand-new-ana-secret", client.cookies[SESSION_COOKIE]]
    client.post(
        "/api/v1/auth/password",
        json={"current": "wrong-current-password", "new": "another-new-secret-here"},
        headers={CSRF_HEADER: client.cookies[CSRF_COOKIE]},
    )
    client.post("/api/v1/auth/logout", headers={CSRF_HEADER: client.cookies[CSRF_COOKIE]})
    text = path.read_text(encoding="utf-8")
    assert text.count("\n") > 10
    for secret in [
        *secrets,
        "argon2",
        "wrong-current-password",
        "another-new-secret",
        "what-is-the-secret",
    ]:
        assert secret not in text, secret
    assert "set-cookie" not in text.lower()


def test_neither_the_speech_key_nor_the_transcript_reaches_the_log(
    tmp_path: Path, seed: list[SeedUser]
) -> None:
    key_file = tmp_path / "stt.key"
    key_file.write_text("stt-key-never-in-a-log-0001")
    key_file.chmod(0o600)
    home = tmp_path / "home"
    settings = prepare_live(
        live_settings(
            home,
            log_dir=home / "logs",
            speech={"stt_endpoint": "http://127.0.0.1:8004/v1", "stt_api_key_file": key_file},
        )
    )
    container = build_container(settings, cheap_hashing=True)
    for account in seed:
        container.auth.users.create(
            account.username,
            account.display_name,
            account.role,
            password=account.password,
            must_change_password=account.must_change_password,
        )
    assert settings.log_dir and container.speech
    configure_logging(settings.log_dir)
    transcript = "the-private-words-of-a-member"
    container.speech._transcriber = OpenAiTranscriber(
        "http://127.0.0.1:8004/v1",
        "local-stt",
        "stt-key-never-in-a-log-0001",
        timeout_s=5,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"text": transcript, "language": "es"})
        ),
    )
    client = TestClient(
        build_app(container),
        base_url="http://127.0.0.1:8090",
        headers={"Origin": "http://127.0.0.1:8090"},
        raise_server_exceptions=False,
    )
    client.post("/api/v1/auth/login", json=ANA)
    reply = client.post(
        "/api/v1/speech/transcriptions",
        content=make_wav(1),
        headers={"Content-Type": "audio/wav", CSRF_HEADER: client.cookies[CSRF_COOKIE]},
    )
    assert reply.status_code == 200 and reply.json()["text"] == transcript
    text = (settings.log_dir / LOG_NAME).read_text(encoding="utf-8")
    assert text.count("\n") > 0
    assert "stt-key-never-in-a-log" not in text
    assert transcript not in text


def test_neither_the_reading_key_nor_the_text_reaches_the_log(
    tmp_path: Path, seed: list[SeedUser]
) -> None:
    key_file = tmp_path / "tts.key"
    key_file.write_text("tts-key-never-in-a-log-0002")
    key_file.chmod(0o600)
    home = tmp_path / "home"
    settings = prepare_live(
        live_settings(
            home,
            log_dir=home / "logs",
            speech={"tts_endpoint": "http://127.0.0.1:8005/v1", "tts_api_key_file": key_file},
        )
    )
    container = build_container(settings, cheap_hashing=True)
    for account in seed:
        container.auth.users.create(
            account.username,
            account.display_name,
            account.role,
            password=account.password,
            must_change_password=account.must_change_password,
        )
    assert settings.log_dir and container.synthesis
    configure_logging(settings.log_dir)
    private = "the-private-sentence-of-an-answer"
    state = {"down": False}

    def answer(request: httpx.Request) -> httpx.Response:
        if state["down"]:
            raise httpx.ConnectError(private, request=request)
        return httpx.Response(200, content=tone(0.1, 220))

    container.synthesis._synthesizer = OpenAiSynthesizer(
        "http://127.0.0.1:8005/v1",
        "local-tts",
        "tts-key-never-in-a-log-0002",
        timeout_s=5,
        transport=httpx.MockTransport(answer),
    )
    client = TestClient(
        build_app(container),
        base_url="http://127.0.0.1:8090",
        headers={"Origin": "http://127.0.0.1:8090"},
        raise_server_exceptions=False,
    )
    client.post("/api/v1/auth/login", json=ANA)
    headers = {CSRF_HEADER: client.cookies[CSRF_COOKIE]}
    url = "/api/v1/speech/synthesis"
    reply = client.post(url, json={"text": private}, headers=headers)
    assert reply.status_code == 200 and reply.headers["content-type"] == "audio/wav"
    state["down"] = True
    down = client.post(url, json={"text": private}, headers=headers)
    assert down.status_code == 503 and down.headers["retry-after"] == "30"
    assert private not in down.text
    text = (settings.log_dir / LOG_NAME).read_text(encoding="utf-8")
    assert text.count("\n") > 0
    assert "tts-key-never-in-a-log" not in text
    assert private not in text


def test_the_audit_trail_names_who_did_what(logged: tuple[TestClient, Path, list[str]]) -> None:
    client, path, _ = logged
    client.post("/api/v1/auth/login", json={**ANA, "password": "a-wrong-password-1"})
    client.post("/api/v1/auth/login", json=ANA)
    csrf = {CSRF_HEADER: client.cookies[CSRF_COOKIE]}
    client.post("/api/v1/auth/logout", headers=csrf)
    audit = [line for line in lines(path) if line["logger"] == "research_api.audit"]
    events = [(line["event"], line.get("username")) for line in audit]
    assert ("login.failure", "ana") in events
    assert ("login.success", "ana") in events
    assert ("logout", "ana") in events
    failure = next(line for line in audit if line["event"] == "login.failure")
    assert failure["address"] == "testclient"
    success = next(line for line in audit if line["event"] == "login.success")
    assert len(success["session_id"]) == 32


def test_blocked_attempts_password_changes_and_ended_sessions_are_logged(
    logged: tuple[TestClient, Path, list[str]], seed: list[SeedUser]
) -> None:
    client, path, _ = logged
    for _ in range(5):
        client.post("/api/v1/auth/login", json={**BETO, "password": "a-wrong-password-1"})
    client.post("/api/v1/auth/login", json=BETO)
    client.post("/api/v1/auth/login", json=ANA)
    csrf = {CSRF_HEADER: client.cookies[CSRF_COOKIE]}
    events = {line["event"] for line in lines(path) if "event" in line}
    assert {"login.blocked", "login.failure", "login.success", "request"} <= events
    client.post(
        "/api/v1/auth/password",
        json={"current": ANA["password"], "new": "the-brand-new-ana-secret"},
        headers=csrf,
    )
    assert "password.changed" in {line["event"] for line in lines(path) if "event" in line}


def test_a_typed_username_that_names_no_account_is_not_written_down(
    logged: tuple[TestClient, Path, list[str]],
) -> None:
    client, path, _ = logged
    for typed in ("my pass word 123", "hunter2-looks-like-a-name"):
        client.post("/api/v1/auth/login", json={"username": typed, "password": "x"})
    text = path.read_text(encoding="utf-8")
    assert "my pass word" not in text
    assert "hunter2-looks" not in text
    failures = [line for line in lines(path) if line.get("event") == "login.failure"]
    assert [f["username"] for f in failures] == ["(unknown)", "(unknown)"]


def test_a_request_line_has_the_route_template_not_the_path_or_the_query(
    logged: tuple[TestClient, Path, list[str]],
) -> None:
    client, path, _ = logged
    client.post("/api/v1/auth/login", json=ANA)
    client.get("/api/v1/health?question=private-words")
    client.delete(
        "/api/v1/admin/sessions/some-session-id", headers={CSRF_HEADER: client.cookies[CSRF_COOKIE]}
    )
    client.get("/api/v1/some/unknown/path-with-private-name")
    requests = [line for line in lines(path) if line.get("event") == "request"]
    routes = [(line["method"], line["route"], line["status"]) for line in requests]
    assert ("GET", "/api/v1/health", 200) in routes
    assert ("DELETE", "/api/v1/admin/sessions/{session_id}", 404) in routes
    assert ("GET", "-", 404) in routes
    text = path.read_text(encoding="utf-8")
    assert "private-words" not in text
    assert "private-name" not in text
    assert "some-session-id" not in text


def test_a_request_line_has_the_user_the_status_and_a_duration(
    logged: tuple[TestClient, Path, list[str]],
) -> None:
    client, path, _ = logged
    client.post("/api/v1/auth/login", json=ANA)
    client.get("/api/v1/auth/me")
    mine = [line for line in lines(path) if line.get("route") == "/api/v1/auth/me"]
    assert len(mine) == 1
    line = mine[0]
    assert line["username"] == "ana"
    assert line["status"] == 200
    assert line["duration_ms"] >= 0
    assert len(line["request_id"]) == 16
    anonymous = [x for x in lines(path) if x.get("route") == "/api/v1/auth/login"]
    assert "username" not in anonymous[0]


def test_a_refused_host_is_logged_as_a_request(logged: tuple[TestClient, Path, list[str]]) -> None:
    client, path, _ = logged
    client.get("/api/v1/health", headers={"Host": "evil.example"})
    refused = [line for line in lines(path) if line.get("status") == 403]
    assert refused and refused[0]["route"] == "-"
    assert "evil.example" not in path.read_text(encoding="utf-8")


def test_an_unhandled_error_is_logged_with_its_id_and_answered_without_a_trace(
    logged: tuple[TestClient, Path, list[str]],
) -> None:
    client, path, _ = logged

    @client.app.get("/boom")  # type: ignore[union-attr]
    def boom() -> None:
        raise RuntimeError("kaboom")

    response = client.get("/boom")
    assert response.status_code == 500
    assert "kaboom" not in response.text
    error_id = response.json()["error_id"]
    errors = [line for line in lines(path) if line["level"] == "ERROR"]
    assert errors and error_id in errors[0]["msg"]
    assert next(x for x in lines(path) if x.get("route") == "/boom")["status"] == 500


def test_the_clock_of_fake_mode_does_not_break_the_log(
    seed: list[SeedUser], client_for: Callable[..., TestClient]
) -> None:
    container = build_container(
        Settings.defaults(), fake=True, seed=seed, cheap_hashing=True, clock=MovableClock()
    )
    assert client_for(build_app(container)).get("/api/v1/health").status_code == 200


def test_neither_a_circuit_nor_a_value_of_the_export_reaches_the_log(
    tmp_path: Path, seed: list[SeedUser], client_for: Callable[..., TestClient]
) -> None:
    secret_paper = FakePaper(
        key="private-circuits",
        title="A private paper (synthetic)",
        source_name="private-source-name.tex",
        source_kind="latex",
        authors=("A. Example",),
        year=2026,
        pages=(
            FakePage(
                "```latex\n\\begin{quantikz} \\lstick{private-wire-legend} & "
                "\\gate{R_y(\\theta)} & \\qw \\end{quantikz}\n```",
                title="Private",
            ),
        ),
    )
    container = build_container(
        Settings.defaults(),
        fake=True,
        seed=seed,
        cheap_hashing=True,
        document_reader=FakeDocumentReader((secret_paper,)),
    )
    configure_logging(tmp_path)
    client = client_for(build_app(container))
    client.post("/api/v1/auth/login", json=ANA)
    headers = {CSRF_HEADER: client.cookies[CSRF_COOKIE]}
    pasted = {
        "format": "circuit/1",
        "wires": 1,
        "wire_labels": ["1 = pasted-private-legend"],
        "parts": [[[{"kind": "gate", "label": "R_y(\\theta)", "targets": [1]}]]],
    }
    calls = [
        ("post", "/api/v1/circuits/qasm", {"circuit_json": json.dumps(pasted)}),
        (
            "post",
            "/api/v1/circuits/qasm",
            {"circuit_json": json.dumps(pasted), "values": {"theta": "0.123456789"}},
        ),
        (
            "post",
            "/api/v1/circuits/qasm",
            {"circuit_json": json.dumps(pasted), "values": {"private-bad-key": "1"}},
        ),
        ("get", f"/api/v1/documents/{secret_paper.short_id}/circuits", None),
        (
            "post",
            f"/api/v1/documents/{secret_paper.short_id}/qasm",
            {"values": {"theta": "0.987654321"}},
        ),
        ("post", f"/api/v1/documents/{secret_paper.short_id}/qasm", {"values": {"x": "1 +"}}),
    ]
    for method, url, body in calls:
        assert client.request(method, url, json=body, headers=headers).status_code in (200, 422)
    path = tmp_path / LOG_NAME
    routes = {line.get("route") for line in lines(path) if line.get("event") == "request"}
    assert {
        "/api/v1/circuits/qasm",
        "/api/v1/documents/{doc_id}/circuits",
        "/api/v1/documents/{doc_id}/qasm",
    } <= routes
    text = path.read_text(encoding="utf-8")
    for secret in (
        "private-wire-legend",
        "pasted-private-legend",
        "R_y",
        "0.123456789",
        "0.987654321",
        "private-bad-key",
        "private-source-name",
        secret_paper.short_id,
        "OPENQASM",
    ):
        assert secret not in text, secret
