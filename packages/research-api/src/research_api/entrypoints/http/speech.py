"""Route of speech to text: a clip in, the text out, nothing kept.

The caller is checked first, then the type, then the body is read with a cap, so a request with no
session costs no body read and a clip over the cap stops streaming at the cap.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from ...application.auth import Authenticated
from ...bootstrap import Container
from ...domain.errors import InvalidInputError, TooLargeError, UnavailableError
from ...domain.speech import WRONG_TYPE, Language, is_wav_type
from .circuits import BoundedRequest, check_caller
from .schemas import Problem, TranscriptionOut
from .security import container_of, member

Member = Annotated[Authenticated, Depends(member)]

CLIP = {
    "required": True,
    "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}},
}


class ClipRequest(BoundedRequest):
    def _too_large(self) -> TooLargeError:
        return TooLargeError(f"The clip is over {self.limit} bytes.")


class SpeechRoute(APIRoute):
    """Checks the caller, the type and that speech is set up, then reads the body with a cap."""

    def get_route_handler(self) -> Callable[[Request], Any]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            container: Container = request.app.state.container
            await run_in_threadpool(check_caller, request)
            if container.speech is None:
                raise UnavailableError("Speech recognition is not set up on this service.")
            if not is_wav_type(request.headers.get("content-type", "")):
                raise InvalidInputError(WRONG_TYPE)
            clip = ClipRequest(request.scope, request.receive)
            clip.limit = container.speech.limits.max_audio_bytes
            await clip.body()
            return await original(clip)

        return handler


router = APIRouter(
    prefix="/speech",
    tags=["speech"],
    route_class=SpeechRoute,
    dependencies=[Depends(member)],
    responses={"default": {"model": Problem, "description": "A problem."}},
)


@router.post(
    "/transcriptions",
    operation_id="transcribeSpeech",
    summary="Turn a spoken clip into text",
    description="The body is the clip itself: `Content-Type: audio/wav`, 16 kHz, mono, 16-bit "
    "PCM, at most 2 MiB and 60 seconds with the default settings. `lang` is a hint; without it "
    "the speech server decides. Neither the audio nor the text is stored or logged. At most two "
    "transcriptions per member (429) and a few in all (503) are open at once; see `Retry-After`.",
    openapi_extra={"requestBody": CLIP},
    responses={
        413: {"model": Problem, "description": "The clip is over the byte cap."},
        422: {
            "model": Problem,
            "description": "Not `audio/wav`, not 16 kHz mono 16-bit PCM, empty, too long, or a "
            "`lang` other than `es` or `en`.",
        },
        429: {"model": Problem, "description": "Too many open transcriptions of this member."},
        503: {
            "model": Problem,
            "description": "Speech is not set up, the speech server is down or slow, or the "
            "desk is busy; see `Retry-After`.",
        },
    },
)
async def transcribe_speech(
    request: Request,
    who: Member,
    lang: Annotated[Language | None, Query(description="Language hint: `es` or `en`.")] = None,
) -> TranscriptionOut:
    desk = container_of(request).speech
    assert desk is not None  # the route class answered 503 before this point
    transcript = await desk.transcribe(who.user.username, await request.body(), language=lang)
    return TranscriptionOut.from_domain(transcript)
