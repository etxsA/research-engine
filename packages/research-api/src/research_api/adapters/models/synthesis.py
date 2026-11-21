"""``Synthesizer`` over an OpenAI-style speech server (``POST <endpoint>/audio/speech``).

The endpoint is the address up to and including ``/v1``. The text goes as JSON and the key as a
Bearer token. The client ignores the proxy variables of the environment, and neither the text nor
a response body is ever put in an error message or a log: it is part of a person's answer.
"""

from __future__ import annotations

import httpx

from ...domain.errors import UnavailableError, UpstreamError
from ...domain.speech import Language

RETRY_STATUSES = frozenset({502, 503, 504})
RETRY_AFTER_S = 30
MAX_AUDIO_BYTES = 32 * 1024 * 1024


class OpenAiSynthesizer:
    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str,
        *,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = f"{endpoint.rstrip('/')}/audio/speech"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout_s = timeout_s
        self._transport = transport

    async def synthesize(self, text: str, *, language: Language, voice: str) -> bytes:
        body = {
            "model": self._model,
            "input": text,
            "voice": voice,
            "response_format": "wav",
            "language": language,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport, trust_env=False
            ) as client:
                reply = await client.post(self._url, headers=self._headers, json=body)
        except httpx.TimeoutException:
            raise TimeoutError("the speech server took too long") from None
        except httpx.HTTPError:
            raise UnavailableError(
                "The speech server cannot be reached.", retry_after_s=RETRY_AFTER_S
            ) from None
        if reply.status_code in RETRY_STATUSES:
            raise UnavailableError("The speech server is not ready.", retry_after_s=RETRY_AFTER_S)
        if reply.status_code != 200:
            raise UpstreamError("The speech server refused the request.")
        audio = reply.content
        if not (audio[:4] == b"RIFF" and audio[8:12] == b"WAVE") or len(audio) > MAX_AUDIO_BYTES:
            raise UpstreamError("The speech server answered in an unknown shape.")
        return audio
