"""OpenQASM 3 export of a ``circuit/1`` document, read by this package's reader.

The exporter is deliberately downstream of :func:`quantum_sim.formats.from_gate_list`: it never
interprets a gate label, a control or an angle itself. A document the simulator refuses is refused
here too, and nothing is marked ``"ok"`` that the simulator would not run. The output uses only
gates that ``stdgates.inc`` defines; a gate with no exact spelling is refused rather than guessed
at.

The rules are the rules of the reader plus five format rules:

* a gate with an open control is exported as ``x`` before and after the operation, which is exact
  for every gate and keeps the open control from becoming a closed one;
* a free angle is rendered as an OpenQASM expression, not rounded to a decimal; a symbol with no
  binding refuses that part, and so does a bound angle that the simulator cannot evaluate at the
  bound values (a division by zero, the square root of a negative number, a power that
  overflows), with the simulator's reason;
* every number is a float literal (``5.0``, ``2.0**3.0``), because OpenQASM's ``/`` on integers is
  integer division and an int-to-angle cast is not allowed;
* a ``|1>`` drawn at an input (a wire legend or a text item) refuses the document: the export does
  not add an initialization that the drawing did not state as an operation;
* wire ``i`` (1 at the top) is ``q[i-1]`` and a measurement writes ``c[i-1]``. A bit string of
  ``c`` reads ``c[n-1] ... c[0]``, so wire 1 is its rightmost character, while the simulator's
  basis label puts wire 1 first. The ``c`` lines of the header appear only when the program
  declares ``c``.

A binding name is one symbol of the angle grammar, by :func:`quantum_sim.angles.symbol_name`, the
rule the simulator's ``run`` and ``check`` use too: ``\\theta``, ``{\\theta}`` and ``theta`` are
``theta``, and ``\\theta_{1}`` is ``theta_1``. A key that is not one symbol (``thetta``, ``pi``,
``the{ta}``, a name with a line break inside it; white space around a name is ignored) is
refused as a usage error; a symbol the document does not use is reported in ``unmatched``. A
value given as text is read by :func:`quantum_sim.angles.parse_value`, the simulator's rule too
(``pi/4`` and ``\\pi/4`` are the same value). The header writes a ``// bound:`` line only for a
symbol of that part, under the reader's name, so a caller's text never reaches the header as a
name.

The standard library has no ``sxdg``; it is spelled ``inv @ sx``, which is exact. Gates the
simulator knows but ``stdgates.inc`` does not define (``rxx``, ``ryy``, ``rzz``, ``rk``,
``iswap``) are refused: v1 has no exact standard spelling for them (ADR 0006).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from quantum_sim.angles import Angle, binding_name, format_number, parse_value, symbol_name
from quantum_sim.domain import MEASURE, AngleError, RefusedError
from quantum_sim.formats import ParametricCircuit, ParametricOp, Reading, from_gate_list

__all__ = [
    "QASM_FORMAT_VERSION",
    "QasmExport",
    "QasmProgram",
    "to_qasm",
    "validate_bindings",
]

QASM_FORMAT_VERSION = 1

# Gates whose name is defined by the OpenQASM 3 standard library. ``sxdg`` is not here: it is
# written as ``inv @ sx`` below. The check in the tests keeps this table honest.
_STDGATES = frozenset(
    {"id", "h", "x", "y", "z", "s", "sdg", "t", "tdg", "sx", "rx", "ry", "rz", "p", "swap"}
)

# Simulator gates with no exact ``stdgates.inc`` spelling in v1. Refusing is better than a
# program that means something else.
_NO_STANDARD_FORM = {
    "rxx": "stdgates.inc has no rxx, and v1 does not decompose it",
    "ryy": "stdgates.inc has no ryy, and v1 does not decompose it",
    "rzz": "stdgates.inc has no rzz, and v1 does not decompose it",
    "rk": "stdgates.inc has no R_k, and v1 does not decompose it",
    "iswap": "stdgates.inc has no iSWAP, and v1 does not decompose it",
}

# A controlled standard gate whose one-control spelling has its own name in stdgates.inc.
_ONE_CONTROL = {
    "x": "cx",
    "y": "cy",
    "z": "cz",
    "h": "ch",
    "p": "cp",
    "rx": "crx",
    "ry": "cry",
    "rz": "crz",
    "swap": "cswap",
}


@dataclass(frozen=True)
class QasmProgram:
    """One OpenQASM 3 program: a circuit part, its drawn sign, and the text."""

    part: int
    sign: str | None
    text: str
    n_qubits: int
    n_clbits: int


@dataclass(frozen=True)
class QasmExport:
    """The result of exporting one document. ``status`` is ``"ok"`` or ``"refused"``.

    A refused document has no programs: a drawing with one part the exporter cannot express is
    refused as a whole, so no side of a drawn equality is written alone. ``reasons`` names each
    refused part (the reader's reasons when the simulator refused the document). ``notes`` are
    the reader's notes; ``symbols`` are the free parameters the document mentions, whether or
    not the caller bound them. ``unmatched`` holds the caller's binding keys, as given, whose
    name is none of those symbols (all of them when the reader refused the document).
    """

    status: str
    programs: tuple[QasmProgram, ...]
    reasons: tuple[str, ...]
    notes: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    unmatched: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.reasons


class _Bad(Exception):
    """One circuit part cannot be exported; the message is the reason given to the caller."""


def to_qasm(
    document: object,
    *,
    values: Mapping[str, str | float | int] | None = None,
    origin: str = "",
    tool: str = "quantum-sim",
) -> QasmExport:
    """Read ``document`` with :func:`from_gate_list` and export it as OpenQASM 3.

    ``values`` binds a free parameter by its name (``theta``, ``\\theta`` or ``{\\theta}``; see
    :func:`quantum_sim.angles.symbol_name`) to a LaTeX expression or a number; a key that is not
    one symbol refuses the document. ``origin`` is a one-line provenance comment. ``tool`` is the
    name in the generated-by header; a caller that embeds the exporter passes its own. The
    function returns refusals as data; it raises only for a programming error.
    """
    keys = tuple(values or {})
    try:
        reading = from_gate_list(document)
    except RefusedError as error:
        return QasmExport("refused", (), error.reasons, unmatched=keys)
    unmatched = tuple(key for key in keys if symbol_name(key) not in reading.symbols)
    seen = {"notes": reading.notes, "symbols": reading.symbols, "unmatched": unmatched}
    try:
        bindings = _bindings(values)
    except _Bad as exc:
        return QasmExport("refused", (), (str(exc),), **seen)
    inputs = [bit for _, bit in reading.input_kets]
    if "1" in inputs:
        return QasmExport(
            "refused",
            (),
            ("the drawing starts an input in |1>, which the export does not add",),
            **seen,
        )
    texts = {_name(key): _comment(str(value)) for key, value in (values or {}).items()}
    return _export(reading, bindings, texts, _Provenance(origin, None, tool, ()), unmatched)


def validate_bindings(values: Mapping[str, str | float | int] | None) -> None:
    """Refuse a binding value once, before a caller exports any circuit.

    A ``ValueError`` is a usage error: the caller's value or name is wrong, not one circuit.
    """
    try:
        _bindings(values)
    except _Bad as exc:
        raise ValueError(str(exc)) from None


@dataclass(frozen=True)
class _Provenance:
    """What the header says about the program and the caller."""

    origin: str
    sign: str | None
    tool: str
    bound: tuple[tuple[str, str], ...]


def _export(
    reading: Reading,
    bindings: Mapping[str, Angle],
    texts: Mapping[str, str],
    provenance: _Provenance,
    unmatched: tuple[str, ...],
) -> QasmExport:
    programs: list[QasmProgram] = []
    reasons: list[str] = []
    total = len(reading.circuits)
    for index, circuit in enumerate(reading.circuits, 1):
        sign = reading.signs[index - 2] if index > 1 else None
        if not sign:
            sign = None
        try:
            body, n_clbits = _part(circuit, bindings)
        except _Bad as exc:
            reasons.append(f"part {index}: {exc}")
            continue
        text = _render(
            circuit,
            n_clbits,
            body=body,
            provenance=_Provenance(
                origin=f"{provenance.origin}, part {index}/{total}" if provenance.origin else "",
                sign=sign,
                tool=provenance.tool,
                bound=tuple((name, texts[name]) for name in circuit.symbols if name in texts),
            ),
        )
        programs.append(QasmProgram(index, sign, text, circuit.n_wires, n_clbits))
    seen = {"notes": reading.notes, "symbols": reading.symbols, "unmatched": unmatched}
    if reasons:
        return QasmExport("refused", (), tuple(reasons), **seen)
    return QasmExport("ok", tuple(programs), (), **seen)


# ------------------------------------------------------------------ one part


def _part(circuit: ParametricCircuit, bindings: Mapping[str, Angle]) -> tuple[list[str], int]:
    """The operation lines of one part and its classical bit count (0 without a measurement)."""
    body: list[str] = []
    measured: set[int] = set()
    for op in circuit.ops:
        wires = {*op.wires, *op.controls, *op.open_controls}
        if op.gate == MEASURE:
            if op.controls or op.open_controls:
                raise _Bad("a controlled measurement is not exported")
            if len(op.wires) != 1:
                raise _Bad("a measurement of several wires is not exported")
            wire = op.wires[0]
            if wire in measured:
                raise _Bad(f"wire {wire} is measured twice")
            measured.add(wire)
            body.append(f"measure q[{wire - 1}] -> c[{wire - 1}];")
            continue
        if measured & wires:
            raise _Bad("an operation touches a wire after its measurement")
        body.extend(_op(op, bindings))
    return body, circuit.n_wires if measured else 0


def _op(op: ParametricOp, bindings: Mapping[str, Angle]) -> list[str]:
    name = op.gate
    if name in _NO_STANDARD_FORM:
        raise _Bad(f"gate {name!r}: {_NO_STANDARD_FORM[name]}")
    if name == "sxdg":
        base = "inv @ sx"
        params: tuple[str, ...] = ()
    elif name in _STDGATES:
        params = _params(op, bindings)
        base = name + _arguments(params)
    else:  # pragma: no cover - the reader only produces gates from the simulator table
        raise _Bad(f"gate {name!r} is not in the simulator's table")
    return [_emit(op, base, params)]


def _params(op: ParametricOp, bindings: Mapping[str, Angle]) -> tuple[str, ...]:
    unbound: set[str] = set()
    rendered = tuple(_angle(angle, bindings, unbound) for angle in op.params)
    if unbound:
        names = ", ".join(sorted(unbound))
        raise _Bad(f"unbound parameter(s): {names}; bind them with a value")
    if op.symbols:
        # The simulator evaluates the angle at the bound values; an angle it cannot evaluate
        # (1/0, a negative square root, an overflowing power) is not written as a program.
        try:
            op.bind({name: bindings[name].value for name in op.symbols})
        except AngleError as error:
            raise _Bad(f"gate {op.gate!r} at the bound values: {error}") from None
    return rendered


def _arguments(params: tuple[str, ...]) -> str:
    return f"({', '.join(params)})" if params else ""


def _emit(op: ParametricOp, base: str, params: tuple[str, ...]) -> str:
    """One operation; open controls are conjugated with X, which leaves the gate exact."""
    wrapper = [f"x q[{wire - 1}];" for wire in op.open_controls]
    all_controls = (*op.controls, *op.open_controls)
    count = len(all_controls)
    if count == 0:
        head = base
    elif op.gate == "sxdg":
        head = "ctrl @ " * count + base
    elif count == 1 and op.gate in _ONE_CONTROL:
        head = _ONE_CONTROL[op.gate] + _arguments(params)
    elif op.gate == "x" and count == 2:
        head = "ccx"
    else:
        head = "ctrl @ " * count + base
    wires = (*all_controls, *op.wires)
    operation = f"{head} {', '.join(f'q[{wire - 1}]' for wire in wires)};"
    return "\n".join([*wrapper, operation, *wrapper])


# ------------------------------------------------------------------ parameters


def _name(key: str) -> str:
    """The name a caller's binding key gives its symbol; a key that is not one symbol is a bad
    binding (:func:`quantum_sim.angles.symbol_name` is the rule)."""
    try:
        return binding_name(key)
    except AngleError as error:
        raise _Bad(str(error)) from None


def _bindings(values: Mapping[str, str | float | int] | None) -> dict[str, Angle]:
    """Parse every caller binding once; a value may not name another symbol."""
    bound: dict[str, Angle] = {}
    for key, value in (values or {}).items():
        name = _name(key)
        if name in bound:
            raise _Bad(f"parameter {name!r} is bound twice")
        try:
            if isinstance(value, bool):
                raise AngleError("a parameter value cannot be true or false")
            angle = (
                Angle.of(_float(value))
                if isinstance(value, int | float)
                else parse_value(str(value))
            )
        except AngleError as error:
            raise _Bad(f"value for {name!r}: {error}") from None
        if angle.symbols:
            names = ", ".join(angle.symbols)
            raise _Bad(f"value for {name!r} has free symbol(s): {names}")
        bound[name] = angle
    return bound


def _float(value: int | float) -> float:
    try:
        return float(value)
    except OverflowError:  # an integer too large for a float, 10**400
        raise AngleError("the number is too large for a float") from None


def _angle(angle: Angle, bindings: Mapping[str, Angle], unbound: set[str]) -> str:
    return _strip(_node(angle.tree, bindings, unbound))


def _number_text(number: float) -> str:
    """A number as an OpenQASM float literal; an integer literal would change the meaning."""
    text = format_number(float(number))
    if "." not in text:
        text += ".0"
    return f"({text})" if text.startswith("-") else text


def _node(node: Any, bindings: Mapping[str, Angle], unbound: set[str]) -> str:
    kind = node[0]
    text: str
    if kind == "num":
        text = _number_text(float(node[1]))
    elif kind == "pi":
        text = "pi"
    elif kind == "sym":
        name = node[1]
        bound = bindings.get(name)
        if bound is None:
            unbound.add(name)
            text = name
        else:
            text = _node(bound.tree, bindings, unbound)
    elif kind == "neg":
        text = f"(-{_node(node[1], bindings, unbound)})"
    elif kind == "sqrt":
        text = f"sqrt({_node(node[1], bindings, unbound)})"
    elif kind == "pow":
        text = f"({_node(node[1], bindings, unbound)}**{_number_text(float(node[2]))})"
    else:
        left, right = _node(node[1], bindings, unbound), _node(node[2], bindings, unbound)
        operator = {"add": "+", "sub": "-", "mul": "*", "div": "/"}[kind]
        text = f"({left}{operator}{right})"
    return text


def _strip(value: str) -> str:
    """Drop one layer of parentheses only when it wraps the whole expression."""
    if len(value) < 2 or value[0] != "(" or value[-1] != ")":
        return value
    depth = 0
    for position, character in enumerate(value):
        depth += character == "("
        depth -= character == ")"
        if depth == 0 and position < len(value) - 1:
            return value
    return value[1:-1]


# ------------------------------------------------------------------ text


def _render(
    circuit: ParametricCircuit,
    n_clbits: int,
    *,
    body: list[str],
    provenance: _Provenance,
) -> str:
    n_qubits = circuit.n_wires
    lines = [f"// Generated by {_comment(provenance.tool)} (qasm format {QASM_FORMAT_VERSION})"]
    if provenance.origin:
        lines.append(f"// source: {_comment(provenance.origin)}")
    if provenance.sign is not None:
        lines.append(f"// sign: {_comment(provenance.sign)}")
    for name, value in provenance.bound:
        lines.append(f"// bound: {name} = {value}")
    lines.append("// wire 1 (top) = q[0]; the simulator reads it as the most significant bit")
    if n_clbits:
        lines.append("// c[i] holds q[i]; c reads c[n-1] ... c[0], so wire 1 is its rightmost bit")
        lines.append("// bit[n] declares one classical bit per wire, not only the measured ones")
    legends = "; ".join(_comment(wire) for wire in circuit.wire_labels if wire.strip())
    if legends:
        lines.append(f"// wires: {legends}")
    lines.extend(["OPENQASM 3.0;", 'include "stdgates.inc";', f"qubit[{n_qubits}] q;"])
    if n_clbits:
        lines.append(f"bit[{n_clbits}] c;")
    lines.append("")
    return "\n".join([*lines, *body]) + "\n"


def _comment(text: str) -> str:
    """One comment line: every line break Python knows (``\\r``, ``\\v``, U+2028, ...) is a
    space, so no text can start a line of its own."""
    return " ".join(text.splitlines()).strip()
