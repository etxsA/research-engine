"""Text-to-speech server of the research engine: an OpenAI-style speech route on one GPU.

    POST /v1/audio/speech   Bearer key; JSON {"model": "local-tts", "input": str, "voice": str,
                            "response_format": "wav", "language": "es" | "en" | null}
                            -> audio/wav (mono, 16-bit PCM, at the sample rate of the model)
    GET  /v1/models         Bearer key; the served model name, its voices and its default voice
    GET  /health            no key; 200 {"ok": true} once the model is loaded, 503 while it loads

The model is loaded once, in the background, so the port answers (503) during the load. Requests
are sentence-sized and run one at a time. Text and audio stay in memory and are never logged.
Settings come from the environment (see serving/serve.sh and env.example). The voice name "default"
means the server's default voice (TTS_DEFAULT_VOICE): a client need not know the speakers.
"""

from __future__ import annotations

import hmac
import io
import os
import stat
import sys
import threading
import traceback
import wave
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Protocol

import anyio
import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

SERVED_NAME = "local-tts"
MAX_CHARS = 500  # a sentence: above it the token cap below would cut the speech short
MAX_NEW_TOKENS = (
    1024  # the 12 Hz codec makes about 12 tokens per second: a cap of about 85 s of audio
)
DEFAULT_VOICE_NAME = "default"  # what a client sends to get the server's default voice
PRESET_DEFAULT_VOICE = "vivian"  # default speaker of the CustomVoice models
_LANGUAGE_NAMES = {"es": "Spanish", "en": "English", None: "Auto"}


class Engine(Protocol):
    sample_rate: int
    voices: tuple[str, ...]
    default_voice: str  # one of voices: what the voice name "default" stands for

    def synthesize(self, text: str, voice: str, language: str | None) -> np.ndarray:
        """Mono float32 samples in [-1, 1] at sample_rate; voice is one of voices."""


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = SERVED_NAME
    input: str = Field(min_length=1, max_length=MAX_CHARS)
    voice: str = Field(min_length=1, max_length=64)
    response_format: Literal["wav"] = "wav"
    language: Literal["es", "en"] | None = None


def read_key(path: str) -> str:
    """The API key from a file that only its owner can read."""
    mode = Path(path).stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit(f"{path} must not be readable by group or others (chmod 600)")
    key = Path(path).read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit(f"{path} is empty")
    return key


def pick_default_voice(voices: tuple[str, ...], wanted: str | None, fallback: str) -> str:
    """The voice that "default" stands for: wanted (TTS_DEFAULT_VOICE) or else fallback, which must
    be one of voices (compared without case); refuses to load with a message listing the choices."""
    name = wanted or fallback
    found = {v.lower(): v for v in voices}.get(name.lower())
    if found is None:
        origin = "TTS_DEFAULT_VOICE" if wanted else "the built-in default voice"
        raise SystemExit(
            f"{origin} {name!r} is not a voice of this model (voices: {', '.join(voices)})"
        )
    return found


