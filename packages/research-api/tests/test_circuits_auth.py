"""The circuit routes need a signed-in member, like every route of the API except sign-in."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import BELL, MEMBER, sign_in_as_member

from research_api.adapters.fake.corpus import TELEPORT
from research_api.application.auth import Authenticate, Authenticated
from research_api.entrypoints.cli import openapi_document
from research_api.entrypoints.http.security import CSRF_HEADER, SESSION_COOKIE

PROBLEM = "application/problem+json"
UNAUTHENTICATED = "urn:research-api:problem:unauthenticated"
BELL_TEXT = json.dumps(BELL)
BELL_STATE = [0.7071067811865476, 0, 0, 0.7071067811865476]

ROUTES = [
    ("post", "/api/v1/circuits/parse", {"latex": "x"}),
    ("post", "/api/v1/circuits/run", {"circuit_json": BELL_TEXT}),
    (
        "post",
        "/api/v1/circuits/check",
        {"circuit_json": BELL_TEXT, "target": {"state": BELL_STATE}},
    ),
    ("get", "/api/v1/circuits/conventions", None),
    ("post", "/api/v1/circuits/qasm", {"circuit_json": BELL_TEXT}),
    ("get", f"/api/v1/documents/{TELEPORT.short_id}/circuits", None),
    ("post", f"/api/v1/documents/{TELEPORT.short_id}/qasm", {}),
]
TEMPLATES = {
    f"/api/v1/documents/{TELEPORT.short_id}/circuits": "/api/v1/documents/{doc_id}/circuits",
    f"/api/v1/documents/{TELEPORT.short_id}/qasm": "/api/v1/documents/{doc_id}/qasm",
}


def send(client: TestClient, method: str, path: str, body: Any, **kwargs: Any) -> Any:
    return client.request(method, path, json=body, **kwargs)


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_no_session_is_a_401(client: TestClient, method: str, path: str, body: Any) -> None:
    response = send(client, method, path, body)
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM)
    assert response.json()["type"] == UNAUTHENTICATED


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_a_member_is_served(client: TestClient, method: str, path: str, body: Any) -> None:
    sign_in_as_member(client)
    assert send(client, method, path, body).status_code == 200


@pytest.mark.parametrize("path", ["run", "check"])
def test_a_member_gets_a_result_and_not_a_refusal(client: TestClient, path: str) -> None:
    sign_in_as_member(client)
    body = next(b for m, p, b in ROUTES if p.endswith(f"/{path}"))
    assert client.post(f"/api/v1/circuits/{path}", json=body).json()["status"] == "ok"


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_a_dead_session_is_a_401_that_clears_the_cookies(
    client: TestClient, method: str, path: str, body: Any
) -> None:
    client.cookies.set(SESSION_COOKIE, "not-a-session")
    response = send(client, method, path, body)
    assert response.status_code == 401
    assert SESSION_COOKIE in response.headers["set-cookie"]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_a_temporary_password_must_be_changed_first(
    client: TestClient, method: str, path: str, body: Any
) -> None:
    done = client.post(
        "/api/v1/auth/login", json={"username": "temp", "password": "temporary-test-0001"}
    )
    assert done.status_code == 204
    client.headers[CSRF_HEADER] = client.cookies.get("re_csrf", "")
    response = send(client, method, path, body)
    assert response.status_code == 403
    assert response.json()["detail"] == "Change your password first."


@pytest.mark.parametrize(("method", "path", "body"), [r for r in ROUTES if r[0] == "post"])
def test_a_write_without_the_csrf_header_is_a_403(
    client: TestClient, method: str, path: str, body: Any
) -> None:
    assert client.post("/api/v1/auth/login", json=MEMBER).status_code == 204
    response = send(client, method, path, body)
    assert response.status_code == 403
    assert response.json()["detail"] == "The CSRF token is missing or wrong."


def test_a_caller_with_no_session_gets_the_401_before_the_body_is_read(
    client: TestClient,
) -> None:
    for body in (b"not json", b'{"latex": "a", "latex": "b"}', b"x" * 2_000_000):
        response = client.post(
            "/api/v1/circuits/parse", content=body, headers={"content-type": "application/json"}
        )
        assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_the_session_is_read_once_per_request(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    body: Any,
) -> None:
    sign_in_as_member(client)
    reads: list[str | None] = []
    original = Authenticate.__call__

    def counting(self: Authenticate, token: str | None) -> Authenticated:
        reads.append(token)
        return original(self, token)

    monkeypatch.setattr(Authenticate, "__call__", counting)
    assert send(client, method, path, body).status_code == 200
    assert len(reads) == 1


def test_the_contract_lists_the_session_cookie_on_the_circuit_operations() -> None:
    spec = json.loads(openapi_document())
    for _, path, _ in ROUTES:
        for operation in spec["paths"][TEMPLATES.get(path, path)].values():
            assert operation["security"] == [{"sessionCookie": []}]


def test_a_caller_with_no_session_gets_the_401_before_a_paper_is_read(
    client: TestClient,
) -> None:
    for body in (b"not json", b'{"values": {}, "values": {}}', b"x" * 2_000_000):
        response = client.post(
            f"/api/v1/documents/{TELEPORT.short_id}/qasm",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 401
