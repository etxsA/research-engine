"""Run a blocking function in a thread, wait for it at most ``timeout_s``, bound how many run.

A call over the time limit is a 422 whose message ends with the call's ``hint``: where to do
work this size instead (by default, ``quantum-sim`` on the command line).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from typing import TypeVar

from ...domain.circuits import USE_THE_CLI
from ...domain.errors import InvalidInputError, UnavailableError

T = TypeVar("T")

RETRY_AFTER_S = 5


class ComputeLane:
    """At most ``max_busy`` calls run at once; the rest are told to try again.

    A Python thread cannot be stopped. When a call outlives the timeout the caller is released
    with a 422 and the thread finishes on its own, still holding its slot, so a slow circuit
    cannot pile up unbounded work: the caps keep it short and the slots keep it few. The threads
    are daemons, so a stopping server does not wait for them.
    """

    def __init__(self, *, timeout_s: float, max_busy: int = 2) -> None:
        self._timeout_s = timeout_s
        self._slots = threading.BoundedSemaphore(max_busy)

    async def run(self, work: Callable[[], T], *, hint: str = USE_THE_CLI) -> T:
        if not self._slots.acquire(blocking=False):
            raise UnavailableError(
                "Circuit jobs are already running; try again in a few seconds.",
                retry_after_s=RETRY_AFTER_S,
            )
        loop = asyncio.get_running_loop()
        outcome: asyncio.Future[T] = loop.create_future()

        def deliver(result: T | None, error: Exception | None) -> None:
            if outcome.done():  # the caller gave up
                return
            if error is not None:
                outcome.set_exception(error)
            else:
                outcome.set_result(result)  # type: ignore[arg-type]

        def thread() -> None:
            result: T | None = None
            error: Exception | None = None
            try:
                result = work()
            except Exception as e:
                error = e
            finally:
                self._slots.release()
            with contextlib.suppress(RuntimeError):  # the loop closed: nobody is waiting
                loop.call_soon_threadsafe(deliver, result, error)

        threading.Thread(target=thread, name="circuit-work", daemon=True).start()
        try:
            return await asyncio.wait_for(outcome, self._timeout_s)
        except TimeoutError:
            raise InvalidInputError(
                f"The work took longer than {self._timeout_s:g} s, so this request was released. "
                f"{hint}"
            ) from None