def encode_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """A mono 16-bit PCM WAV of float samples in [-1, 1]."""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def create_app(
    engine: Engine | None, api_key: str, loader: Callable[[], Engine] | None = None
) -> FastAPI:
    """The app around an engine. With a loader instead of an engine, the engine is built in a thread
    at start-up and /health answers 503 until it is there."""
    state: dict[str, Engine | None] = {"engine": engine}
    one_at_a_time = anyio.CapacityLimiter(1)  # one GPU, one sentence at a time

    def load() -> None:
        assert loader is not None
        try:
            state["engine"] = loader()
        except BaseException as exc:  # SystemExit too: a thread would swallow it without a word
            print(f"tts: the model failed to load: {type(exc).__name__}: {exc}", file=sys.stderr)
            traceback.print_exc()
            sys.stderr.flush()
            os._exit(1)  # exit so serve.sh reports it instead of waiting on a 503 forever

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if engine is None and loader is not None:
            threading.Thread(target=load, name="tts-load", daemon=True).start()
        yield

    app = FastAPI(
        title="research-engine tts",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def check_key(request: Request) -> None:
        auth = request.headers.get("authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {api_key}".encode()):
            raise HTTPException(
                401, "missing or wrong API key", headers={"WWW-Authenticate": "Bearer"}
            )

    def ready() -> Engine:
        current = state["engine"]
        if current is None:
            raise HTTPException(503, "the model is still loading", headers={"Retry-After": "5"})
        return current

    @app.get("/health")
    async def health(response: Response) -> dict[str, bool]:
        if state["engine"] is None:
            response.status_code = 503
            return {"ok": False}
        return {"ok": True}

    @app.get("/v1/models", dependencies=[Depends(check_key)])
    async def models() -> dict:
        voices = list(state["engine"].voices) if state["engine"] is not None else []  # type: ignore[union-attr]
        default = state["engine"].default_voice if state["engine"] is not None else None  # type: ignore[union-attr]
        return {
            "object": "list",
            "data": [
                {
                    "id": SERVED_NAME,
                    "object": "model",
                    "voices": voices,
                    "default_voice": default,
                    "aliases": {DEFAULT_VOICE_NAME: default},
                }
            ],
        }

    @app.post("/v1/audio/speech", dependencies=[Depends(check_key)])
    async def speech(body: SpeechRequest) -> Response:
        current = ready()
        if body.model != SERVED_NAME:
            raise HTTPException(400, f"unknown model, use {SERVED_NAME}")
        text = body.input.strip()
        if not text:
            raise HTTPException(422, "input is empty")
        known = {v.lower(): v for v in current.voices}
        voice = known.get(body.voice.lower())
        if voice is None and body.voice.lower() == DEFAULT_VOICE_NAME:
            voice = current.default_voice
        if voice is None:
            raise HTTPException(400, "unknown voice, see GET /v1/models")
        samples = await anyio.to_thread.run_sync(
            current.synthesize, text, voice, body.language, limiter=one_at_a_time
        )
        return Response(
            encode_wav(samples, current.sample_rate),
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )

    return app


class QwenEngine:
    """Qwen3-TTS behind one call: generate(text, voice, language name) -> (waves, sample rate)."""

    def __init__(
        self,
        voices: tuple[str, ...],
        generate: Callable[[str, str, str], tuple],
        default_voice: str | None = None,
    ):
        self.voices = voices
        self.default_voice = default_voice or voices[0]
        self.sample_rate = 24_000
        self._generate = generate

    def synthesize(self, text: str, voice: str, language: str | None) -> np.ndarray:
        wavs, rate = self._generate(text, voice, _LANGUAGE_NAMES[language])
        self.sample_rate = int(rate)
        return np.asarray(wavs[0], dtype=np.float32).reshape(-1)


def token_cap(text: str) -> int:
    """Most codec tokens worth generating for a text: measured speech takes 1.4 to 1.8 per
    character, so 3 per character plus two seconds leaves room for digits and stops a sampled run
    that never ends (one took 28 s of audio)."""
    return min(MAX_NEW_TOKENS, 24 + 3 * len(text))


def _clone_clips(folder: str) -> dict[str, tuple[str, str]]:
    """NAME -> (clip path, its text) for the NAME.wav and NAME.txt pairs of a folder."""
    clips = {
        clip.stem: (str(clip), clip.with_suffix(".txt").read_text(encoding="utf-8").strip())
        for clip in sorted(Path(folder).glob("*.wav"))
        if clip.with_suffix(".txt").exists()
    }
    if not clips:
        raise SystemExit(f"no NAME.wav + NAME.txt pairs in {folder}")
    return clips


def build_engine(env: Callable[[str, str | None], str | None] = os.environ.get) -> Engine:
    """Load the pinned model on the GPU that CUDA_VISIBLE_DEVICES leaves visible.

    TTS_DEFAULT_VOICE names the voice that "default" stands for (vivian for a CustomVoice model, the
    first clip of TTS_VOICES_DIR for a Base model).

    TTS_BACKEND=faster (default) runs the talker and the code predictor as CUDA graphs
    (faster-qwen3-tts); TTS_BACKEND=qwen runs the plain qwen-tts loop, about six times slower.
    """
    import torch  # noqa: PLC0415 - heavy imports stay out of the tests
    from huggingface_hub import snapshot_download  # noqa: PLC0415

    def need(name: str) -> str:
        value = env(name, None)
        if not value:
            raise SystemExit(f"{name} is not set (serve.sh sets it from models.tsv)")
        return value

    path = env("TTS_MODEL_PATH", None) or snapshot_download(
        need("TTS_MODEL_REPO"), revision=need("TTS_MODEL_REVISION")
    )
    backend = env("TTS_BACKEND", "faster") or "faster"
    if backend not in ("faster", "qwen"):
        raise SystemExit("TTS_BACKEND must be faster or qwen")
    if backend == "faster":
        from faster_qwen3_tts import FasterQwen3TTS  # noqa: PLC0415

        fast = FasterQwen3TTS.from_pretrained(path, device="cuda:0", dtype=torch.bfloat16)
        wrapper = fast.model
        fast.warmup()
    else:
        from qwen_tts import Qwen3TTSModel  # noqa: PLC0415

        fast = None
        wrapper = Qwen3TTSModel.from_pretrained(path, device_map="cuda:0", dtype=torch.bfloat16)

    kind = wrapper.model.tts_model_type
    if kind not in ("base", "custom_voice"):
        raise SystemExit(f"model type {kind} is not served (use a CustomVoice or Base model)")
    if kind == "custom_voice":
        speakers = tuple(wrapper.get_supported_speakers() or ())
        default_voice = pick_default_voice(
            speakers, env("TTS_DEFAULT_VOICE", None), PRESET_DEFAULT_VOICE
        )
        target = fast or wrapper

        def preset(text: str, voice: str, language: str) -> tuple:
            return target.generate_custom_voice(
                text=text, speaker=voice, language=language, max_new_tokens=token_cap(text)
            )

        return QwenEngine(speakers, preset, default_voice)

    # A Base model speaks in cloned voices: a folder of NAME.wav clips, each with a NAME.txt text.
    clips = _clone_clips(need("TTS_VOICES_DIR"))
    if fast is not None:

        def clone(text: str, voice: str, language: str) -> tuple:
            audio, ref_text = clips[voice]
            return fast.generate_voice_clone(
                text=text,
                language=language,
                ref_audio=audio,
                ref_text=ref_text,
                max_new_tokens=token_cap(text),
            )

    else:
        prompts = {
            name: wrapper.create_voice_clone_prompt(ref_audio=audio, ref_text=ref_text)
            for name, (audio, ref_text) in clips.items()
        }

        def clone(text: str, voice: str, language: str) -> tuple:
            return wrapper.generate_voice_clone(
                text=text,
                language=language,
                voice_clone_prompt=prompts[voice],
                max_new_tokens=token_cap(text),
            )

    default_voice = pick_default_voice(
        tuple(clips), env("TTS_DEFAULT_VOICE", None), next(iter(clips))
    )
    return QwenEngine(tuple(clips), clone, default_voice)


def create_app_from_env() -> FastAPI:
    """uvicorn --factory entry point."""
    key_file = os.environ.get("TTS_API_KEY_FILE")
    if not key_file:
        raise SystemExit("TTS_API_KEY_FILE is not set")
    return create_app(None, read_key(key_file), loader=build_engine)
