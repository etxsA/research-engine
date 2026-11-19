"""Fakes for fake mode: deterministic stand-ins that touch no network, disk or real clock."""

from __future__ import annotations

from datetime import UTC, datetime

from .ask import FakeAskEngine, FakeDocumentReader
from .bench import FakeArms, FakeModelServer
from .speech import FakeTranscriber

FAKE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

__all__ = [
    "FAKE_EPOCH",
    "FakeArms",
    "FakeAskEngine",
    "FakeDocumentReader",
    "FakeModelServer",
    "FakeTranscriber",
    "FixedClock",
]


class FixedClock:
    """A clock that always says the same time, so fake-mode documents are reproducible."""

    def __init__(self, at: datetime = FAKE_EPOCH) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at
