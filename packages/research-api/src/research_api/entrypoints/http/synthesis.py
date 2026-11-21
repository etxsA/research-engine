"""Route of text to speech: a piece of an answer in, a WAV out, nothing kept.

The caller is checked first, then that reading aloud is set up, then the body is read with a cap,
so a request with no session costs no body read.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from ...application.auth import Authenticated
from ...bootstrap import Container
from ...domain.errors import TooLargeError, UnavailableError
from ...domain.synthesis import MAX_RAW_CHARS
from .circuits import BoundedRequest, check_caller
from .schemas import Problem, SynthesisRequest
from .security import container_of, member

Member = Annotated[Authenticated, Depends(member)]

BODY_CAP = MAX_RAW_CHARS * 6 + 1024
WAV = {"audio/wav": {"schema": {"type": "string", "format": "binary"}}}


class TextRequest(BoundedRequest):
    limit = BODY_CAP

    def _too_large(self) -> TooLargeError:
        return TooLargeError(f"The request body is over {self.limit} bytes.")


class SynthesisRoute(APIRoute):
    """Checks the caller and that reading aloud is set up, then reads the body with a cap."""

    def get_route_handler(self) -> Callable[[Request], Any]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            container: Container = request.app.state.container
            await run_in_threadpool(check_caller, request)
            if container.synthesis is None:
                raise UnavailableError("Reading aloud is not set up on this service.")
            bounded = TextRequest(request.scope, request.receive)
            await bounded.body()
            return await original(bounded)

        return handler


router = APIRouter(
    prefix="/speech",
    tags=["speech"],
    route_class=SynthesisRoute,
    dependencies=[Depends(member)],
    responses={"default": {"model": Problem, "description": "A problem."}},
)


@router.post(
    "/synthesis",
    operation_id="synthesizeSpeech",
    summary="Read a piece of an answer aloud",
    description="The body is a piece of an answer, one sentence at a time. Citations, markdown "
    'and formulas are cleaned away on the server first (a formula is said as "ecuación" or '
    '"equation"); what is left must be 1 to 400 characters with the default settings. Without '
    "`language` it is guessed from the words. The answer is a mono 16-bit PCM WAV with "
    "`Cache-Control: no-store`. Neither the text nor the audio is stored or logged. At most two "
    "readings per member are open at once (429, see `Retry-After`).",
    response_model=None,
    response_class=Response,
    responses={
        200: {"description": "The speech.", "content": WAV},
        413: {"model": Problem, "description": "The body is over the byte cap."},
        422: {
            "model": Problem,
            "description": "Nothing to read after the cleanup, more than `max_tts_chars` "
            "characters, or a `language` other than `es` or `en`.",
        },
        429: {"model": Problem, "description": "Too many open readings of this member."},
        503: {
            "model": Problem,
            "description": "Reading aloud is not set up, or its server is down or slow; see "
            "`Retry-After`.",
        },
    },
)
async def synthesize_speech(request: Request, who: Member, body: SynthesisRequest) -> Response:
    desk = container_of(request).synthesis
    assert desk is not None  # the route class answered 503 before this point
    audio = await desk.synthesize(who.user.username, body.text, language=body.language)
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})
