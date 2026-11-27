"""Rules of the circuit check: how a circuit document is read and how big a request may be.

Standard library only. The simulator and the LaTeX reader sit behind a port; what is decided
here is what the API refuses before any work starts.

The OpenQASM 3 export follows the same caps with no wire limit (it builds no state): one
operation budget for the whole request, so all the circuits of a paper count together. Its
refusals point to what can export a circuit this size: the library function
``quantum_sim.to_qasm`` for a pasted circuit, ``docingest circuits export --doc`` for a paper. A
paper's programs are named and written as ``docingest circuits export --doc`` writes them
(``<doc_id[:16]>-bNN-cNN-pNN.qasm``, blocks and circuits numbered from 1 over the whole
``document.md``, ``docingest`` in the generated-by line); a pasted circuit's are
``circuit-pNN.qasm``, with ``research-api`` in that line and no source line, so no name or path
of the caller's reaches the text.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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

# Where to do work over the caps instead. quantum-sim's command simulates and checks but does
# not export, so an export over a cap points to the library function or to docingest's command.
USE_THE_CLI = "Use `quantum-sim` on the command line for a circuit this size."
USE_THE_LIBRARY = "Use the Python function `quantum_sim.to_qasm` for a circuit this size."
USE_DOCINGEST = "Use `docingest circuits export --doc` on the command line for a paper this size."

# The generated-by name and the source line of the programs (see the module docstring).
PASTED_TOOL = "research-api"
PASTED_ORIGIN = ""
PAPER_TOOL = "docingest"

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
    documents: Sequence[tuple[str, object]],
    caps: CircuitCaps,
    *,
    max_wires: int | None,
    hint: str = USE_THE_CLI,
) -> None:
    """Refuse (422) a request whose circuits are over the synchronous caps.

    ``documents`` pairs the JSON pointer of each circuit field with its parsed document. The
    operation cap is for the whole request. ``max_wires`` is ``None`` for work that builds no
    state (the OpenQASM export): then any number of wires passes. ``hint`` ends the message:
    where to do work this size instead.
    """
    for pointer, document in documents:
        wires = wire_count(document)
        if max_wires is not None and wires is not None and wires > max_wires:
            _refuse(
                pointer,
                f"The circuit has {wires} wires; this check takes at most {max_wires}. " + hint,
            )
    ops = sum(operation_count(document) for _, document in documents)
    if ops > caps.max_ops:
        _refuse(
            documents[0][0],
            f"The request asks for {ops} operations; this endpoint takes at most {caps.max_ops}. "
            + hint,
        )


def ensure_paper_within_caps(documents: Sequence[object], caps: CircuitCaps) -> None:
    """Refuse (422) the export of a paper whose circuits, all together, ask for more operations
    than the operation cap; the message points to docingest's command, which has no cap. There
    is no wire cap. No field is named: the path names the paper and the body holds no circuit.
    """
    ops = sum(operation_count(document) for document in documents)
    if ops > caps.max_ops:
        raise InvalidInputError(
            f"The paper's circuits ask for {ops} operations; this endpoint takes at most "
            f"{caps.max_ops}. " + USE_DOCINGEST
        )


def _refuse(pointer: str, message: str) -> NoReturn:
    raise InvalidInputError(message, (FieldError(pointer, message),))


# --- OpenQASM 3 export -------------------------------------------------------------------------


def qasm_filename(doc_id: str, block: int, circuit: int, part: int) -> str:
    """The file name ``docingest circuits export`` gives a paper's program (blocks, circuits and
    parts from 1): ``<doc_id[:16]>-bNN-cNN-pNN.qasm``."""
    return f"{doc_id[:16]}-b{block:02d}-c{circuit:02d}-p{part:02d}.qasm"


def pasted_qasm_filename(part: int) -> str:
    """The file name of a part of a pasted circuit: ``circuit-pNN.qasm``."""
    return f"circuit-p{part:02d}.qasm"


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


@dataclass(frozen=True)
class QasmFile:
    """A program and the name of its file."""

    filename: str
    program: QasmProgram


@dataclass(frozen=True)
class PastedQasm:
    """The export of one pasted circuit: the emitter's answer and its named programs."""

    export: QasmExport
    files: tuple[QasmFile, ...]


@dataclass(frozen=True)
class PaperBlock:
    """One fenced ``latex`` block of a paper, numbered from 1 over the whole text as docingest
    numbers it, with the reader's status and each circuit as a ``circuit/1`` document."""

    block: int
    latex: str
    status: str
    circuits: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class PaperCircuits:
    doc_id: str
    source_name: str
    blocks: tuple[PaperBlock, ...]


@dataclass(frozen=True)
class PaperCircuitQasm:
    """The export of circuit ``circuit`` (from 1) of block ``block`` of a paper."""

    block: int
    circuit: int
    export: QasmExport
    files: tuple[QasmFile, ...]


@dataclass(frozen=True)
class PaperQasm:
    """The export of every circuit of one paper. ``warnings`` are the notes docingest's export
    gives for the paper: each circuit's notes after ``<source_name>, block b, circuit c: ``,
    ``<source_name>: no circuits found``, and each binding key that matches no symbol of any
    exported circuit."""

    doc_id: str
    source_name: str
    circuits: tuple[PaperCircuitQasm, ...]
    warnings: tuple[str, ...]


def paper_origin(source_name: str, block: int, circuit: int) -> str:
    """The source line docingest writes for a paper's circuit."""
    return f"{source_name}, block {block}, circuit {circuit}"
