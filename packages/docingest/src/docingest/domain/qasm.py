"""OpenQASM 3 export of a circuit read from a paper, as a pure function.

:class:`~docingest.domain.circuits.CircuitSteps` is the reading of a drawn circuit (wires, steps,
controls, open controls). This module projects that reading onto OpenQASM 3 text: one program per
part of a drawing (the sides of a drawn equality), with a comment naming the source and the wire
convention. It owns no I/O and no format reader; ``circuits.py`` reads the drawing, this module
writes a program, and neither guesses.

The rules are deliberately narrow:

* A circuit that is not fully read (``status`` other than ``"parsed"``) and any item whose kind is
  not an operation are refused, never skipped: dropping an item can leave a wrong program.
* A one-qubit gate on several targets is the same gate on each wire, as the data contract says;
  with controls that is ambiguous and is refused.
* A closed control acts on ``|1>`` and an open control on ``|0>``; an open control is exported as
  ``x`` before and after the operation, which is exact for every gate.
* Gate parameters are LaTeX arithmetic in a closed subset (numbers, ``\\pi``, ``+ - * /``,
  ``\\frac``, ``\\sqrt`` and Greek symbols). A free symbol is refused unless the caller binds it;
  nothing is evaluated to a rounded decimal.
* QASM has no fixed bit order. Wire 1 (the top wire, the most significant bit of the simulator's
  basis labels) becomes ``q[0]``, and the header says so. The exporter never reorders wires.
* A ``|1>`` drawn at the input of a wire is refused: QASM export does not add an initialization
  that the drawing did not state as an operation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .circuits import CircuitSteps, GateOp

__all__ = ["QASM_VERSION", "QasmExport", "QasmProgram", "to_qasm"]

QASM_VERSION = 1

# (target wires, parameters) of every gate with an exact OpenQASM 3 ``stdgates.inc`` spelling.
_GATES: dict[str, tuple[int, int]] = {
    "id": (1, 0),
    "h": (1, 0),
    "x": (1, 0),
    "y": (1, 0),
    "z": (1, 0),
    "s": (1, 0),
    "sdg": (1, 0),
    "t": (1, 0),
    "tdg": (1, 0),
    "sx": (1, 0),
    "sxdg": (1, 0),
    "rx": (1, 1),
    "ry": (1, 1),
    "rz": (1, 1),
    "p": (1, 1),
    "rxx": (2, 1),
    "ryy": (2, 1),
    "rzz": (2, 1),
    "swap": (2, 0),
}

# Spellings the author may write, mapped to the canonical name. Box labels that name a gate over
# several wires without saying which wire is the control are refused below, not guessed.
_ALIASES: dict[str, str] = {
    "i": "id",
    "id": "id",
    "identity": "id",
    "h": "h",
    "hadamard": "h",
    "x": "x",
    "not": "x",
    "y": "y",
    "z": "z",
    "s": "s",
    "sdg": "sdg",
    "t": "t",
    "tdg": "tdg",
    "sx": "sx",
    "sqrtx": "sx",
    "sxdg": "sxdg",
    "rx": "rx",
    "ry": "ry",
    "rz": "rz",
    "p": "p",
    "phase": "p",
    "u1": "p",
    "rxx": "rxx",
    "ryy": "ryy",
    "rzz": "rzz",
    "swap": "swap",
}

# A box label whose control wires are not in the drawing. The IR holds all wires in ``targets``.
_AMBIGUOUS: dict[str, str] = {
    "cnot": "the control wires of a box label are not known",
    "cx": "the control wires of a box label are not known",
    "ccx": "the control wires of a box label are not known",
    "toffoli": "the control wires of a box label are not known",
    "fredkin": "the control wires of a box label are not known",
    "cswap": "the control wires of a box label are not known",
    "u": "there is no exact OpenQASM 3 stdgates.inc form",
    "u2": "there is no exact OpenQASM 3 stdgates.inc form",
    "u3": "there is no exact OpenQASM 3 stdgates.inc form",
    "iswap": "stdgates.inc has no iSWAP, and v1 does not decompose it",
    "rk": "R_k has no exact stdgates.inc form",
    "measure": "a measurement is a measure item, not a gate",
}

# Greek letters a paper may use as a parameter. Kept distinct (``phi`` and ``varphi`` differ).
_SYMBOLS = frozenset(
    {
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "varepsilon",
        "zeta",
        "eta",
        "theta",
        "vartheta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "rho",
        "varrho",
        "sigma",
        "varsigma",
        "tau",
        "upsilon",
        "phi",
        "varphi",
        "chi",
        "psi",
        "omega",
        "Gamma",
        "Delta",
        "Theta",
        "Lambda",
        "Xi",
        "Pi",
        "Sigma",
        "Upsilon",
        "Phi",
        "Psi",
        "Omega",
    }
)

_WRAPPER = re.compile(r"\\(?:mathrm|text|operatorname|mathbf|mathsf|mathit|textsc)\s*\{([^{}]*)\}")
_DAGGER = re.compile(r"\^\s*(?:\{\s*\\(?:dagger|dag)\s*\}|\\dagger|\\dag|\{\s*-1\s*\})")
_SQRT = re.compile(r"\\sqrt\s*\{\s*([^{}]+?)\s*\}")
_SPACING = re.compile(r"\\[,;!: ]")
_TOKEN = re.compile(
    r"""\s*(?:
        (?P<number>\d+(?:\.\d*)?|\.\d+)
      | (?P<cmd>\\[A-Za-z]+)
      | (?P<ident>[A-Za-z]+)
      | (?P<op>[+\-*/^])
      | (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<lbrace>\{)
      | (?P<rbrace>\})
    )""",
    re.VERBOSE,
)


class _Bad(Exception):
    """One circuit part cannot be exported; the message is the reason given to the caller."""


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
    """The result of exporting one circuit. ``status`` is ``"ok"`` or ``"refused"``.

    A refused export can still hold programs: a part that could not be read does not stop the
    others. ``reasons`` names each refused part.
    """

    status: str
    programs: tuple[QasmProgram, ...]
    reasons: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.reasons


def to_qasm(
    circuit: CircuitSteps,
    *,
    values: Mapping[str, str] | None = None,
    origin: str = "",
) -> QasmExport:
    """Export ``circuit`` as OpenQASM 3, one program per part.

    ``values`` binds a free parameter by its canonical name (``theta`` or ``\\theta``) to a LaTeX
    expression in the same closed grammar. ``origin`` is a one-line provenance comment. The
    function returns refusals as data; it raises only for a programming error.
    """
    if circuit.status != "parsed":
        reason = f"circuit status is {circuit.status!r}: only a fully parsed circuit is exported"
        return QasmExport("refused", (), (reason,))
    n_qubits = len(circuit.wires)
    if n_qubits < 1:
        return QasmExport("refused", (), ("the circuit has no wires",))
    if not circuit.parts:
        return QasmExport("refused", (), ("the circuit has no parts",))
    try:
        bindings = _bindings(values)
    except _Bad as exc:
        return QasmExport("refused", (), (str(exc),))

    programs: list[QasmProgram] = []
    reasons: list[str] = []
    for index, part in enumerate(circuit.parts, 1):
        try:
            sign, start = _part_start(part, index)
            body, n_clbits = _part(part, start, n_qubits, bindings)
        except _Bad as exc:
            reasons.append(f"part {index}: {exc}")
            continue
        text = _render(
            n_qubits,
            n_clbits,
            body=body,
            wires=circuit.wires,
            origin=f"{origin}, part {index}/{len(circuit.parts)}" if origin else "",
            sign=sign,
        )
        programs.append(QasmProgram(index, sign, text, n_qubits, n_clbits))
    return QasmExport("ok" if not reasons else "refused", tuple(programs), tuple(reasons))


# ------------------------------------------------------------------ one part


def _part_start(part: tuple[tuple[GateOp, ...], ...], index: int) -> tuple[str | None, int]:
    """The drawn sign of a part and the first step to read (1 when the sign opens it)."""
    found = [
        (step_no, item) for step_no, step in enumerate(part) for item in step if item.kind == "sep"
    ]
    if not found:
        return None, 0
    if index == 1:
        raise _Bad("a drawn equality inside a part")
    if len(found) != 1:
        raise _Bad("a part holds more than one drawn equality")
    step_no, item = found[0]
    if step_no != 0 or len(part[0]) != 1:
        raise _Bad("a drawn equality must stand alone in its step")
    return item.label or "=", 1


def _part(
    part: tuple[tuple[GateOp, ...], ...],
    start: int,
    n_qubits: int,
    bindings: Mapping[str, str],
) -> tuple[list[str], int]:
    """The operation lines of one part and its classical bit count (0 without a measurement)."""
    body: list[str] = []
    measured: set[int] = set()
    for step in part[start:]:
        for item in step:
            _validate(item, n_qubits)
            if item.kind == "sep":
                raise _Bad("a drawn equality inside a part")
            if item.kind == "measure":
                body.append(_measure(item, measured))
                continue
            if item.kind not in _NEUTRAL and measured & _wires_of(item):
                raise _Bad("an operation touches a wire after its measurement")
            handler = _HANDLERS.get(item.kind)
            if handler is None:
                raise _Bad(f"item of kind {item.kind!r} is not an operation QASM can carry")
            body.extend(handler(item, bindings))
    return body, n_qubits if measured else 0


_NEUTRAL = frozenset({"text", "sign", "barrier"})


def _wires_of(item: GateOp) -> set[int]:
    return {*item.targets, *item.controls, *item.open_controls}


def _validate(item: GateOp, n_qubits: int) -> None:
    if item.classical:
        raise _Bad("a classically controlled item is not exported")
    wires = (*item.targets, *item.controls, *item.open_controls)
    for wire in wires:
        if not 1 <= wire <= n_qubits:
            raise _Bad(f"wire {wire} is outside the {n_qubits} wires of the circuit")
    if len(set(wires)) != len(wires):
        raise _Bad("a wire is named twice in one item")


def _measure(item: GateOp, measured: set[int]) -> str:
    if item.controls or item.open_controls:
        raise _Bad("a controlled measurement is not exported")
    if len(item.targets) != 1:
        raise _Bad("a measurement of several wires is not exported")
    wire = item.targets[0]
    if wire in measured:
        raise _Bad(f"wire {wire} is measured twice")
    measured.add(wire)
    return f"measure q[{wire - 1}] -> c[{wire - 1}];"


# ------------------------------------------------------------------ item kinds


def _gate(item: GateOp, bindings: Mapping[str, str]) -> list[str]:
    name, arg = _label(item.label)
    arity, n_params = _GATES[name]
    expression = _gate_expression(name, arg, n_params, bindings)
    if arity == 1:
        if not item.targets:
            raise _Bad(f"gate {name} has no target wire")
        if len(item.targets) > 1 and (item.controls or item.open_controls):
            raise _Bad("a controlled gate over several target wires is ambiguous")
        return [
            _emit(expression, item.controls, item.open_controls, (target,))
            for target in item.targets
        ]
    if len(item.targets) != 2:
        raise _Bad(f"gate {name} needs two target wires")
    return [_emit(expression, item.controls, item.open_controls, item.targets)]


def _x(item: GateOp, bindings: Mapping[str, str]) -> list[str]:
    if len(item.targets) != 1:
        raise _Bad("an X drawn as a target needs one target wire")
    return [_emit("x", item.controls, item.open_controls, item.targets)]


def _z_dots(item: GateOp, bindings: Mapping[str, str]) -> list[str]:
    closed, opened = item.controls, item.open_controls
    if len(closed) + len(opened) < 2:
        raise _Bad("a controlled-Z needs at least two dots")
    if not closed:
        raise _Bad("a controlled-Z whose dots are all open is not exported")
    target, controls = closed[-1], (*closed[:-1], *opened)
    return [_emit("z", controls, (), (target,))]


def _phase(item: GateOp, bindings: Mapping[str, str]) -> list[str]:
    if len(item.targets) != 1:
        raise _Bad("a phase gate needs one target wire")
    if not item.label.strip():
        raise _Bad("a phase gate has no angle")
    expression = _expression(item.label, bindings)
    return [_emit(f"p({expression})", item.controls, item.open_controls, item.targets)]


def _dots_gate(item: GateOp, bindings: Mapping[str, str]) -> list[str]:
    name, arg = _label(item.label)
    if name != "swap" or arg is not None:
        raise _Bad(f"a labelled dot gate {item.label!r} is not an exact two-qubit gate")
    if len(item.targets) != 2:
        raise _Bad("a labelled dot gate needs two target wires")
    return [_emit("swap", item.controls, item.open_controls, item.targets)]


def _swap(item: GateOp, bindings: Mapping[str, str]) -> list[str]:
    if len(item.targets) != 2:
        raise _Bad("a swap needs two target wires")
    return [_emit("swap", item.controls, item.open_controls, item.targets)]


def _barrier(item: GateOp, bindings: Mapping[str, str]) -> list[str]:
    if item.controls or item.open_controls:
        raise _Bad("a controlled barrier is not exported")
    if not item.targets:
        return ["barrier q;"]
    return [f"barrier {', '.join(f'q[{wire - 1}]' for wire in item.targets)};"]


def _text(item: GateOp, bindings: Mapping[str, str]) -> list[str]:
    ket = _ket(item.label)
    if ket in ("", "0"):
        return []
    if ket == "1":
        raise _Bad("the drawing starts a wire in |1>, which the export does not add")
    raise _Bad(f"text {item.label!r} is not a basis ket")


def _sign(item: GateOp, bindings: Mapping[str, str]) -> list[str]:
    if item.label.strip():
        raise _Bad(f"a sign {item.label!r} drawn on a wire is not exported")
    return []


_HANDLERS = {
    "gate": _gate,
    "x": _x,
    "z_dots": _z_dots,
    "phase": _phase,
    "dots_gate": _dots_gate,
    "swap": _swap,
    "barrier": _barrier,
    "text": _text,
    "sign": _sign,
}


def _emit(
    gate: str,
    controls: tuple[int, ...],
    open_controls: tuple[int, ...],
    targets: tuple[int, ...],
) -> str:
    """One operation; open controls are conjugated with X, which leaves the gate exact."""
    wrapper = [f"x q[{wire - 1}];" for wire in open_controls]
    all_controls = (*controls, *open_controls)
    count = len(all_controls)
    if count == 0:
        head = gate
    elif gate == "x" and count == 1:
        head = "cx"
    elif gate == "x" and count == 2:
        head = "ccx"
    elif gate == "z" and count == 1:
        head = "cz"
    elif gate == "swap" and count == 1:
        head = "cswap"
    else:
        head = "ctrl @ " * count + gate
    wires = (*all_controls, *targets)
    operation = f"{head} {', '.join(f'q[{wire - 1}]' for wire in wires)};"
    return "\n".join([*wrapper, operation, *wrapper])


# ------------------------------------------------------------------ labels and expressions


def _label(text: str) -> tuple[str, str | None]:
    """The canonical gate name and its raw parameter text, from the label the author wrote."""
    value = _clean(text)
    dagger = bool(_DAGGER.search(value))
    value = _DAGGER.sub("", value).strip()
    sqrt = _SQRT.fullmatch(value)
    if sqrt is not None:
        if sqrt.group(1).strip().lower() != "x":
            raise _Bad(f"gate {text!r} has no OpenQASM 3 form")
        return ("sxdg" if dagger else "sx"), None
    name, argument = _split_argument(value, text)
    name = re.sub(r"[\s_{}]", "", name).lower().lstrip("\\")
    if dagger:
        if name not in {"s", "t", "sx"}:
            raise _Bad(f"a dagger on {text!r} is not read")
        name += "dg"
    canonical = _ALIASES.get(name)
    if canonical is None:
        reason = _AMBIGUOUS.get(name)
        if reason is not None:
            raise _Bad(f"gate {text!r}: {reason}")
        raise _Bad(f"gate {text!r} has no OpenQASM 3 form")
    return canonical, argument


def _clean(text: str) -> str:
    value = text.strip()
    if len(value) >= 2 and value.startswith("$") and value.endswith("$"):
        value = value[1:-1].strip()
    value = value.replace("\\left", "").replace("\\right", "")
    value = _SPACING.sub("", value)
    for _ in range(2):
        value = _WRAPPER.sub(r"\1", value)
    return value.strip()


def _split_argument(value: str, original: str) -> tuple[str, str | None]:
    if not value.endswith(")"):
        return value, None
    depth = 0
    for position in range(len(value) - 1, -1, -1):
        character = value[position]
        if character == ")":
            depth += 1
        elif character == "(":
            depth -= 1
            if depth == 0:
                if position == 0:
                    raise _Bad(f"gate {original!r} has no name")
                return value[:position], value[position + 1 : -1].strip()
    raise _Bad(f"gate {original!r} has an unbalanced argument")


def _gate_expression(
    name: str, argument: str | None, n_params: int, bindings: Mapping[str, str]
) -> str:
    if n_params == 0:
        if argument:
            raise _Bad(f"gate {name} takes no angle")
        return name
    if not argument:
        raise _Bad(f"gate {name} needs an angle")
    return f"{name}({_expression(argument, bindings)})"


def _expression(text: str, bindings: Mapping[str, str]) -> str:
    parser = _Expression(text, bindings)
    value = parser.parse()
    unbound = sorted(parser.symbols)
    if unbound:
        names = ", ".join(unbound)
        raise _Bad(f"unbound parameter(s): {names}; bind them with a value")
    return value


def _bindings(values: Mapping[str, str] | None) -> dict[str, str]:
    """Parse every caller binding once; values may not name another symbol."""
    bound: dict[str, str] = {}
    for key, value in (values or {}).items():
        name = key.strip().lstrip("\\")
        if not name:
            raise _Bad("a parameter binding has an empty name")
        try:
            bound[name] = _Expression(str(value), {}).parse()
        except _Bad as exc:
            raise _Bad(f"value for {name!r}: {exc}") from exc
    return bound


def _ket(text: str) -> str:
    value = text.strip().replace("\\left", "").replace("\\right", "")
    value = value.replace("\\rangle", ">").replace("\\vert", "|").replace("\\mid", "|")
    match = re.fullmatch(r"\\ket\s*\{?\s*([01])\s*\}?|\\ket([01])|\|([01])\s*>", value)
    if match is None:
        return value
    return next(group for group in match.groups() if group is not None)


class _Expression:
    """A closed LaTeX arithmetic subset, canonicalized to an OpenQASM 3 expression.

    Accepted: decimal numbers, ``\\pi``/``pi``, ``+ - * /``, parentheses, ``\\frac{a}{b}``,
    ``\\sqrt{a}`` and Greek symbols. ``^`` is refused: an exponent has no exact spelling in the
    subset v1 promises. A bound symbol is replaced by its parsed value in parentheses.
    """

    def __init__(self, text: str, bindings: Mapping[str, str]) -> None:
        self._tokens = _tokenize(text)
        self._position = 0
        self._bindings = bindings
        self.symbols: set[str] = set()

    def parse(self) -> str:
        value = self._expr()
        token = self._peek()
        if token is not None:
            raise _Bad(f"unexpected {token[1]!r} in {self._source()}")
        return _strip(value)

    def _source(self) -> str:
        return " ".join(token for _, token in self._tokens) or "an empty expression"

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._position] if self._position < len(self._tokens) else None

    def _take(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise _Bad(f"the expression {self._source()} ends too early")
        self._position += 1
        return token

    def _expr(self) -> str:
        value = self._term()
        while (token := self._peek()) is not None and token[0] == "op" and token[1] in "+-":
            operator = self._take()[1]
            value = f"({value}{operator}{self._term()})"
        return value

    def _term(self) -> str:
        value = self._unary()
        while (token := self._peek()) is not None:
            if token[0] == "op" and token[1] in "*/":
                operator = self._take()[1]
                value = f"({value}{operator}{self._unary()})"
            elif token[0] in {"number", "cmd", "ident", "lparen", "lbrace"}:
                value = f"({value}*{self._unary()})"
            else:
                break
        return value

    def _unary(self) -> str:
        token = self._peek()
        if token is not None and token[0] == "op" and token[1] == "-":
            self._take()
            return f"(-{self._unary()})"
        return self._atom()

    def _atom(self) -> str:
        kind, token = self._take()
        if kind == "number":
            return _number(token)
        if kind in {"lparen", "lbrace"}:
            value = self._expr()
            closing = "rparen" if kind == "lparen" else "rbrace"
            if self._take()[0] != closing:
                raise _Bad(f"unbalanced {token!r} in {self._source()}")
            return f"({value})"
        if kind == "cmd":
            return self._command(token)
        if kind == "ident":
            return self._ident(token)
        raise _Bad(f"unexpected {token!r} in {self._source()}")

    def _command(self, token: str) -> str:
        name = token[1:]
        if name == "pi":
            return "pi"
        if name == "frac":
            numerator = self._group("lbrace")
            denominator = self._group("lbrace")
            return f"({numerator}/{denominator})"
        if name == "sqrt":
            return f"sqrt({self._group('lbrace')})"
        if name in _SYMBOLS:
            return self._symbol(name)
        raise _Bad(f"{token!r} is not in the parameter grammar")

    def _ident(self, token: str) -> str:
        if token == "pi":
            return "pi"
        if token in _SYMBOLS:
            return self._symbol(token)
        raise _Bad(f"{token!r} is not in the parameter grammar")

    def _symbol(self, name: str) -> str:
        bound = self._bindings.get(name)
        if bound is not None:
            return f"({bound})"
        self.symbols.add(name)
        return name

    def _group(self, opening: str) -> str:
        if self._take()[0] != opening:
            raise _Bad(f"expected '{{' in {self._source()}")
        value = self._expr()
        if self._take()[0] != "rbrace":
            raise _Bad(f"unbalanced '{{' in {self._source()}")
        return value


def _number(token: str) -> str:
    if token.startswith("."):
        return "0" + token
    if token.endswith("."):
        return token + "0"
    return token


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


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            if text[position].isspace():
                position += 1
                continue
            raise _Bad(f"unexpected character {text[position]!r} in {text!r}")
        kind = match.lastgroup
        if kind is None:  # pragma: no cover - the alternatives above are exhaustive
            raise _Bad(f"unexpected token in {text!r}")
        tokens.append((kind, match.group(kind)))
        position = match.end()
    if not tokens:
        raise _Bad("an empty parameter")
    return tokens


def _render(
    n_qubits: int,
    n_clbits: int,
    *,
    body: list[str],
    wires: tuple[str, ...],
    origin: str,
    sign: str | None,
) -> str:
    lines = [f"// Generated by docingest (qasm format {QASM_VERSION})"]
    if origin:
        lines.append(f"// source: {_comment(origin)}")
    if sign is not None:
        lines.append(f"// sign: {_comment(sign)}")
    lines.append("// wire 1 (top of the drawing) = q[0]; OpenQASM fixes no bit order")
    legends = "; ".join(_comment(wire) for wire in wires if wire.strip())
    if legends:
        lines.append(f"// wires: {legends}")
    lines.extend(["OPENQASM 3.0;", 'include "stdgates.inc";', f"qubit[{n_qubits}] q;"])
    if n_clbits:
        lines.append(f"bit[{n_clbits}] c;")
    lines.append("")
    return "\n".join([*lines, *body]) + "\n"


def _comment(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ").strip()
