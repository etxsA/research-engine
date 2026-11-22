"""Wrapper tests that need no model or GPU: a stub engine and the WAV that comes back."""

import asyncio
import io
import sys
import threading
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tts_server

KEY = "test-key"
AUTH = {"Authorization": f"Bearer {KEY}"}
RATE = 24_000


class StubEngine:
    sample_rate = RATE
    voices = ("Ryan", "Vivian")

    def __init__(self, seconds=0.5):
        self.seconds, self.calls = seconds, []

    def synthesize(self, text, voice, language):
        self.calls.append((text, voice, language))
        t = np.arange(int(self.seconds * RATE)) / RATE
        return (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


@pytest.fixture
def engine():
    return StubEngine()


@pytest.fixture
def client(engine):
    return TestClient(tts_server.create_app(engine, KEY))


def speak(client, headers=AUTH, **fields):
    body = {"model": "local-tts", "input": "Hola.", "voice": "Ryan", "response_format": "wav"}
    return client.post("/v1/audio/speech", headers=headers, json={**body, **fields})


def test_health_needs_no_key(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_models_lists_the_served_model_and_voices(client):
    r = client.get("/v1/models", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["data"] == [
        {"id": "local-tts", "object": "model", "voices": ["Ryan", "Vivian"]}
    ]
    assert client.get("/v1/models").status_code == 401


def test_speech_returns_mono_16_bit_wav(client, engine):
    r = speak(client, language="es")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.headers["cache-control"] == "no-store"
    with wave.open(io.BytesIO(r.content), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, RATE)
        assert w.getnframes() == RATE // 2
    assert engine.calls == [("Hola.", "Ryan", "es")]


def test_language_is_optional_and_voice_is_case_insensitive(client, engine):
    assert speak(client, voice="vivian").status_code == 200
    assert engine.calls == [("Hola.", "Vivian", None)]


def test_samples_are_clipped_to_16_bit():
    data = tts_server.encode_wav(np.array([0.0, 2.0, -2.0, 0.5], dtype=np.float32), 8_000)
    with wave.open(io.BytesIO(data), "rb") as w:
        pcm = np.frombuffer(w.readframes(4), dtype="<i2")
    assert pcm.tolist() == [0, 32767, -32767, 16383]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": KEY}])
def test_rejects_missing_or_wrong_key(client, engine, headers):
    assert speak(client, headers=headers).status_code == 401
    assert engine.calls == []


def test_key_is_checked_before_the_body_is_judged(client):
    r = client.post("/v1/audio/speech", content=b"not json", headers={"Content-Type": "text/plain"})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "fields",
    [
        {"input": ""},
        {"input": "x" * (tts_server.MAX_CHARS + 1)},
        {"voice": ""},
        {"response_format": "mp3"},
        {"language": "fr"},
    ],
)
def test_rejects_bad_input(client, engine, fields):
    assert speak(client, **fields).status_code == 422
    assert engine.calls == []


def test_blank_input_is_rejected_after_trimming(client, engine):
    assert speak(client, input="   \n").status_code == 422 and engine.calls == []


def test_rejects_unknown_voice_and_model(client, engine):
    assert speak(client, voice="nobody").status_code == 400
    assert speak(client, model="tts-1").status_code == 400
    assert engine.calls == []


def test_input_is_accepted_at_the_limit(client):
    assert speak(client, input="a" * tts_server.MAX_CHARS).status_code == 200


def test_loading_gives_503_until_the_model_is_there():
    gate = threading.Event()
    engine = StubEngine()

    def loader():
        gate.wait(5)
        return engine

    with TestClient(tts_server.create_app(None, KEY, loader=loader)) as c:
        r = c.get("/health")
        assert r.status_code == 503 and r.json() == {"ok": False}
        busy = speak(c)
        assert busy.status_code == 503 and busy.headers["retry-after"] == "5"
        gate.set()
        for _ in range(100):
            if c.get("/health").status_code == 200:
                break
            time.sleep(0.02)
        assert c.get("/health").json() == {"ok": True}
        assert speak(c).status_code == 200


def test_requests_run_one_at_a_time():
    active, peak, lock = 0, 0, threading.Lock()

    class Slow(StubEngine):
        def synthesize(self, text, voice, language):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return super().synthesize(text, voice, language)

    app = tts_server.create_app(Slow(), KEY)
    body = {"model": "local-tts", "input": "Hola.", "voice": "Ryan"}

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            rs = await asyncio.gather(
                *[c.post("/v1/audio/speech", headers=AUTH, json=body) for _ in range(4)]
            )
        return [r.status_code for r in rs]

    assert asyncio.run(run()) == [200] * 4 and peak == 1


def test_a_failed_load_exits_the_process_with_a_reason(monkeypatch, capfd):
    exits = []
    monkeypatch.setattr(tts_server.os, "_exit", exits.append)

    def loader():
        raise SystemExit("TTS_VOICES_DIR is not set")

    with TestClient(tts_server.create_app(None, KEY, loader=loader)):
        for _ in range(100):
            if exits:
                break
            time.sleep(0.02)
    assert exits == [1]
    assert "TTS_VOICES_DIR is not set" in capfd.readouterr().err


def test_key_file_must_be_private(tmp_path):
    f = tmp_path / "key"
    f.write_text("secret\n")
    f.chmod(0o644)
    with pytest.raises(SystemExit):
        tts_server.read_key(str(f))
    f.chmod(0o600)
    assert tts_server.read_key(str(f)) == "secret"
    f.write_text("\n")
    with pytest.raises(SystemExit):
        tts_server.read_key(str(f))


def test_factory_needs_a_key_file(monkeypatch):
    monkeypatch.delenv("TTS_API_KEY_FILE", raising=False)
    with pytest.raises(SystemExit):
        tts_server.create_app_from_env()


def test_qwen_engine_maps_the_language_and_rate():
    seen = []

    def generate(text, voice, language):
        seen.append((text, voice, language))
        return [np.zeros(240, dtype=np.float32)], 22_050

    engine = tts_server.QwenEngine(("ryan",), generate)
    out = engine.synthesize("Hola", "ryan", "es")
    assert out.shape == (240,) and engine.sample_rate == 22_050 and engine.voices == ("ryan",)
    engine.synthesize("Hi", "ryan", "en")
    engine.synthesize("Hi", "ryan", None)
    assert [s[2] for s in seen] == ["Spanish", "English", "Auto"]


def test_clone_clips_pair_each_wav_with_its_text(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"")
    (tmp_path / "a.txt").write_text(" hola \n")
    (tmp_path / "orphan.wav").write_bytes(b"")
    assert tts_server._clone_clips(str(tmp_path)) == {"a": (str(tmp_path / "a.wav"), "hola")}
    (tmp_path / "a.txt").unlink()
    with pytest.raises(SystemExit):
        tts_server._clone_clips(str(tmp_path))


def test_token_cap_follows_the_text_and_stays_bounded():
    assert tts_server.token_cap("Hola.") == 39
    assert tts_server.token_cap("x" * 120) == 384
    assert tts_server.token_cap("x" * tts_server.MAX_CHARS) == tts_server.MAX_NEW_TOKENS
