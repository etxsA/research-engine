"""The Python API behind the ``quantum-sim`` command: ``run`` and ``check`` on circuit/1
documents, with a JSON-ready result.

Both functions take plain Python data (what ``json.load`` gives) and return a ``dict`` in the
result format ``quantum-sim-result/1``; they never raise on bad input, they return a result
whose ``status`` is ``"refused"`` and whose ``refused`` lists the reasons. ``status`` is
``"ok"`` when the work was done, ``"symbolic"`` when the circuit has free symbols that no
value was given for (nothing is simulated; ``unbound`` lists them) and ``"refused"``.

Result keys: ``format``, ``command``, ``version``, ``status``, ``conventions`` (printed in
every result), ``input``, ``parameters`` and ``values``, ``unbound``, ``circuits`` (one entry
for each part of the document, with ``state``, ``probabilities`` and, when asked for,
``unitary``, every complex number as ``[re, im]`` rounded to 12 decimals), ``equal_to_target``
(true, false or null), ``comparisons`` (the equality details), ``notes`` and ``refused``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from importlib.metadata import version
from typing import Any

import numpy as np
from numpy.typing import NDArray

from quantum_sim.angles import binding_name, parse_value
from quantum_sim.domain import (
    ATOL,
    CONVENTIONS,
    MAX_STATEVECTOR_WIRES,
    AngleError,
    Circuit,
    QuantumSimError,
    RefusedError,
    short_text,
)
from quantum_sim.engine import equal, probabilities, statevector, unitary
from quantum_sim.formats import ParametricCircuit, Reading, from_gate_list

RESULT_FORMAT = "quantum-sim-result/1"
DECIMALS = 12
MAX_LISTED_WIRES = 12  # states and probabilities are listed in full up to this many wires
MAX_LISTED_UNITARY_WIRES = 8  # a unitary is listed up to this many wires
TOP_OUTCOMES = 64  # outcomes listed when there are more wires than MAX_LISTED_WIRES

_RUN_KEYS = frozenset({"circuit", "input", "values", "unitary"})
_CHECK_KEYS = frozenset({"a", "b", "circuit", "target", "input", "values"})


class _Refuse(Exception):
    def __init__(self, *reasons: str) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


# --- Encoding ----------------------------------------------------------------------------------


def _plain(values: Any) -> Any:
    return np.round(values, DECIMALS) + 0.0  # + 0.0 turns -0.0 into 0.0


def encode(array: NDArray[np.complex128]) -> list[Any]:
    """A complex array as nested lists of ``[re, im]`` pairs, rounded."""
    return np.stack([_plain(array.real), _plain(array.imag)], axis=-1).tolist()


def _is_number(entry: object) -> bool:
    return isinstance(entry, int | float) and not isinstance(entry, bool)


def _is_pair(entry: object) -> bool:
    return isinstance(entry, list) and len(entry) == 2 and all(_is_number(x) for x in entry)


def _decode_number(entry: object) -> complex:
    try:
        if _is_number(entry):
            return complex(entry, 0.0)  # type: ignore[arg-type]
        if _is_pair(entry):
            return complex(entry[0], entry[1])  # type: ignore[index]
    except OverflowError:  # an integer too large for a float
        raise _Refuse("an amplitude is too large for a float") from None
    raise _Refuse("an amplitude is a number or a [re, im] pair")


def decode(value: object, ndim: int = 1) -> NDArray[np.complex128]:
    """A list (``ndim`` 1) or a list of rows (``ndim`` 2) of numbers or ``[re, im]`` pairs as
    a complex array. The depth is stated by the caller because ``[[0, 1], [1, 0]]`` is a matrix
    of numbers where a vector of pairs would read the same. Only finite entries are accepted."""
    if not isinstance(value, list) or not value:
        raise _Refuse("expected a non-empty list of amplitudes")
    if ndim == 1:
        array = np.array([_decode_number(x) for x in value], dtype=complex)
    else:
        rows = [row if isinstance(row, list) else None for row in value]
        if any(row is None or not row for row in rows):
            raise _Refuse("a matrix is a list of non-empty rows")
        if any(len(row) != len(rows[0]) for row in rows):  # type: ignore[arg-type]
            raise _Refuse("the rows of a matrix must have the same length")
        array = np.array([[_decode_number(x) for x in row] for row in rows], dtype=complex)  # type: ignore[union-attr]
    if not np.all(np.isfinite(array)):
        raise _Refuse("amplitudes must be finite")
    return array


# --- Requests ----------------------------------------------------------------------------------


def _envelope(request: object, allowed: frozenset[str], main: str) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise _Refuse("the request must be a JSON object")
    if "format" in request:  # a bare circuit/1 document
        return {main: request}
    unknown = sorted(str(k) for k in request if k not in allowed)
    if unknown:
        raise _Refuse(f"unknown key {unknown[0]!r}; the keys are {sorted(allowed)}")
    return dict(request)


def _read(document: object, name: str = "") -> Reading:
    """The document as a reading. A circuit above the wire limit is refused first, before any
    legend or item is read: a document of the largest size would take minutes to read."""
    prefix = f"{name}: " if name else ""
    wires = document.get("wires") if isinstance(document, dict) else None
    if isinstance(wires, int) and not isinstance(wires, bool) and wires > MAX_STATEVECTOR_WIRES:
        raise _Refuse(f"{prefix}{short_text(wires)} wires; the limit is {MAX_STATEVECTOR_WIRES}")
    try:
        return from_gate_list(document)
    except RefusedError as error:
        raise _Refuse(*(prefix + r for r in error.reasons)) from None


def _value_of(name: str, raw: object) -> float:
    if isinstance(raw, str):
        try:
            number = parse_value(raw).value
        except AngleError as error:
            raise _Refuse(f"the value of {name!r}: {error}") from None
    elif _is_number(raw):
        try:
            number = float(raw)  # type: ignore[arg-type]
        except OverflowError:  # an integer too large for a float
            raise _Refuse(f"the value of {name!r} is too large for a float") from None
    else:
        raise _Refuse(f"the value of {name!r} must be a number or an angle in text")
    if not math.isfinite(number):
        raise _Refuse(f"the value of {name!r} is not finite")
    return number


def _bind_values(raw: object, symbols: Sequence[str]) -> dict[str, float]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _Refuse("'values' must map a symbol name to a number")
    bound: dict[str, float] = {}
    spelled: dict[str, str] = {}  # the key that gave each name
    for key, value in raw.items():
        try:  # the one rule for a binding name, shared with the export
            name = binding_name(str(key))
        except AngleError as error:
            raise _Refuse(str(error)) from None
        if name not in symbols:
            known = ", ".join(symbols) or "none"
            raise _Refuse(f"no parameter named {name!r} (the circuit has: {known})")
        if (
            name in bound
        ):  # 'theta' and a backslash theta are one symbol: which value is meant is unknown
            raise _Refuse(
                f"values give {name!r} twice: as {short_text(spelled[name])!r} and as "
                f"{short_text(str(key))!r}; give it once"
            )
        bound[name] = _value_of(name, value)
        spelled[name] = str(key)
    return bound


def _input_state(raw: object) -> str | NDArray[np.complex128] | None:
    if raw is None or isinstance(raw, str):
        return raw
    return decode(raw)


def _checked_input(keyword: object, envelope: object, n_wires: int) -> Any:
    """The input state: the keyword if given, else the request's. Both are checked, so that a
    request with a bad ``input`` is refused whichever one wins."""
    chosen = None
    for raw in (envelope, keyword):  # the keyword is last: it overrides
        state = _input_state(raw)
        if state is not None:
            statevector(Circuit(n_wires, ()), state)  # raises if it does not fit the wires
            chosen = state
    return chosen


def _checked_values(keyword: object, envelope: object, symbols: Sequence[str]) -> dict[str, float]:
    """The values: the keyword if given, else the request's. Both are checked."""
    from_request = _bind_values(envelope, symbols)
    return _bind_values(keyword, symbols) if keyword is not None else from_request


