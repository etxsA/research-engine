"""Wrapper tests that need no model: a stub engine, a fake recognizer behind the VAD path, synthetic WAVs."""

import io
import os
import sys
import types
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stt_server

KEY = "test-key"
AUTH = {"Authorization": f"Bearer {KEY}"}


def wav(seconds=1.0, rate=16_000, channels=1, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(seconds * rate) * channels * width)
    return buf.getvalue()


class StubEngine:
    def __init__(self, text="hola qubit"):
        self.text, self.calls = text, []

    def transcribe(self, samples):
        self.calls.append(len(samples))
        return self.text


@pytest.fixture
def engine():
    return StubEngine()


@pytest.fixture
def client(engine):
    return TestClient(stt_server.create_app(engine, KEY))


def post(client, data, headers=AUTH, **fields):
    return client.post(
        "/v1/audio/transcriptions",
        headers=headers,
        files={"file": ("a.wav", data, "audio/wav")},
        data=fields,
    )


def test_health_needs_no_key(client):
    assert client.get("/health").json() == {"status": "ok", "model": "local-stt"}


def test_transcribes_multipart(client, engine):
    r = post(client, wav(2.0), language="es", model="local-stt")
    assert r.status_code == 200
    assert r.json() == {"text": "hola qubit", "language": "es", "duration": 2.0}
    assert engine.calls == [32_000]


def test_transcribes_raw_body(client):
    r = client.post(
        "/v1/audio/transcriptions?language=en",
        headers={**AUTH, "Content-Type": "audio/wav"},
        content=wav(),
    )
    assert r.status_code == 200 and r.json()["language"] == "en"


def test_language_is_optional(client):
    assert post(client, wav()).json()["language"] is None


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": KEY}])
def test_rejects_missing_or_wrong_key(client, engine, headers):
    assert post(client, wav(), headers=headers).status_code == 401
    assert engine.calls == []


def test_key_checked_before_the_body_is_judged(client):
    r = client.post(
        "/v1/audio/transcriptions", headers={"Content-Type": "text/plain"}, content=b"x"
    )
    assert r.status_code == 401


def test_rejects_oversized_upload(client, engine):
    r = post(client, wav(70.0))  # 2.1 MB
    assert r.status_code == 413 and engine.calls == []
    assert (
        client.post(
            "/v1/audio/transcriptions",
            headers={**AUTH, "Content-Type": "audio/wav"},
            content=b"0" * (2 * 1024 * 1024 + 1),
        ).status_code
        == 413
    )


def test_rejects_too_long_clip(client):
    assert post(client, wav(61.0, rate=16_000)).status_code == 413  # 1.95 MB, under the byte cap


def test_accepts_clip_at_the_limits(client):
    assert post(client, wav(60.0)).status_code == 200


@pytest.mark.parametrize("data", [wav(rate=8_000), wav(channels=2), wav(width=1), b"not a wav"])
def test_rejects_unsupported_audio(client, data):
    assert post(client, data).status_code == 422


def test_rejects_unsupported_content_type(client):
    r = client.post(
        "/v1/audio/transcriptions", headers={**AUTH, "Content-Type": "application/json"}, json={}
    )
    assert r.status_code == 422


def test_rejects_bad_fields(client):
    assert post(client, wav(), language="fr").status_code == 422
    assert post(client, wav(), model="whisper-1").status_code == 422
    r = client.post(
        "/v1/audio/transcriptions", headers=AUTH, files={"other": ("a.wav", wav(), "audio/wav")}
    )
    assert r.status_code == 422


def test_samples_are_float32_unit_range():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(16_000)
        w.writeframes(np.array([0, 16384, -32768], dtype="<i2").tobytes())
    out = stt_server.decode_wav(buf.getvalue())
    assert out.dtype == np.float32 and out.tolist() == [0.0, 0.5, -1.0]


def test_vad_path_drops_silence_and_joins_segments():
    seg = lambda t: types.SimpleNamespace(text=t)
    fake = types.SimpleNamespace(
        recognize=lambda s, sample_rate: iter([seg(" hola "), seg(""), seg("T2")])
    )
    assert stt_server.VadEngine(fake).transcribe(np.zeros(16_000, np.float32)) == "hola T2"
    silent = types.SimpleNamespace(recognize=lambda s, sample_rate: iter([]))
    assert stt_server.VadEngine(silent).transcribe(np.zeros(16_000, np.float32)) == ""


def test_silence_gives_empty_text_through_the_api():
    c = TestClient(stt_server.create_app(StubEngine(text=""), KEY))
    assert post(c, wav()).json()["text"] == ""


def test_key_file_must_be_private(tmp_path):
    f = tmp_path / "key"
    f.write_text("secret\n")
    f.chmod(0o644)
    with pytest.raises(SystemExit):
        stt_server.read_key(str(f))
    f.chmod(0o600)
    assert stt_server.read_key(str(f)) == "secret"
    f.write_text("\n")
    with pytest.raises(SystemExit):
        stt_server.read_key(str(f))


def test_factory_needs_a_key_file(monkeypatch):
    monkeypatch.delenv("STT_API_KEY_FILE", raising=False)
    with pytest.raises(SystemExit):
        stt_server.create_app_from_env()
    assert os.environ.get("STT_API_KEY_FILE") is None
