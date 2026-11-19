"""Shared helpers of the tests (fixtures live in conftest.py)."""

from __future__ import annotations

import asyncio
import functools
import io
import time
import wave
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from starlette.types import Message

from research_api.bootstrap import Settings, open_database


def arun[**P, R](test: Callable[P, Awaitable[R]]) -> Callable[P, R]:
    """Run an ``async def`` test in its own event loop (no async pytest plugin is installed)."""

    @functools.wraps(test)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        return asyncio.run(test(*args, **kwargs))  # type: ignore[arg-type]

    return wrapper  # type: ignore[return-value]


def past_the_tick(path: Path, ctime_ns: int, bound: float = 3.0) -> None:
    """Wait until the filesystem clock has moved past ``ctime_ns``, so a write that follows is told
    from the one that made it (the change time has the clock of the modification time)."""
    probe, deadline = path.with_name("probe"), time.monotonic() + bound
    try:
        while True:
            probe.write_bytes(b"")
            if probe.stat().st_ctime_ns != ctime_ns:
                return
            assert time.monotonic() < deadline, "the filesystem clock did not move"
            time.sleep(0.01)
    finally:
        probe.unlink(missing_ok=True)


LOOPBACK = "http://127.0.0.1:8090"
MEMBER = {"username": "beto", "password": "test-password-beto-02"}
ORIGIN = {"Origin": LOOPBACK}
SEED_FILE = Path(__file__).parent / "fixtures" / "seed-users.json"

# H on wire 1, then X on wire 2 controlled by wire 1: the circuit the HTTP tests send as a good one.
BELL = {
    "format": "circuit/1",
    "wires": 2,
    "parts": [
        [
            [{"kind": "gate", "label": "H", "targets": [1]}],
            [{"kind": "x", "label": "X", "targets": [2], "controls": [1]}],
        ]
    ],
}


def make_wav(
    seconds: float = 1.0, *, rate: int = 16_000, channels: int = 1, width: int = 2
) -> bytes:
    """A silent PCM WAV of ``seconds`` (a clip the speech route accepts, with the defaults)."""
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(width)
        clip.setframerate(rate)
        clip.writeframes(b"\x01" * (int(seconds * rate) * channels * width))
    return out.getvalue()


class MovableClock:
    """A clock the test moves by hand, to cross session and rate-limit windows."""

    def __init__(self, at: datetime = datetime(2026, 1, 1, tzinfo=UTC)) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def advance(self, **delta: float) -> None:
        self.at += timedelta(**delta)


def live_settings(folder: Path, **overrides: object) -> Settings:
    """Settings for a live container whose files sit in a private test folder."""
    folder.mkdir(mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    values: dict[str, object] = {
        "data_dir": folder / "data",
        "db_path": folder / "db" / "research-api.sqlite3",
    }
    return Settings.model_validate({**values, **overrides})


def write_config(path: Path, text: str) -> Path:
    """A settings file the way the service wants it: mode 0600."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path


def prepare_live(settings: Settings) -> Settings:
    """Make the database of live settings (what `research-api db upgrade` does) and return them."""
    database = open_database(settings, create=True)
    database.upgrade()
    database.close()
    return settings


# --- calling an ASGI app by hand, for the middleware tests ---------------------------------


def call(app: Any, scope: dict[str, Any]) -> list[Message]:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


async def ok_app(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def http_scope(
    method: str = "GET", headers: list[tuple[bytes, bytes]] | None = None
) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": "/api/v1/health",
        "headers": headers or [],
        "server": ("127.0.0.1", 8090),
        "query_string": b"",
        "scheme": "http",
        "root_path": "",
    }


def status_of(messages: list[Message]) -> int:
    return next(m["status"] for m in messages if m["type"] == "http.response.start")


def sign_in_as_member(client: Any) -> Any:
    """Sign a client in as a seeded member and make it send the CSRF header on every request."""
    done = client.post("/api/v1/auth/login", json=MEMBER)
    assert done.status_code == 204, done.text
    client.headers["X-CSRF-Token"] = client.cookies.get("re_csrf", "")
    return client