def _bind(circuits: Sequence[ParametricCircuit], values: dict[str, float]) -> list[Circuit]:
    bound = []
    for index, circuit in enumerate(circuits, start=1):
        try:
            bound.append(circuit.bind(values))
        except AngleError as error:
            raise _Refuse(f"part {index}: {error}") from None
    return bound


# --- Results -----------------------------------------------------------------------------------


def _base(command: str, status: str) -> dict[str, Any]:
    return {
        "format": RESULT_FORMAT,
        "command": command,
        "version": version("quantum-sim"),
        "status": status,
        "conventions": dict(CONVENTIONS),
        "input": None,
        "parameters": [],
        "values": {},
        "unbound": [],
        "circuits": [],
        "equal_to_target": None,
        "comparisons": [],
        "notes": [],
        "refused": [],
    }


def refusal(command: str, reasons: Sequence[str]) -> dict[str, Any]:
    """A result with status ``refused`` and the given reasons."""
    result = _base(command, "refused")
    result["refused"] = list(reasons)
    return result


def _describe(index: int, circuit: ParametricCircuit) -> dict[str, Any]:
    return {
        "part": index,
        "wires": circuit.n_wires,
        "operations": len(circuit.ops),
        "parameters": list(circuit.symbols),
    }


def _probabilities_json(wires: Sequence[int], values: NDArray[np.float64]) -> dict[str, Any]:
    if len(wires) <= MAX_LISTED_WIRES:
        return {"wires": list(wires), "values": _plain(values).tolist()}
    order = np.argsort(-values, kind="stable")[:TOP_OUTCOMES]
    top = [
        [format(int(k), f"0{len(wires)}b"), float(_plain(values[k]))]
        for k in order
        if values[k] > 0
    ]
    return {"wires": list(wires), "top": top, "truncated": int(np.count_nonzero(values)) > len(top)}


