"""Speech-to-text server of the research engine: an OpenAI-style transcription route on CPU.

    POST /v1/audio/transcriptions   Bearer key; multipart "file" (WAV 16 kHz mono, 16-bit) plus
                                    optional "language" (es or en) and "model" (local-stt), or the
                                    raw WAV as the body with Content-Type audio/wav (and
                                    ?language=, ?model=)
    GET  /health                    no key; answers only once the models are loaded

Silence is dropped by Silero VAD before recognition, so a silent clip gives an empty text. Audio
stays in memory and is never logged. Settings come from the environment (see serving/serve.sh and
env.example).
"""

from __future__ import annotations

import hmac
import io
import os
import stat
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import anyio
import numpy as np
import onnx_asr
import onnxruntime as rt
from fastapi import FastAPI, HTTPException, Request
from huggingface_hub import snapshot_download
from starlette.formparsers import MultiPartParser

SERVED_NAME = "local-stt"
SAMPLE_RATE = 16_000
LANGUAGES = ("es", "en")
MAX_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 60.0
FORM_OVERHEAD = 16 * 1024  # multipart framing and the small text fields around the file

# A 2 MiB upload must not spill to a temporary file: keep the whole form in memory.
MultiPartParser.spool_max_size = MAX_BYTES + FORM_OVERHEAD


class Engine(Protocol):
    def transcribe(self, samples: np.ndarray) -> str:
        """Text of the speech in 16 kHz mono float32 samples; empty when there is none."""


class VadEngine:
    """A recognizer behind a VAD: recognize(samples, sample_rate=...) yields segments with .text."""

    def __init__(self, recognizer):
        self._recognizer = recognizer

    def transcribe(self, samples: np.ndarray) -> str:
        segments = self._recognizer.recognize(samples, sample_rate=SAMPLE_RATE)
        return " ".join(t for s in segments if (t := s.text.strip()))


def read_key(path: str) -> str:
    """The API key from a file that only its owner can read."""
    mode = Path(path).stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit(f"{path} must not be readable by group or others (chmod 600)")
    key = Path(path).read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit(f"{path} is empty")
    return key


def decode_wav(data: bytes) -> np.ndarray:
    """Samples of a 16 kHz mono 16-bit PCM WAV as float32 in [-1, 1]; HTTP 422 for anything else."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            rate, channels, width, frames = (
                w.getframerate(),
                w.getnchannels(),
                w.getsampwidth(),
                w.getnframes(),
            )
            if (rate, channels, width) != (SAMPLE_RATE, 1, 2):
                raise HTTPException(422, "audio must be a WAV with 16 kHz, mono, 16-bit PCM")
            if frames / SAMPLE_RATE > MAX_SECONDS:
                raise HTTPException(413, f"audio is longer than {MAX_SECONDS:.0f} s")
            pcm = w.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise HTTPException(422, "audio is not a readable WAV file") from exc
    pcm = pcm[: len(pcm) // 2 * 2]  # a truncated upload can end in half a sample
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


async def _read_request(request: Request, ctype: str) -> tuple[bytes, str | None, str | None]:
    """The audio bytes, language and model of a multipart or raw-body request, size-capped."""
    too_big = HTTPException(413, f"audio is larger than {MAX_BYTES // 1024 // 1024} MiB")
    if ctype == "multipart/form-data":
        form = await request.form()
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise HTTPException(422, 'multipart field "file" is required')
        data = await upload.read()
        language, model = form.get("language"), form.get("model")
    else:
        data = b""
        async for chunk in request.stream():
            data += chunk
            if len(data) > MAX_BYTES:
                raise too_big
        language, model = request.query_params.get("language"), request.query_params.get("model")
    if len(data) > MAX_BYTES:
        raise too_big
    return data, language, model


def create_app(engine: Engine, api_key: str) -> FastAPI:
    app = FastAPI(title="research-engine stt", docs_url=None, redoc_url=None, openapi_url=None)
    one_at_a_time = anyio.CapacityLimiter(1)  # the CPU threads belong to one clip at a time

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model": SERVED_NAME}

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(request: Request) -> dict:
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {api_key}".encode()):
            raise HTTPException(
                401, "missing or wrong API key", headers={"WWW-Authenticate": "Bearer"}
            )
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in ("multipart/form-data", "audio/wav", "audio/x-wav", "audio/wave"):
            raise HTTPException(
                422, "send multipart/form-data with a file, or a raw audio/wav body"
            )
        try:
            declared = int(request.headers.get("content-length", ""))
        except ValueError:
            raise HTTPException(411, "Content-Length is required") from None
        if declared > MAX_BYTES + FORM_OVERHEAD:
            raise HTTPException(413, f"audio is larger than {MAX_BYTES // 1024 // 1024} MiB")

        data, language, model = await _read_request(request, ctype)
        if model not in (None, "", SERVED_NAME):
            raise HTTPException(422, f"unknown model, use {SERVED_NAME}")
        if language in ("", None):
            language = None
        elif language not in LANGUAGES:
            raise HTTPException(422, "language must be es or en")

        samples = decode_wav(data)
        text = ""
        if len(samples):
            text = await anyio.to_thread.run_sync(engine.transcribe, samples, limiter=one_at_a_time)
        # The model detects the language itself; the hint is echoed, not forced.
        return {
            "text": text,
            "language": language,
            "duration": round(len(samples) / SAMPLE_RATE, 2),
        }

    return app


def _snapshot(repo: str, revision: str, patterns: list[str]) -> str:
    return snapshot_download(repo, revision=revision, allow_patterns=patterns)


def build_engine(env: Callable[[str, str | None], str | None] = os.environ.get) -> Engine:
    """Load the pinned ASR model and Silero VAD into one onnxruntime-on-CPU recognizer."""

    def need(name: str) -> str:
        value = env(name, None)
        if not value:
            raise SystemExit(f"{name} is not set (serve.sh sets it from models.tsv)")
        return value

    opts = rt.SessionOptions()
    opts.intra_op_num_threads = int(env("STT_THREADS", "8") or 8)
    opts.inter_op_num_threads = 1
    cpu = {"sess_options": opts, "providers": ["CPUExecutionProvider"]}

    model_dir = env("STT_MODEL_PATH", None) or _snapshot(
        need("STT_MODEL_REPO"),
        need("STT_MODEL_REVISION"),
        ["config.json", "vocab.txt", "encoder-model.onnx*", "decoder_joint-model.onnx*"],
    )
    vad_dir = env("STT_VAD_PATH", None) or _snapshot(
        need("STT_VAD_REPO"), need("STT_VAD_REVISION"), ["config.json", "silero_vad.onnx"]
    )
    asr = onnx_asr.load_model("nemo-conformer-tdt", model_dir, asr_config=cpu)
    vad = onnx_asr.load_vad("silero", vad_dir, sess_options=opts, providers=cpu["providers"])
    return VadEngine(asr.with_vad(vad))


def create_app_from_env() -> FastAPI:
    """uvicorn --factory entry point."""
    key_file = os.environ.get("STT_API_KEY_FILE")
    if not key_file:
        raise SystemExit("STT_API_KEY_FILE is not set")
    return create_app(build_engine(), read_key(key_file))
