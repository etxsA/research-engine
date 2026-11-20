"""What went wrong inside docingest or the model servers, in words a member can act on.

Only text written here or by docingest's own domain errors reaches a member. The message of a
library exception (litellm, httpx, PaperQA2) is never passed on: it can hold the prompt, and the
prompt holds the question.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from docingest.domain.errors import (
    IndexMismatchError,
    IndexNotReadyError,
    InvalidConfigError,
    NotConfiguredError,
    RetrievalError,
)

from ...domain.errors import (
    InvalidInputError,
    ResearchApiError,
    UnavailableError,
    UpstreamError,
)

NO_PASSAGE = "the index found no passage for this question"
# The refusal of ``AskSession.answer_from_passages`` for a passage of a paper that is not in the
# corpus; it names the paper by the 16 characters of its id, which is all the message may hold.
UNKNOWN_PAPER = re.compile(r"paper [0-9a-f]{1,16} is not in the corpus")
# Fixed reasons for /status, which signed-in members read (never a path or a setting value).
REASON_INDEX = "The search index is not ready."
REASON_CONFIG = "The answer engine is not configured correctly."
REASON_START = "The answer engine could not start."
REASON_SLOW = "The answer engine did not finish starting in time."

# Names of the exceptions litellm and the HTTP clients raise, matched by name so that this
# module needs neither library (the `qa` extra is optional).
_NO_ANSWER = frozenset(
    {
        "APIConnectionError",
        "ServiceUnavailableError",
        "Timeout",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "RateLimitError",
    }
)
_REFUSED = frozenset(
    {
        "BadRequestError",
        "ContextWindowExceededError",
        "InternalServerError",
        "APIError",
        "APIResponseValidationError",
        "HTTPStatusError",
    }
)


# httpx's base class for every failure to reach a server (refused, reset, timed out, dropped
# mid-answer). Its HTTPStatusError is not one: a server that answered is not a lost server.
_TRANSPORT = frozenset({"TransportError"})


def _names(error: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(error).__mro__}


def _unreachable(error: BaseException | None) -> bool:
    """Whether ``error`` says that a model server could not be reached."""
    if error is None:
        return False
    if isinstance(error, ConnectionError | TimeoutError):
        return True
    return bool(_names(error) & (_NO_ANSWER | _TRANSPORT))


def _lost_server(error: Exception) -> bool:
    """The wrappers that hide a connection failure: litellm's chain of models (the last error
    it saw) and docingest-index's ``RemoteServiceError`` without an HTTP status (its cause is
    what httpx raised). A wrapper around a refusal, a 4xx or a bad body is not one."""
    names = _names(error)
    if "AllModelsExhaustedError" in names:
        last = getattr(error, "last_exc", None) or error.__cause__
        return _unreachable(last)
    if "RemoteServiceError" in names and getattr(error, "status", None) is None:
        return _unreachable(error.__cause__)
    return False


def _retrieval(error: Exception) -> ResearchApiError:
    if NO_PASSAGE in str(error):
        return InvalidInputError(
            "The index found no passage for this question. Try asking it another way."
        )
    return UpstreamError("The retrieval step failed (the embedding or reranking server).")


def _no_answer(_: Exception) -> ResearchApiError:
    return UnavailableError(
        "A model server did not answer. Check that it is up and try again.", retry_after_s=15
    )


Rule = tuple[tuple[type[Exception], ...], Callable[[Exception], ResearchApiError]]
# (what was raised, what the member is told), tried in order.
_RULES: tuple[Rule, ...] = (
    (
        (IndexNotReadyError,),
        lambda _: UnavailableError(
            "The search index is not ready (empty, or being rebuilt). "
            "Try again later, or tell the person who runs the server.",
            retry_after_s=30,
        ),
    ),
    (
        (IndexMismatchError,),
        lambda _: UnavailableError(
            "The search index was built with another embedding model than the one the server "
            "uses now. The person who runs the server has to rebuild it."
        ),
    ),
    ((NotConfiguredError, InvalidConfigError), lambda _: UnavailableError(REASON_CONFIG)),
    ((RetrievalError,), _retrieval),
    (
        (ImportError,),
        lambda _: UnavailableError(
            "The libraries that write answers are not installed on the server "
            "(the `qa` extra of docingest)."
        ),
    ),
    ((ConnectionError, TimeoutError), _no_answer),
)


def _refused(text: str) -> InvalidInputError | None:
    """What docingest's own checks of a question or of given passages say (their messages are
    written there and hold no question text)."""
    if text.startswith(("whole_blocks", "keywords")):
        return InvalidInputError(text)  # written in docingest's AskService.settle
    if UNKNOWN_PAPER.fullmatch(text):
        return InvalidInputError(
            f"{text[:1].upper()}{text[1:]}, so a gold passage of it cannot be given to the model."
        )
    return None


def classify(error: Exception) -> ResearchApiError | None:
    """A domain error with a message a member can read, or None when ``error`` is a surprise."""
    for kinds, build in _RULES:
        if isinstance(error, kinds):
            return build(error)
    text = str(error)
    if isinstance(error, RuntimeError) and text.startswith("no ingested documents"):
        return UnavailableError("The corpus is empty: no paper has been ingested yet.")
    if isinstance(error, ValueError) and (refused := _refused(text)) is not None:
        return refused
    names = _names(error)
    if names & _NO_ANSWER or _lost_server(error):
        return _no_answer(error)
    if names & _REFUSED:
        return UpstreamError("A model server refused the request (it may be too long).")
    return None


def start_reason(error: Exception) -> str:
    """The fixed text for ``/status`` when warming up failed."""
    if isinstance(error, IndexNotReadyError | IndexMismatchError):
        return REASON_INDEX
    if isinstance(error, NotConfiguredError | InvalidConfigError | FileNotFoundError | ImportError):
        return REASON_CONFIG
    if isinstance(error, TimeoutError):
        return REASON_SLOW
    return REASON_START