def _unitary_json(index: int, circuit: Circuit, notes: list[str]) -> list[Any] | None:
    if circuit.measured_wires:
        notes.append(f"part {index}: a circuit with measurements has no unitary")
        return None
    if circuit.n_wires > MAX_LISTED_UNITARY_WIRES:
        notes.append(
            f"part {index}: the unitary of {circuit.n_wires} wires is not listed "
            f"(more than {MAX_LISTED_UNITARY_WIRES})"
        )
        return None
    return encode(unitary(circuit))


def _simulate(
    index: int, circuit: Circuit, state: Any, want_unitary: bool, notes: list[str]
) -> dict[str, Any]:
    entry: dict[str, Any] = {"part": index, "wires": circuit.n_wires}
    entry["measured_wires"] = list(circuit.measured_wires)
    final = statevector(circuit, state)
    entry["state"] = None
    if circuit.n_wires <= MAX_LISTED_WIRES:
        entry["state"] = encode(final)
    else:
        notes.append(
            f"part {index}: the state of {circuit.n_wires} wires is not listed "
            f"(more than {MAX_LISTED_WIRES}); the probabilities are"
        )
    wires = list(circuit.measured_wires or range(1, circuit.n_wires + 1))
    entry["probabilities"] = _probabilities_json(wires, probabilities(circuit, state))
    entry["unitary"] = _unitary_json(index, circuit, notes) if want_unitary else None
    return entry


def _input_json(state: Any, n_wires: int) -> Any:
    if state is None:
        return "0" * n_wires
    if isinstance(state, str):
        return state
    return encode(state) if n_wires <= MAX_LISTED_WIRES else f"{state.size} amplitudes"


def _symbolic(result: dict[str, Any], symbols: Sequence[str], bound: dict[str, float]) -> bool:
    """Mark ``result`` symbolic and return True when some symbol has no value."""
    unbound = [s for s in symbols if s not in bound]
    if unbound:
        result["status"], result["unbound"] = "symbolic", unbound
    return bool(unbound)


# --- run ---------------------------------------------------------------------------------------


def run(
    request: object,
    *,
    input_state: object = None,
    values: object = None,
    unitary: bool | None = None,
) -> dict[str, Any]:
    """Simulate a circuit/1 document: the state, the probabilities and, if asked, the unitary.

    ``request`` is a circuit/1 document, or an object with ``circuit`` and optionally ``input``
    (a basis label such as ``"010"``, wire 1 first, or a list of amplitudes), ``values`` (a
    number or an angle in text for each symbol, keyed by one symbol such as ``theta``,
    ``\\theta`` or ``\\theta_{1}``: :func:`quantum_sim.angles.symbol_name`; a text value is read
    by :func:`quantum_sim.angles.parse_value`, so ``pi/4`` is ``\\pi/4``, as in the export) and
    ``unitary`` (true to list the matrix); the
    keyword arguments, when given, replace those keys. A document with several parts (a drawn
    equality) is simulated part by part. Without a value for every symbol, nothing is simulated:
    ``status`` is ``"symbolic"`` and ``unbound`` lists the symbols.
    """
    try:
        return _run(request, input_state, values, unitary)
    except _Refuse as stop:
        return refusal("run", stop.reasons)
    except (QuantumSimError, ValueError, TypeError) as error:
        return refusal("run", [str(error)])


