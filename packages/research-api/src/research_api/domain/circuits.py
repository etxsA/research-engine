"""Rules of the circuit check: how a circuit document is read and how big a request may be.

Standard library only. The simulator and the LaTeX reader sit behind a port; what is decided
here is what the API refuses before any work starts.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NoReturn

from .errors import BadRequestError, FieldError, InvalidInputError

# A state is 2**n amplitudes; a unitary is 4**n entries and each operation costs 4**n. Measured
# on 2,000 operations: 12 wires of state take 0.1 s, an exact comparison of 8 wires 0.3 s and
# of 10 wires 41 s. So anything that builds a unitary stops at 8 wires.
MAX_STATE_WIRES = 12
MAX_UNITARY_WIRES = 8

DEFAULT_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_MAX_OPS = 2000
DEFAULT_TIMEOUT_S = 30.0

USE_THE_CLI = "Use `quantum-sim` on the command line for a circuit this size."

_NEUTRAL_KINDS = frozenset({"barrier", "text"})
_SHOWN_KEY = 80


@dataclass(frozen=True)
class CircuitCaps:
    """What the synchronous circuit endpoints accept. Inside these, quantum-sim keeps its own
    limits (20 wires, 10,000 operations)."""

    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    max_ops: int = DEFAULT_MAX_OPS
    timeout_s: float = DEFAULT_TIMEOUT_S


@dataclass(frozen=True)
class CheckTarget:
    """What a circuit is checked against. Exactly one field is set."""

    state: str | list[Any] | None = None
    unitary: list[list[Any]] | None = None
    circuit_json: str | None = None


def _short(text: str) -> str:
    return text if len(text) <= _SHOWN_KEY else text[:_SHOWN_KEY] + "..."


def _no_repeats(what: str, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise BadRequestError(
                f"{what} gives the key {_short(key)!r} twice; the document does not say which "
                "value is meant."
            )
        found[key] = value
    return found


def _no_constants(what: str, name: str) -> Any:
    raise BadRequestError(f"{what} holds {name}, which JSON does not allow.")


def read_json_strict(text: str | bytes, *, what: str) -> Any:
    """Parse JSON the way quantum-sim's own command does: a key written twice is refused.

    ``json.loads`` keeps the last value of a repeated key without a word, so a parsed body would
    hide the very ambiguity the simulator refuses. ``NaN`` and ``Infinity`` are refused too.
    ``what`` names the input in the message; the text itself is never echoed.
    """
    try:
        return json.loads(
            text,
            object_pairs_hook=lambda pairs: _no_repeats(what, pairs),
            parse_constant=lambda name: _no_constants(what, name),
        )
    except json.JSONDecodeError as e:
        raise BadRequestError(
            f"{what} is not valid JSON: {e.msg} at line {e.lineno} column {e.colno}."
        ) from None
    except RecursionError:
        raise BadRequestError(f"{what} is nested too deeply.") from None
    except (ValueError, UnicodeDecodeError):
        raise BadRequestError(f"{what} is not valid JSON.") from None


def wire_count(document: object) -> int | None:
    wires = document.get("wires") if isinstance(document, dict) else None
    return wires if isinstance(wires, int) and not isinstance(wires, bool) else None


def operation_count(document: object) -> int:
    """How many gate applications a circuit/1 document asks for, counted without trusting its
    shape: a gate drawn over several targets counts once for each, barriers and text not at all.
    Whatever is not shaped like a circuit counts as nothing; quantum-sim refuses it later."""
    parts = document.get("parts") if isinstance(document, dict) else None
    total = 0
    for part in parts if isinstance(parts, list) else ():
        for step in part if isinstance(part, list) else ():
            for item in step if isinstance(step, list) else ():
                if not isinstance(item, dict) or item.get("kind") in _NEUTRAL_KINDS:
                    continue
                targets = item.get("targets")
                total += len(targets) if isinstance(targets, list) and targets else 1
    return total


def ensure_within_caps(
    documents: Sequence[tuple[str, object]], caps: CircuitCaps, *, max_wires: int
) -> None:
    """Refuse (422) a request whose circuits are over the synchronous caps.

    ``documents`` pairs the JSON pointer of each circuit field with its parsed document. The
    operation cap is for the whole request.
    """
    for pointer, document in documents:
        wires = wire_count(document)
        if wires is not None and wires > max_wires:
            _refuse(
                pointer,
                f"The circuit has {wires} wires; this check takes at most {max_wires}. "
                + USE_THE_CLI,
            )
    ops = sum(operation_count(document) for _, document in documents)
    if ops > caps.max_ops:
        _refuse(
            documents[0][0],
            f"The request asks for {ops} operations; this endpoint takes at most {caps.max_ops}. "
            + USE_THE_CLI,
        )


def _refuse(pointer: str, message: str) -> NoReturn:
    raise InvalidInputError(message, (FieldError(pointer, message),))


# --- OpenQASM 3 export -------------------------------------------------------------------------


def values_pointer(key: str) -> str:
    """The JSON pointer of one key of ``values`` (``~`` and ``/`` escaped, RFC 6901)."""
    return "/values/" + key.replace("~", "~0").replace("/", "~1")


@dataclass(frozen=True)
class QasmProgram:
    """One OpenQASM 3 program: a part of a drawn circuit (from 1), its drawn sign (``None`` for
    the first part), the text, and its qubit and classical bit counts."""

    part: int
    sign: str | None
    text: str
    n_qubits: int
    n_clbits: int


@dataclass(frozen=True)
class QasmExport:
    """What the emitter made of one circuit. ``status`` is ``ok`` or ``refused``; a refused
    circuit has no programs (all or nothing) and ``reasons`` says why, in the emitter's words.
    ``notes`` are the reader's notes, ``symbols`` the free parameters the circuit mentions,
    ``unmatched`` the caller's binding keys that name none of them. ``unbound`` lists, as binding
    keys, the symbols of a refused circuit that have no value, only when values for them alone
    would export it (as docingest's exporter reports them)."""

    status: str
    programs: tuple[QasmProgram, ...]
    reasons: tuple[str, ...]
    notes: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    unmatched: tuple[str, ...] = ()
    unbound: tuple[str, ...] = ()
