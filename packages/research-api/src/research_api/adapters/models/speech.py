"""``Transcriber`` over an OpenAI-style speech server (``POST <endpoint>/audio/transcriptions``).

The endpoint is the address up to and including ``/v1``, like a model server's. The clip goes as
a multipart upload and the key as a Bearer token. The client ignores the proxy variables of the
environment, and a response body is never put in an error message or a log: it is a person's
words.
"""

from __future__ import annotations

import httpx

from ...domain.errors import UnavailableError, UpstreamError
from ...domain.speech import LANGUAGES, Language
from ...ports.speech import Heard

RETRY_STATUSES = frozenset({502, 503, 504})
RETRY_AFTER_S = 30


class OpenAiTranscriber:
    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str,
        *,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = f"{endpoint.rstrip('/')}/audio/transcriptions"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout_s = timeout_s
        self._transport = transport

    async def transcribe(self, audio: bytes, *, language: Language | None) -> Heard:
        fields = {"model": self._model, "response_format": "json"}
        if language is not None:
            fields["language"] = language
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport, trust_env=False
            ) as client:
                reply = await client.post(
                    self._url,
                    headers=self._headers,
                    data=fields,
                    files={"file": ("clip.wav", audio, "audio/wav")},
                )
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
        try:
            data = reply.json()
            text = str(data["text"]).strip()
            heard = data.get("language")
        except (ValueError, KeyError, TypeError, AttributeError):
            raise UpstreamError("The speech server answered in an unknown shape.") from None
        return Heard(text=text, language=_language(heard))


def _language(value: object) -> Language | None:
    """``es`` or ``en`` from what a server may say (``es``, ``ES``, ``Spanish``); else None."""
    if not isinstance(value, str):
        return None
    code = {"spanish": "es", "english": "en"}.get(value.strip().lower(), value.strip().lower())
    return next((lang for lang in LANGUAGES if lang == code), None)