def _run(
    request: object, input_state: object, values: object, want_unitary: bool | None
) -> dict[str, Any]:
    envelope = _envelope(request, _RUN_KEYS, "circuit")
    if "circuit" not in envelope:
        raise _Refuse("a run request needs a 'circuit'")
    reading = _read(envelope["circuit"])
    symbols = reading.symbols
    bound = _checked_values(values, envelope.get("values"), symbols)
    state = _checked_input(input_state, envelope.get("input"), reading.circuits[0].n_wires)
    asked = envelope.get("unitary", False)  # both the request and the keyword are checked
    if not isinstance(asked, bool) or not isinstance(want_unitary, bool | None):
        raise _Refuse("'unitary' must be true or false")
    want = asked if want_unitary is None else want_unitary
    result = _base("run", "ok")
    result["parameters"], result["values"] = list(symbols), bound
    result["notes"] = list(reading.notes)
    result["input"] = _input_json(state, reading.circuits[0].n_wires)
    if _symbolic(result, symbols, bound):
        result["circuits"] = [_describe(i, c) for i, c in enumerate(reading.circuits, start=1)]
        return result
    for index, circuit in enumerate(_bind(reading.circuits, bound), start=1):
        entry = _simulate(index, circuit, state, want, result["notes"])
        entry["sign"] = reading.signs[index - 2] if index > 1 else None
        result["circuits"].append(entry)
    return result


# --- check -------------------------------------------------------------------------------------


def check(request: object, *, values: object = None) -> dict[str, Any]:
    """Whether two circuits, or a circuit and a target, are equal up to global phase.

    ``request`` is one of

    * ``{"a": circuit/1, "b": circuit/1}``: two circuits (unitaries up to 10 wires, random
      states from 11 to 20);
    * ``{"circuit": circuit/1, "target": {"unitary": matrix}}``: the circuit's unitary against
      a matrix;
    * ``{"circuit": circuit/1, "target": {"state": amplitudes or basis label}}``: the circuit's
      state (from ``input``, default |0...0>) against a state;
    * one circuit/1 document with a drawn equality: its parts, left against right.

    A matrix or state is a list of numbers or ``[re, im]`` pairs; a matrix must be unitary. A
    circuit with measurements is refused in every one of these (it has no unitary, and no single
    state). ``equal_to_target`` is true only if every comparison is equal; ``comparisons`` has
    the details of each. ``values`` (the keyword, or the request's key) binds the symbols as in
    :func:`run`.
    """
    try:
        return _check(request, values)
    except _Refuse as stop:
        return refusal("check", stop.reasons)
    except (QuantumSimError, ValueError, TypeError) as error:
        return refusal("check", [str(error)])


def _two_circuits(envelope: dict[str, Any]) -> tuple[Reading, list[str]]:
    if "a" not in envelope or "b" not in envelope or "circuit" in envelope:
        raise _Refuse("compare two circuits with both 'a' and 'b' (and no 'circuit')")
    first, second = _read(envelope["a"], "a"), _read(envelope["b"], "b")
    for name, reading in (("a", first), ("b", second)):
        if len(reading.circuits) != 1:
            raise _Refuse(f"{name}: give one circuit without a drawn equality")
    merged = Reading(
        (first.circuits[0], second.circuits[0]),
        ("=",),
        first.notes + second.notes,
        first.input_kets + second.input_kets,
    )
    return merged, ["a", "b"]


def _note_unused_input(envelope: dict[str, Any], result: dict[str, Any]) -> None:
    """A note when a check was given an ``input`` it does not use: whether two circuits, or a
    circuit and a unitary, are equal does not depend on the input state."""
    if envelope.get("input") is not None:
        result["notes"].append(
            "input is not used: whether two circuits, or a circuit and a unitary, are equal does "
            "not depend on the input state"
        )


