"""Routes of the ``/api/v1`` surface that exist so far."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...bootstrap import Container
from .auth import PROTECTED
from .schemas import Health, Problem, Status
from .security import member

router = APIRouter(responses={"default": {"model": Problem, "description": "A problem."}})


def _container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


@router.get("/health", operation_id="getHealth", summary="Liveness check")
def health() -> Health:
    return Health(ok=True)


@router.get(
    "/status",
    operation_id="getStatus",
    summary="State of the service",
    dependencies=[Depends(member)],
    responses=PROTECTED,
)
def status(request: Request) -> Status:
    """For signed-in members only: it shows when someone is asking."""
    container = _container(request)
    return Status.from_domain(
        container.report_status(),
        container.asks.status(),
        container.bench.jobs.status(),
        speech_ready=container.speech is not None,
        tts_ready=container.synthesis is not None,
    )
