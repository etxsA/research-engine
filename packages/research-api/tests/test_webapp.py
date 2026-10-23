"""The built web app served from the API's origin."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import LOOPBACK, call, http_scope
from starlette.websockets import WebSocketDisconnect

from research_api.bootstrap import Settings, SettingsError, build_container, load_settings
from research_api.bootstrap.container import FAKE_ENV, WEB_DIR_ENV, container_from_env
from research_api.bootstrap.seed import SeedUser
from research_api.entrypoints.http import build_app
from research_api.entrypoints.http.webapp import CSP, IMMUTABLE, REVALIDATE

PROBLEM = "application/problem+json"
ClientFor = Callable[..., TestClient]
HOST = [(b"host", b"127.0.0.1:8090")]


def make_build(folder: Path, marker: str = "one") -> Path:
    """A folder shaped like apps/web/dist, with made-up content."""
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(f"<!doctype html><title>{marker}</title>", encoding="utf-8")
    (folder / "assets" / "app-3f2a9c.js").write_text("console.log(1)", encoding="utf-8")
    (folder / "assets" / "app-3f2a9c.css").write_text("body{}", encoding="utf-8")
    (folder / "theme-init.js").write_text("// theme", encoding="utf-8")
    (folder / "version.json").write_text('{"commit": "abc"}', encoding="utf-8")
    return folder


@pytest.fixture
def build(tmp_path: Path) -> Path:
    return make_build(tmp_path / "dist")


def app_serving(folder: Path, seed: list[SeedUser]):
    settings = Settings(web_dir=folder)
    return build_app(build_container(settings, fake=True, seed=seed, cheap_hashing=True))


@pytest.fixture
def web(build: Path, seed: list[SeedUser], client_for: ClientFor) -> TestClient:
    return client_for(app_serving(build, seed))


def test_the_home_page_is_served_with_a_strict_policy(web: TestClient) -> None:
    done = web.get("/")
    assert done.status_code == 200
    assert "<title>one</title>" in done.text
    assert done.headers["content-type"] == "text/html; charset=utf-8"
    assert done.headers["cache-control"] == REVALIDATE
    assert done.headers["content-security-policy"] == CSP
    assert "script-src 'self'" in CSP
    assert "frame-ancestors 'none'" in CSP
    assert done.headers["x-content-type-options"] == "nosniff"
    assert done.headers["referrer-policy"] == "same-origin"


def test_a_route_of_the_single_page_app_gets_the_home_page(web: TestClient) -> None:
    for path in ("/login", "/ask", "/sets/some-set/versions/2", "/circuits/"):
        done = web.get(path)
        assert done.status_code == 200, path
        assert "<title>one</title>" in done.text


def test_files_have_the_right_type_and_hashed_ones_are_cached_for_good(web: TestClient) -> None:
    js = web.get("/assets/app-3f2a9c.js")
    assert js.headers["content-type"] == "text/javascript; charset=utf-8"
    assert js.headers["cache-control"] == IMMUTABLE
    assert web.get("/assets/app-3f2a9c.css").headers["content-type"].startswith("text/css")
    theme = web.get("/theme-init.js")
    assert theme.headers["cache-control"] == REVALIDATE
    assert web.get("/version.json").json() == {"commit": "abc"}


def test_a_missing_file_is_not_answered_with_the_home_page(web: TestClient) -> None:
    for path in ("/assets/app-old.js", "/missing.css", "/assets/"):
        done = web.get(path)
        if path == "/assets/":
            assert done.status_code == 200  # a folder path is a route of the app
            continue
        assert done.status_code == 404, path
        assert done.headers["content-type"] == PROBLEM


def test_the_api_keeps_its_own_routes_and_its_own_errors(web: TestClient) -> None:
    assert web.get("/api/v1/health").json() == {"ok": True}
    for method, path in (
        ("GET", "/api/v1/nothing"),
        ("GET", "/api"),
        ("GET", "/api/"),
        ("DELETE", "/api/v1/nothing"),
    ):
        done = web.request(method, path)
        assert done.status_code in (403, 404), (method, path)
        assert "text/html" not in done.headers.get("content-type", "")
    assert web.get("/api/v1/nothing").headers["content-type"] == PROBLEM


def test_a_wrong_method_on_an_api_route_stays_a_405_with_the_web_app(
    web: TestClient, client: TestClient
) -> None:
    for method, path in (
        ("DELETE", "/api/v1/health"),
        ("POST", "/api/v1/health"),
        ("PUT", "/api/v1/auth/login"),
    ):
        plain = client.request(method, path)
        served = web.request(method, path)
        assert plain.status_code == 405, (method, path)
        assert served.status_code == 405, (method, path)
        assert served.headers["allow"] == plain.headers["allow"], (method, path)
        assert served.headers["content-type"] == PROBLEM


def test_a_websocket_is_closed_not_answered_with_the_page(web: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect), web.websocket_connect("/api/v1/health"):
        pass


def test_a_write_to_a_web_path_is_not_allowed(web: TestClient) -> None:
    done = web.post("/login")
    assert done.status_code == 405
    assert done.headers["allow"] == "GET, HEAD"
    assert done.headers["content-type"] == PROBLEM
    assert web.put("/anything").status_code == 405


def test_head_and_conditional_requests_work(web: TestClient) -> None:
    head = web.head("/assets/app-3f2a9c.js")
    assert head.status_code == 200
    assert head.content == b""
    first = web.get("/theme-init.js")
    again = web.get("/theme-init.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["content-security-policy"] == CSP
    assert again.content == b""
    assert web.get("/theme-init.js", headers={"If-None-Match": '"other"'}).status_code == 200
    stamp = first.headers["last-modified"]
    assert web.get("/theme-init.js", headers={"If-Modified-Since": stamp}).status_code == 304
    old = "Mon, 01 Jan 2001 00:00:00 GMT"
    assert web.get("/theme-init.js", headers={"If-Modified-Since": old}).status_code == 200
    assert web.get("/theme-init.js", headers={"If-Modified-Since": "garbage"}).status_code == 200


def test_a_path_cannot_leave_the_folder(tmp_path: Path, build: Path, seed: list[SeedUser]) -> None:
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    (build / "link.txt").symlink_to(tmp_path / "secret.txt")
    app = app_serving(build, seed)
    for path in ("/../secret.txt", "/assets/../../secret.txt", "/link.txt", "/a\\..\\secret.txt"):
        scope = {**http_scope(headers=HOST), "path": path}
        sent = call(app, scope)
        start = next(m for m in sent if m["type"] == "http.response.start")
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        assert start["status"] == 404, path
        assert b"outside" not in body


def test_a_nul_in_the_path_is_a_404(build: Path, seed: list[SeedUser]) -> None:
    sent = call(app_serving(build, seed), {**http_scope(headers=HOST), "path": "/a\x00b"})
    assert next(m for m in sent if m["type"] == "http.response.start")["status"] == 404


def test_pointing_the_link_at_a_new_build_changes_the_answer_without_a_restart(
    tmp_path: Path, seed: list[SeedUser], client_for: ClientFor
) -> None:
    first = make_build(tmp_path / "web" / "v1", "one")
    second = make_build(tmp_path / "web" / "v2", "two")
    current = tmp_path / "web" / "current"
    current.symlink_to(first)
    web = client_for(app_serving(current, seed))
    assert "<title>one</title>" in web.get("/").text
    current.unlink()
    current.symlink_to(second)
    assert "<title>two</title>" in web.get("/").text


def test_without_an_installed_build_the_answer_says_so(
    tmp_path: Path, seed: list[SeedUser], client_for: ClientFor
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    for folder in (tmp_path / "nothing", empty):
        done = client_for(app_serving(folder, seed)).get("/")
        assert done.status_code == 503
        assert done.headers["content-type"] == PROBLEM
        assert "not installed" in done.json()["detail"]
        assert done.headers["content-security-policy"] == CSP


def test_without_a_web_dir_nothing_is_served(client: TestClient) -> None:
    done = client.get("/")
    assert done.status_code == 404
    assert done.headers["content-type"] == PROBLEM
    assert "content-security-policy" not in done.headers


def test_the_host_and_bind_checks_still_cover_the_web_app(
    build: Path, seed: list[SeedUser], client_for: ClientFor
) -> None:
    web = client_for(app_serving(build, seed), headers={"Host": "rebind.attacker.example"})
    assert web.get("/").status_code == 403
    elsewhere = client_for(app_serving(build, seed), base_url="http://192.0.2.7:8090")
    assert elsewhere.get("/").status_code == 403


def test_the_access_log_names_no_web_path(
    web: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="research_api.access"):
        web.get("/sets/private-set-name?q=secret-question")
    lines = [r for r in caplog.records if r.name == "research_api.access"]
    assert lines
    assert all(getattr(r, "route", None) == "-" for r in lines)
    assert "private-set-name" not in " ".join(str(r.__dict__) for r in lines)


def test_the_page_is_not_in_the_contract(
    web: TestClient, build: Path, seed: list[SeedUser]
) -> None:
    paths = set(app_serving(build, seed).openapi()["paths"])
    assert not any(p in ("/", "/{path}") for p in paths)
    assert all(p.startswith("/api/") for p in paths)


def test_the_settings_file_can_name_the_folder(tmp_path: Path) -> None:
    path = tmp_path / "s.toml"
    path.write_text(f'web_dir = "{tmp_path}/web/current"\n', encoding="utf-8")
    assert load_settings(path).web_dir == tmp_path / "web" / "current"
    path.write_text('web_dir = "web/current"\n', encoding="utf-8")
    with pytest.raises(SettingsError, match="absolute"):
        load_settings(path)


def test_the_environment_names_the_folder_for_the_factory(tmp_path: Path) -> None:
    container = container_from_env({FAKE_ENV: "1", WEB_DIR_ENV: str(tmp_path)})
    assert container.settings.web_dir == tmp_path
    assert container_from_env({FAKE_ENV: "1"}).settings.web_dir is None
    with pytest.raises(SettingsError, match=WEB_DIR_ENV):
        container_from_env({FAKE_ENV: "1", WEB_DIR_ENV: "relative/dist"})


def test_the_page_is_reachable_over_the_address_people_type(
    build: Path, seed: list[SeedUser], client_for: ClientFor
) -> None:
    web = client_for(app_serving(build, seed), base_url=LOOPBACK)
    assert web.get("/").status_code == 200


def test_a_symlink_loop_in_the_folder_is_a_404(build: Path, seed: list[SeedUser]) -> None:
    (build / "loop").symlink_to(build / "loop")
    sent = call(app_serving(build, seed), {**http_scope(headers=HOST), "path": "/loop"})
    assert next(m for m in sent if m["type"] == "http.response.start")["status"] == 404