def _check(request: object, values: object) -> dict[str, Any]:
    envelope = _envelope(request, _CHECK_KEYS, "circuit")
    if "target" in envelope:
        return _check_target(envelope, values)
    if "a" in envelope or "b" in envelope:
        reading, names = _two_circuits(envelope)
    elif "circuit" in envelope:
        reading = _read(envelope["circuit"])
        names = [f"part {i}" for i in range(1, len(reading.circuits) + 1)]
    else:
        raise _Refuse("a check request needs 'a' and 'b', or a 'circuit' with a 'target'")
    if len(reading.circuits) < 2:
        raise _Refuse("a check needs two circuits, a target, or a drawn equality in the document")
    bound = _checked_values(values, envelope.get("values"), reading.symbols)
    result = _check_result(reading, bound)
    _note_unused_input(envelope, result)
    result["circuits"] = [_describe(i, c) for i, c in enumerate(reading.circuits, start=1)]
    if _symbolic(result, reading.symbols, bound):
        return result
    circuits = _bind(reading.circuits, bound)
    comparisons = []
    for i in range(len(circuits) - 1):
        outcome = equal(circuits[i], circuits[i + 1])
        comparisons.append(_comparison(names[i], names[i + 1], outcome))
        if not reading.claims_equality(i):
            result["notes"].append(
                f"the sign between {names[i]} and {names[i + 1]} is {reading.signs[i]!r}, not an "
                "equality; the parts were compared anyway"
            )
    result["comparisons"] = comparisons
    result["equal_to_target"] = all(c["equal"] for c in comparisons)
    return result


def _check_result(reading: Reading, bound: dict[str, float]) -> dict[str, Any]:
    result = _base("check", "ok")
    result["parameters"], result["values"] = list(reading.symbols), bound
    result["notes"] = list(reading.notes)
    return result


def _comparison(left: str, right: str, outcome: Any) -> dict[str, Any]:
    return {
        "between": [left, right],
        "equal": bool(outcome.equal),
        "exact": bool(outcome.exact),
        "phase": round(float(outcome.phase), DECIMALS),
        "max_deviation": float(f"{outcome.max_deviation:.6g}"),
        "method": outcome.method,
    }


def _check_target(envelope: dict[str, Any], values: object) -> dict[str, Any]:
    if "circuit" not in envelope or "a" in envelope or "b" in envelope:
        raise _Refuse("compare a circuit with a target using 'circuit' and 'target' only")
    target = envelope["target"]
    if not isinstance(target, dict) or set(target) not in ({"state"}, {"unitary"}):
        raise _Refuse('\'target\' must be {"state": ...} or {"unitary": ...}')
    reading = _read(envelope["circuit"])
    if len(reading.circuits) != 1:
        raise _Refuse("compare one circuit (without a drawn equality) with a target")
    bound = _checked_values(values, envelope.get("values"), reading.symbols)
    result = _check_result(reading, bound)
    result["circuits"] = [_describe(1, reading.circuits[0])]
    if _symbolic(result, reading.symbols, bound):
        return result
    (circuit,) = _bind(reading.circuits, bound)
    if "unitary" in target:
        outcome = equal(circuit, _target_unitary(target["unitary"]))
        names = ("circuit", "unitary")
        _note_unused_input(envelope, result)
    else:
        if circuit.measured_wires:  # `equal` between circuits refuses them too
            raise _Refuse(
                f"the circuit measures wire(s) {list(circuit.measured_wires)}: after a "
                "measurement there is no single state to compare with a target state"
            )
        state = _input_state(envelope.get("input"))
        result["input"] = _input_json(state, circuit.n_wires)
        outcome = equal(statevector(circuit, state), _target_state(target["state"], circuit))
        names = ("circuit", "state")
    result["comparisons"] = [_comparison(*names, outcome)]
    result["equal_to_target"] = bool(outcome.equal)
    return result


def _target_unitary(raw: object) -> NDArray[np.complex128]:
    """The target matrix; a square one must be unitary (U†U = I within ``ATOL``)."""
    matrix = decode(raw, 2)
    rows, columns = matrix.shape
    if rows == columns:
        deviation = float(np.max(np.abs(matrix.conj().T @ matrix - np.eye(rows))))
        if not deviation <= ATOL:
            raise _Refuse(
                "the target matrix is not unitary: "
                f"U†U differs from the identity by {deviation:.3g}"
            )
    return matrix


def _target_state(raw: object, circuit: Circuit) -> NDArray[np.complex128]:
    """The target state as amplitudes of the circuit's size and norm 1."""
    size = 2**circuit.n_wires
    if isinstance(raw, str):
        if len(raw) != circuit.n_wires or set(raw) - {"0", "1"}:
            raise _Refuse(f"{raw!r} is not a basis label of {circuit.n_wires} bits")
        state = np.zeros(size, dtype=np.complex128)
        state[int(raw, 2)] = 1
        return state
    state = decode(raw)
    if state.shape != (size,):
        raise _Refuse(f"the target state needs {size} amplitudes for {circuit.n_wires} wires")
    norm = float(np.linalg.norm(state))
    if abs(norm - 1) > ATOL:
        raise _Refuse(f"the target state has norm {norm}, not 1")
    return state
