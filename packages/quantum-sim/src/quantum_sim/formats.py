"""The JSON circuit format ``circuit/1``: reader and writer. Standard library only.

``circuit/1`` is exactly the shape that docingest's ``CircuitSteps.to_dict()`` produces, and the
shape an agent can write by hand:

    {"format": "circuit/1", "wires": 2,
     "parts": [[[{"kind": "gate", "label": "H", "targets": [1]}],
                [{"kind": "x", "label": "X", "targets": [2], "controls": [1]}]]]}

``parts`` is a list of parts (the sides of drawn equalities), a part is a list of steps, a step
a list of items. An item has a ``kind``, a ``label`` (LaTeX as the author wrote it),
``targets``, ``controls`` and ``open_controls`` (1-based wires, top to bottom), ``classical``
and ``source``; everything but ``kind`` may be left out. The circuit's own ``kind`` (the
notation: ``quantikz``, ``qcircuit``, ``tikzcd``, ``yquant``) and ``status`` are read when
present; ``label`` and ``wire_labels`` too. A key that is none of these is refused, in an item
and in the document: a misspelled ``controls`` would otherwise be dropped and change the
circuit (``input`` and ``values`` belong in the request that holds the document).

``from_gate_list`` turns a document into circuits, one per part, or refuses the whole of it. It
refuses, with the place of each cause (``part 1, step 3, item 1: ...``): a status other than
``parsed``; an item that is unparsed (``unparsed``, ``unparsed_op``, ``unparsed_group``,
``unparsed_outside``), of a kind it does not know, a row of dots, a ``dots_gate``, a lone control,
a discard, a ``sign``, a ``phase``, or text that is not a ket |0> or |1> (a bra, a projector and an
expectation value are text that is not one; so is text of more than ``MAX_LABEL_CHARS``
characters, or text with controls); a slice title (the label of a ``barrier``) that is not empty
or a plain name; a wire legend that is not read (see below); a gate label that is unknown or
ambiguous; a one-qubit label on one box over several wires in a quantikz, qcircuit or tikzcd
drawing (``\\gate[2]{H}`` does not say it is H on each wire); a classically controlled item; a gate
with a label drawn beside it (``source``); a measurement in another basis or with controls; a wire
legend that shows a bundle, a register, a row of dots or a classical wire; a wire named twice in
one item, or used by two items of one step (a column has no order); an operation after the
measurement of its wire. Nothing is skipped to make a circuit simulable.

Text is read by what it may hold, never by what it must not: a postselection, a projector or a bra
can be written in more ways than any list has (``\\bra{1}``, ``\\kb{1}{1}``, ``\\Proj{1}``,
``\\left<1\\right\\vert``, a bar or a bracket in Unicode, the word postselect), so no spelling is
looked for. A wire legend is a line of docingest's (``2 = label, output label``, with ``N``
alone for a wire with neither) and is read only if its input part is empty, a plain name or a
computational-basis ket (``\\ket{0}``, ``\\ket0``, ``|0\\rangle``, ``\\left|0\\right\\rangle``, and
1), and its output part is empty or a plain name (a letter, digits, a subscript, primes). A word is
not a name: it can say that the run is postselected or what the input is. An output ket, bra,
bar, projector, macro, word, number (``output 1`` may be a postselection on 1) or anything else
refuses the whole circuit ("output legend not read: ..."): a postselection is never simulated
in version 1. Text on a wire is read only if it is empty or a ket |0> or |1> in those spellings.
A ket |1> in a wire legend or in a text item is not read as an input state: it becomes a note, and
the input is chosen by the caller (default |0...0>). A ket in a text item on a wire an
operation uses, in an earlier step or in its own step (a step has no order), |0> too, refuses the
circuit: it may be a reset or a postselection. So does a ket with no wire. The document ``label``
is not read: a non-empty one becomes a note, and one that is not text refuses the circuit, like a
``kind`` that is not text and a legend numbered for another wire than its place.

Reading stops once ``MAX_REASONS`` causes are listed, so that the cost of a refusal does not grow
with the size of the document; the last line of the list then says there may be more.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeGuard

from quantum_sim.angles import Angle
from quantum_sim.domain import (
    GATES,
    MAX_OPS,
    MAX_REASONS,
    MEASURE,
    Circuit,
    LabelError,
    Op,
    QuantumSimError,
    RefusedError,
    short_text,
    validate,
)
from quantum_sim.labels import MAX_LABEL_CHARS, label_of, resolve_label

CIRCUIT_FORMAT = "circuit/1"
MAX_ITEMS = 50_000  # items in one document; checked before any is read

_UNPARSED = frozenset({"unparsed", "unparsed_op", "unparsed_group", "unparsed_outside"})
_KIND_REASONS = {
    "dots": "a row of dots stands for a variable number of wires or gates",
    "dots_gate": "a gate named by a label beside control dots is not read",
    "control": "a control dot with nothing joined to it",
    "discard": "a discarded wire (a partial trace) is not simulated",
    "sign": "a sign drawn on a wire inside a part",
    "phase": "a drawn phase item: the drawing does not say whether it is diag(1, e^{i theta}) "
    "or a global phase",
    "sep": "a drawn equality in the middle of a part",
}
# What a legend shows that the reader cannot simulate: LaTeX as an author writes it, and the
# words docingest writes for a bundle ("2 = a, bundle m") and a row of dots ("2 = dots (more
# wires, not drawn)").
_WIRE_FACTS = re.compile(
    r"\\(?:qwbundle|cwx|cw|setwiretype|vdots|cdots|ldots|dots|ddots|otimes|bundle)(?![A-Za-z])"
    r"|\bcbit\b|/\^|\{/|\bbundle\b|\bdots \(more wires"
)
_BOX_NOTATIONS = frozenset({"quantikz", "qcircuit", "tikzcd"})  # one box can span several wires
_DOCUMENT_KEYS = frozenset({"format", "kind", "status", "label", "wires", "wire_labels", "parts"})
_ITEM_KEYS = frozenset(
    {"kind", "label", "targets", "controls", "open_controls", "classical", "source"}
)
_REQUEST_KEYS = frozenset({"input", "values", "unitary"})  # of the run request, not the document
# What a wire legend or a text item may hold. docingest writes a legend as "N", "N = label",
# "N = label, output label" or "N = output label"; a label that stands for several wires ends in
# " (one label for wires 1 and 2)". Everything else is outside the grammar and is not read.
_SPAN = re.compile(
    r" \(one label for wires (?:[0-9]+ to [0-9]+|[0-9]+(?:, [0-9]+)*(?: and [0-9]+)?)\)\Z"
)
_LEGEND_LINE = re.compile(r"(?P<number>[0-9]+)(?: ?= ?(?P<body>.*))?")
_OUTPUT_WORD = re.compile(r"(?<![A-Za-z0-9_\\])output(?![A-Za-z0-9_])")
# a name of a wire: one letter (alone or in \mathrm{} or \text{}), up to two digits, a subscript (a
# character, or up to eight in braces), primes: q_0, a1, x', \mathrm{A}_1, S_{2m-1}, q_{2n,0}.
# Never a word: a word can say that the run is postselected or what the input is, and no list of
# words is complete.
_SUBSCRIPT = r"_(?:[A-Za-z0-9]|\{[A-Za-z0-9][A-Za-z0-9+,-]{0,7}\})"
_NAME_HEAD = r"(?:[A-Za-z]|\\(?:mathrm|text)\{[A-Za-z]\})[0-9]{0,2}'*"
_SYMBOL = re.compile(rf"{_NAME_HEAD}(?:{_SUBSCRIPT})?'*")
# An output (and a slice title) is stricter: no two letters in a row in a braced subscript, so
# q_{postsel}, x_{ps} and P_{proj} are not names. v_1, S_{2m-1} and q_{2n,0} still are.
_OUTPUT_SUBSCRIPT = (
    r"_(?:[A-Za-z0-9]|\{(?![A-Za-z0-9+,-]*[A-Za-z]{2})[A-Za-z0-9][A-Za-z0-9+,-]{0,7}\})"
)
_OUTPUT_SYMBOL = re.compile(rf"{_NAME_HEAD}(?:{_OUTPUT_SUBSCRIPT})?'*")
# a ket of a computational-basis state, in the spellings docingest copies from a paper
_BAR = r"(?:\||\\lvert|\\vert)"
_BASIS_KET = re.compile(
    r"\\ket\s*\{\s*([01])\s*\}"
    r"|\\ket\s*([01])"
    rf"|\\left\s*{_BAR}\s*([01])\s*\\right\s*\\rangle"
    rf"|{_BAR}\s*([01])\s*\\rangle",
    re.ASCII,
)
_ASCII_SPACE = re.compile(r"[ \t\r\n\f\v]+")
_EQUALITY_SIGNS = frozenset({"=", r"\equiv", r"\simeq", r"\Leftrightarrow", r"\iff", r"\cong"})


# --- Circuits with free parameters --------------------------------------------------------------


@dataclass(frozen=True)
class ParametricOp:
    """An ``Op`` whose parameters are angles that may have free symbols."""

    gate: str
    wires: tuple[int, ...]
    controls: tuple[int, ...] = ()
    open_controls: tuple[int, ...] = ()
    params: tuple[Angle, ...] = ()

    @property
    def symbols(self) -> frozenset[str]:
        return frozenset(s for angle in self.params for s in angle.symbols)

    def bind(self, values: Mapping[str, float]) -> Op:
        return Op(
            self.gate,
            self.wires,
            controls=self.controls,
            open_controls=self.open_controls,
            params=tuple(angle.evaluate(values) for angle in self.params),
        )


@dataclass(frozen=True)
class ParametricCircuit:
    """A circuit of ``ParametricOp``: simulate it after ``bind`` gives every symbol a value."""

    n_wires: int
    ops: tuple[ParametricOp, ...] = ()
    wire_labels: tuple[str, ...] = ()
    label: str = ""

    @property
    def symbols(self) -> tuple[str, ...]:
        """The free symbols, sorted."""
        return tuple(sorted({s for op in self.ops for s in op.symbols}))

    @property
    def is_symbolic(self) -> bool:
        return any(op.symbols for op in self.ops)

    def bind(self, values: Mapping[str, float] | None = None) -> Circuit:
        """The circuit with a value for every symbol (``AngleError`` if one is missing, or if
        an expression divides by zero or overflows at these values)."""
        return Circuit(self.n_wires, tuple(op.bind(values or {}) for op in self.ops))

    @classmethod
    def from_circuit(cls, circuit: Circuit) -> ParametricCircuit:
        """A numeric ``Circuit`` as a ``ParametricCircuit`` without symbols."""
        ops = tuple(
            ParametricOp(
                op.gate,
                op.wires,
                op.controls,
                op.open_controls,
                tuple(Angle.of(float(p)) for p in op.params),
            )
            for op in circuit.ops
        )
        return cls(circuit.n_wires, ops)


@dataclass(frozen=True)
class Reading:
    """What a circuit/1 document holds: one circuit for each part.

    ``signs[i]`` is the sign drawn between part ``i`` and part ``i + 1`` (``=``, ``\\equiv``,
    ``\\to``, ...); ``claims_equality`` says whether it is one that states an identity.
    ``notes`` are things the caller should know that did not stop the reading.
    """

    circuits: tuple[ParametricCircuit, ...]
    signs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    # Every basis ket at an input (a wire legend or a text item before any operation), as
    # (wire, "0" | "1"). The reader does not add an initialization; a caller that writes a
    # program can use this to refuse a |1> it cannot state as an operation.
    input_kets: tuple[tuple[int, str], ...] = ()

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted({s for c in self.circuits for s in c.symbols}))

    def claims_equality(self, index: int) -> bool:
        """Whether the sign after part ``index`` states an equality (not an arrow)."""
        return self.signs[index] in _EQUALITY_SIGNS


# --- Reader ------------------------------------------------------------------------------------


class _Reasons:
    def __init__(self) -> None:
        self.items: list[str] = []
        self.extra = 0
        self.cut = False  # reading stopped once the list was full: the rest is not counted

    def add(self, place: str, text: str) -> None:
        message = f"{place}: {text}" if place else text
        if len(self.items) < MAX_REASONS:
            self.items.append(message)
        else:
            self.extra += 1

    def __bool__(self) -> bool:
        return bool(self.items)

    @property
    def full(self) -> bool:
        return len(self.items) >= MAX_REASONS

    def listed(self) -> list[str]:
        """The messages, with a last line that says there are more than ``MAX_REASONS``."""
        if self.cut:  # the reader stopped: the cost of a refusal does not grow with the document
            return [
                *self.items,
                f"... reading stopped after {MAX_REASONS} causes; there may be more",
            ]
        return [*self.items, f"... and {self.extra} more"] if self.extra else list(self.items)

    def error(self) -> RefusedError:
        return RefusedError(self.listed())


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


class _Bad(Exception):
    """Raised inside the reader for one cause; the caller records it and goes on."""

    def __init__(self, text: str, place: str = "") -> None:
        super().__init__(text)
        self.text, self.place = text, place


@dataclass(frozen=True)
class _Item:
    """One item of the document after its fields are checked."""

    kind: str
    label: str
    targets: tuple[int, ...]
    controls: tuple[int, ...]
    opened: tuple[int, ...]
    notation: str = ""  # the ``kind`` of the document: quantikz, qcircuit, tikzcd, yquant, ...

    def ops(self, gate: str, wires: tuple[int, ...]) -> ParametricOp:
        return ParametricOp(gate, wires, self.controls, self.opened)


def _int_tuple(item: dict[str, Any], name: str) -> tuple[int, ...]:
    value = item.get(name)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(_is_int(w) for w in value):
        raise _Bad(f"{name} must be a list of integers")
    return tuple(value)


def _check_keys(raw: dict[Any, Any], allowed: frozenset[str], what: str, place: str = "") -> None:
    """``_Bad`` for the first key (in sorted order) that is not in ``allowed``: a misspelled
    ``controls`` must not be dropped and leave a different circuit."""
    unknown = sorted(short_text(key) for key in raw if key not in allowed)
    if unknown:
        hint = (
            f"; put {unknown[0]!r} in the request that holds the circuit, not in the circuit"
            if unknown[0] in _REQUEST_KEYS
            else ""
        )
        raise _Bad(
            f"unknown key {unknown[0]!r}; the keys of {what} are {sorted(allowed)}{hint}", place
        )


def _read_item(raw: object, n_wires: int, notation: str) -> _Item:
    """The checked fields of ``raw``; ``_Bad`` for anything this reader must not guess at."""
    if not isinstance(raw, dict):
        raise _Bad("an item must be an object")
    _check_keys(raw, _ITEM_KEYS, "an item")
    kind, label = raw.get("kind"), raw.get("label", "")
    if not isinstance(kind, str) or not isinstance(label, str):
        raise _Bad("an item needs a text 'kind' and a text 'label'")
    source, classical = raw.get("source", ""), raw.get("classical", False)
    if not isinstance(source, str) or not isinstance(classical, bool):
        raise _Bad("'source' must be text and 'classical' true or false")
    item = _Item(
        kind,
        label,
        _int_tuple(raw, "targets"),
        _int_tuple(raw, "controls"),
        _int_tuple(raw, "open_controls"),
        notation,
    )
    if kind in _UNPARSED:
        raise _Bad(f"{kind} item {source[:60]!r}: not understood")
    named: set[int] = set()
    for wire in (*item.targets, *item.controls, *item.opened):
        if not 1 <= wire <= n_wires:
            raise _Bad(f"wire {short_text(wire)} is not in 1..{short_text(n_wires)}")
        if wire in named:
            raise _Bad(f"wire {wire} is named twice in this item")
        named.add(wire)
    if classical:
        raise _Bad(f"{kind} {label!r} is classically controlled (not simulated in v1)")
    if source:
        raise _Bad(f"{kind} {label!r} carries drawn text {source[:60]!r} that is not read")
    return item


def _gate(item: _Item) -> list[ParametricOp]:
    try:
        resolved = resolve_label(item.label)
    except LabelError as error:
        raise _Bad(str(error)) from None
    if not item.targets:
        raise _Bad(f"gate {item.label!r} has no target wire")
    two_wires = resolved.implied_controls or GATES[resolved.gate].n_targets == 2
    if two_wires and len(item.targets) != resolved.drawn_wires:
        raise _Bad(
            f"gate {item.label!r} acts on {resolved.drawn_wires} wires, "
            f"the item has {len(item.targets)}"
        )
    params = resolved.params
    if resolved.implied_controls:  # CZ: the first drawn wire controls Z on the second
        wires, controls = (item.targets[1],), (item.targets[0], *item.controls)
        return [ParametricOp(resolved.gate, wires, controls, item.opened, params)]
    if GATES[resolved.gate].n_targets == 1:  # a one-qubit label on k wires: one gate on each
        if len(item.targets) > 1 and item.notation in _BOX_NOTATIONS:
            raise _Bad(
                f"gate {item.label!r} is one box over {len(item.targets)} wires in a "
                f"{item.notation} drawing: that is not necessarily the gate on each wire"
            )
        return [
            ParametricOp(resolved.gate, (w,), item.controls, item.opened, params)
            for w in item.targets
        ]
    return [ParametricOp(resolved.gate, item.targets, item.controls, item.opened, params)]


def _x(item: _Item) -> list[ParametricOp]:
    if item.label not in ("X", "") or not item.targets:
        raise _Bad(f"x item with label {item.label!r} and {len(item.targets)} target(s)")
    return [item.ops("x", (w,)) for w in item.targets]


def _z_dots(item: _Item) -> list[ParametricOp]:
    if item.label not in ("", "Z") or item.targets:
        raise _Bad(
            f"control dots with label {item.label!r} and {len(item.targets)} target(s): "
            "only dots that carry a Z, with no target, are read"
        )
    if len(item.controls) + len(item.opened) < 2:
        raise _Bad("control dots for a Z need at least two wires")
    if not item.controls:
        raise _Bad("control dots that are all open: no wire to carry the Z")
    # Z is -1 on |1> of its target, so any closed dot can carry it; the others control it.
    return [ParametricOp("z", (item.controls[-1],), item.controls[:-1], item.opened)]


def _swap(item: _Item) -> list[ParametricOp]:
    if item.label not in ("", "SWAP"):
        raise _Bad(f"swap with label {item.label!r}: only a plain SWAP is read (not iSWAP, a root)")
    if len(item.targets) != 2:
        raise _Bad(f"a swap needs two wires, the item has {len(item.targets)}")
    return [item.ops("swap", item.targets)]


def _measure(item: _Item) -> list[ParametricOp]:
    if item.label not in ("", "Z") or item.controls or item.opened or not item.targets:
        raise _Bad(
            "only a computational-basis measurement of wires, without controls, is read "
            f"(label {item.label!r})"
        )
    return [ParametricOp(MEASURE, (w,)) for w in item.targets]


def _barrier(item: _Item) -> list[ParametricOp]:
    if item.controls or item.opened:
        raise _Bad("a barrier with controls")
    if _too_long(item.label):
        raise _Bad(f"a slice title of {len(item.label):,} characters (at most {MAX_LABEL_CHARS})")
    if _squeezed(item.label) and not _is_output_name(item.label):
        raise _Bad(
            f"the slice title {item.label[:60]!r} is not read; a slice title is read only if it is "
            "empty or a plain name (a letter, a subscript): a word there may be a postselection, "
            "which is not simulated"
        )
    return []


def _too_long(text: str) -> bool:
    return len(text) > MAX_LABEL_CHARS


def _squeezed(text: str) -> str:
    """``text`` with every run of ASCII white space as one space, none at either end."""
    return _ASCII_SPACE.sub(" ", text).strip(" ")


def _is_output_name(text: str) -> bool:
    """Whether ``text`` is a plain name that cannot be a word (see ``_OUTPUT_SYMBOL``)."""
    return _OUTPUT_SYMBOL.fullmatch(_unwrapped(_squeezed(text))) is not None


def _unwrapped(text: str) -> str:
    """``text`` without the dollar signs and braces that wrap the whole of it. A pair that does
    not belong together (``{a}_{b}``) leaves a text that no pattern below matches."""
    while (len(text) > 1 and text[0] == text[-1] == "$") or (text[:1] == "{" and text[-1:] == "}"):
        text = text[1:-1].strip(" ")
    return text


def _basis_ket(text: str) -> str | None:
    """``"0"`` or ``"1"`` when ``text`` is exactly a ket of that computational-basis state
    (``\\ket{0}``, ``\\ket 0``, ``|0\\rangle``, ``\\left|0\\right\\rangle``, between dollar signs
    or braces too), else ``None``."""
    match = _BASIS_KET.fullmatch(_unwrapped(_squeezed(text)))
    return next(group for group in match.groups() if group) if match else None


def _text(item: _Item) -> list[ParametricOp]:
    if _too_long(item.label):
        raise _Bad(f"text of {len(item.label):,} characters on a wire (at most {MAX_LABEL_CHARS})")
    if item.controls or item.opened:
        raise _Bad("a text item with controls")
    if _squeezed(item.label) and _basis_ket(item.label) is None:
        raise _Bad(f"text {item.label[:60]!r} on a wire is not a gate and not a ket |0> or |1>")
    return []


_HANDLERS = {
    "gate": _gate,
    "x": _x,
    "z_dots": _z_dots,
    "swap": _swap,
    "measure": _measure,
    "barrier": _barrier,
    "text": _text,
}


def _item_ops(raw: object, n_wires: int, notation: str) -> tuple[_Item, list[ParametricOp]]:
    item = _read_item(raw, n_wires, notation)
    handler = _HANDLERS.get(item.kind)
    if handler is None:
        raise _Bad(
            _KIND_REASONS.get(item.kind)
            or f"item of kind {item.kind!r}, which this reader does not know"
        )
    return item, handler(item)


_INPUT_RULE = (
    "an input is read only if it is empty, a plain name (a letter, a subscript) or a ket |0> or "
    "|1> (\\ket{0}, |1\\rangle)"
)
_OUTPUT_RULE = (
    "an output is read only if it is empty or a plain name (a letter, a subscript); a ket, a "
    "bra, a projector, a word or a number there may be a postselection, which is not simulated"
)


def _split_legend(legend: str) -> tuple[str, str]:
    """(input part, output part) of a legend as docingest writes it: ``N``, ``N = label``,
    ``N = label, output label`` or ``N = output label``. A legend without the number is a body."""
    text = _squeezed(legend)
    numbered = _LEGEND_LINE.fullmatch(text)
    body = (numbered.group("body") or "") if numbered else text
    marker = _OUTPUT_WORD.search(body)
    if marker is None:
        return body, ""
    before = body[: marker.start()].rstrip(" ")
    if before.endswith(",") and before != ",":
        before = before[:-1].rstrip(" ")
    return before, body[marker.end() :].strip(" ")


def _without_span(part: str) -> tuple[str, bool]:
    """(``part`` without the " (one label for wires 1 and 2)" that docingest adds, whether it
    had one)."""
    match = _SPAN.search(part)
    return (part[: match.start()], True) if match else (part, False)


def _read_input(part: str) -> tuple[bool, str | None]:
    """(whether the input part is read, the state of its ket when it is one). Read: empty, a
    name (a symbol) or a basis ket that stands for one wire."""
    if not part:
        return True, None
    core, spanned = _without_span(part)
    core = _unwrapped(core.strip(" "))
    bit = _basis_ket(core)
    if bit is not None:
        return not spanned, bit
    return _SYMBOL.fullmatch(core) is not None, None


def _read_output(part: str) -> bool:
    """Whether the output part is read: empty, or a name (a symbol). Not a word, not a ket, not a
    number."""
    if not part:
        return True
    core, _ = _without_span(part)
    return _is_output_name(core)


def _misplaced(legend: str, number: int) -> bool:
    """Whether a legend that starts with a wire number gives another number than its place."""
    numbered = _LEGEND_LINE.fullmatch(_squeezed(legend))
    return numbered is not None and numbered.group("number") != str(number)


def _legend_notes(
    legends: object, n_wires: int, notes: _Reasons, kets: list[tuple[int, str]]
) -> tuple[tuple[str, ...], list[_Bad]]:
    """(legends as text, causes to refuse); a ket |1> adds a note and every ket is recorded."""
    if legends is None:
        return (), []
    if not isinstance(legends, list) or not all(isinstance(t, str) for t in legends):
        return (), [_Bad("must be a list of text", "wire_labels")]
    causes: list[_Bad] = []
    if len(legends) != n_wires:  # which legend is on which wire is unknown: none is read
        return (), [_Bad(f"{len(legends)} legends for {short_text(n_wires)} wires", "wire_labels")]
    for number, legend in enumerate(legends, start=1):
        place, shown = f"wire {number}", legend[:60]
        if _too_long(legend):  # before any pattern runs
            causes.append(
                _Bad(f"a legend of {len(legend):,} characters (at most {MAX_LABEL_CHARS})", place)
            )
        elif _WIRE_FACTS.search(legend):
            causes.append(
                _Bad(
                    f"the legend {shown!r} shows a bundle, a register, a row of dots or "
                    "a classical wire",
                    place,
                )
            )
        elif _misplaced(legend, number):
            causes.append(
                _Bad(
                    f"the legend {shown!r} is numbered for another wire; which legend is on "
                    "which wire is unknown",
                    place,
                )
            )
        else:
            before, after = _split_legend(legend)
            read, bit = _read_input(before)
            if not read:
                causes.append(_Bad(f"input legend not read: {shown!r}; {_INPUT_RULE}", place))
            if not _read_output(after):
                causes.append(_Bad(f"output legend not read: {shown!r}; {_OUTPUT_RULE}", place))
            if bit is not None and read:
                kets.append((number, bit))
            if bit == "1" and read:
                notes.add(
                    place,
                    f"the legend {shown!r} is not read as an input state; "
                    "the input is the one given, |0...0> by default",
                )
    return tuple(legends), causes


def _check_shape(data: object) -> tuple[int, list[Any]]:
    if not isinstance(data, dict):
        raise _Bad("a circuit document must be an object")
    if data.get("format") != CIRCUIT_FORMAT:
        raise _Bad(f"expected {CIRCUIT_FORMAT!r}, got {str(data.get('format'))[:40]!r}", "format")
    status = data.get("status", "parsed")  # first: an unparsed circuit has no wires and no parts
    if status != "parsed":
        raise _Bad(f"the circuit was read as {str(status)[:20]!r}, not 'parsed'", "status")
    _check_keys(data, _DOCUMENT_KEYS, "a circuit/1 document")
    n_wires, parts = data.get("wires"), data.get("parts")
    if not _is_int(n_wires) or n_wires < 1:
        raise _Bad("must be an integer of at least 1", "wires")
    if not isinstance(parts, list) or not parts:
        raise _Bad("must be a non-empty list of parts", "parts")
    total = 0
    for part in parts:
        if not isinstance(part, list) or not all(isinstance(step, list) for step in part):
            raise _Bad("each part must be a list of steps, each step a list of items", "parts")
        total += sum(len(step) + 1 for step in part)
    if total > MAX_ITEMS:
        raise _Bad(f"more than {MAX_ITEMS:,} items", "parts")
    return n_wires, parts


def _opening_sign(part: list[Any], index: int) -> str:
    first = part[0] if part else None
    sign = first[0] if isinstance(first, list) and len(first) == 1 else None
    label = sign.get("label") if isinstance(sign, dict) and sign.get("kind") == "sep" else None
    if not isinstance(label, str):
        raise _Bad("does not start with the drawn sign that opens it", f"part {index + 1}")
    _check_keys(sign, _ITEM_KEYS, "an item", f"part {index + 1}")  # type: ignore[arg-type]
    return label


def _claim(claimed: dict[int, int], ops: list[ParametricOp], item_number: int) -> None:
    """Record the wires the operations of one item use in its step; ``_Bad`` if an earlier item
    of the step uses one of them. A step is a column of the drawing: two items on one wire in
    it have no order, and the list position must not be taken for one."""
    used = {w for op in ops for w in (*op.wires, *op.controls, *op.open_controls)}
    for wire in sorted(used):
        if wire in claimed:
            raise _Bad(
                f"wire {wire} is also used by item {claimed[wire]} of this step; "
                "the drawing does not say which comes first"
            )
    claimed.update(dict.fromkeys(used, item_number))


@dataclass(frozen=True)
class _Context:
    """What reading one document shares between its parts: its size and notation, and where the
    refusals and the notes go."""

    n_wires: int
    notation: str
    reasons: _Reasons
    notes: _Reasons
    kets: list[tuple[int, str]]


def _note_ket(
    item: _Item, place: str, used: set[int], notes: _Reasons, kets: list[tuple[int, str]]
) -> None:
    """A text item that is a ket. On a wire an operation uses, in an earlier step or in the same
    step (a step has no order), it is refused: it may be a reset or a postselection, neither is
    simulated. With no wire it is refused too: it is not an input and not a mark on a wire.
    Otherwise a ket |1> gets a note. ``used`` holds the wires of the operations of the circuit
    so far, this step included."""
    bit = _basis_ket(item.label) if item.kind == "text" else None
    if bit is None:
        return
    text = item.label[:60]
    if not item.targets:
        raise _Bad(f"the text {text!r} is a ket on no wire: it is not an input and not a mark")
    if used.intersection(item.targets):
        raise _Bad(
            f"the text {text!r} is a ket after a gate on its wire or in the column of one: it "
            "may be a reset or a postselection, which are not simulated"
        )
    kets.extend((wire, bit) for wire in item.targets)
    if bit == "1":
        notes.add(
            place,
            f"the text {text!r} is a ket, not read as an input state; "
            "the input is the one given, |0...0> by default",
        )


def _read_part(
    part: list[Any], index: int, shared: _Context
) -> tuple[list[ParametricOp], str | None]:
    reasons, notes = shared.reasons, shared.notes
    sign: str | None = None
    steps = part
    if index > 0:
        try:
            sign = _opening_sign(part, index)
        except _Bad as bad:
            reasons.add(bad.place, bad.text)
            return [], None
        steps = part[1:]
    ops: list[ParametricOp] = []
    touched: set[int] = set()  # the wires an operation has used in the steps so far
    for step_number, step in enumerate(steps, start=1):
        claimed: dict[int, int] = {}
        texts: list[tuple[int, _Item]] = []
        for item_number, raw in enumerate(step, start=1):
            if reasons.full:
                reasons.cut = True
                return [], sign
            try:
                read, item_ops = _item_ops(raw, shared.n_wires, shared.notation)
                _claim(claimed, item_ops, item_number)
                ops.extend(item_ops)
                if read.kind == "text":
                    texts.append((item_number, read))
            except _Bad as bad:
                reasons.add(f"part {index + 1}, step {step_number}, item {item_number}", bad.text)
            if len(ops) > MAX_OPS:
                reasons.add(f"part {index + 1}", f"more than {MAX_OPS:,} operations")
                return [], sign
        touched.update(claimed)
        for item_number, read in texts:  # once the whole column is read: it has no order
            place = f"part {index + 1}, step {step_number}, item {item_number}"
            try:
                _note_ket(read, place, touched, notes, shared.kets)
            except _Bad as bad:
                reasons.add(place, bad.text)
    return ops, sign


def _validate_part(circuit: ParametricCircuit, index: int, reasons: _Reasons) -> None:
    """Run the engine's structural checks with a placeholder for every symbol."""
    placeholder = Circuit(
        circuit.n_wires,
        tuple(
            Op(
                op.gate,
                op.wires,
                op.controls,
                op.open_controls,
                tuple(a.value if a.is_numeric else 0.0 for a in op.params),
            )
            for op in circuit.ops
        ),
    )
    try:
        validate(placeholder)
    except QuantumSimError as error:
        reasons.add(f"part {index + 1}", str(error))


def _notation_of(kind: object) -> str:
    """The document's ``kind`` in lower case without spaces, hyphens and underscores
    (``"TikZ-cd"`` is ``tikzcd``)."""
    if not isinstance(kind, str):
        return ""
    return re.sub(r"[\s_-]", "", kind).lower()


def from_gate_list(data: object) -> Reading:
    """Read a ``circuit/1`` document (``CircuitSteps.to_dict()`` or a hand-written one).

    Returns a ``Reading`` with one ``ParametricCircuit`` per part, or raises ``RefusedError``
    whose ``reasons`` list every cause. A circuit with symbols (``\\theta``) is returned as such:
    ``bind`` it with values to simulate it.
    """
    reasons = _Reasons()
    try:
        n_wires, parts = _check_shape(data)
    except _Bad as bad:
        reasons.add(bad.place, bad.text)
        raise reasons.error() from None
    document: dict[str, Any] = data  # type: ignore[assignment]  # _check_shape saw a dict
    notes = _Reasons()
    kets: list[tuple[int, str]] = []
    legends, causes = _legend_notes(document.get("wire_labels"), n_wires, notes, kets)
    for cause in causes:
        reasons.add(cause.place, cause.text)
    label = document.get("label", "")
    if not isinstance(label, str):
        reasons.add("label", "the document label must be text")
        label = ""
    if _squeezed(label):
        notes.add(
            "label",
            f"the document label {label[:60]!r} is not read; if it names a projection, the "
            "result is not postselected",
        )
    kind = document.get("kind", "")
    if not isinstance(kind, str):
        reasons.add("kind", "the document kind must be text")
    notation = _notation_of(kind)
    shared = _Context(n_wires, notation, reasons, notes, kets)
    circuits: list[ParametricCircuit] = []
    signs: list[str] = []
    for index, part in enumerate(parts):
        if reasons.full:
            reasons.cut = True
            break
        ops, sign = _read_part(part, index, shared)
        if sign is not None:
            signs.append(sign)
        circuit = ParametricCircuit(n_wires, tuple(ops), legends, label)
        if not reasons:
            _validate_part(circuit, index, reasons)
        circuits.append(circuit)
    if reasons:
        raise reasons.error()
    return Reading(tuple(circuits), tuple(signs), tuple(notes.listed()), tuple(kets))


# --- Writer ------------------------------------------------------------------------------------


def _item(
    kind: str,
    label: str,
    targets: Sequence[int],
    controls: Sequence[int] = (),
    open_controls: Sequence[int] = (),
) -> dict[str, object]:
    return {
        "kind": kind,
        "label": label,
        "targets": list(targets),
        "controls": list(controls),
        "open_controls": list(open_controls),
        "classical": False,
        "source": "",
    }


def to_gate_list(circuit: Circuit) -> dict[str, object]:
    """``circuit`` as a ``circuit/1`` document, one operation per step.

    ``from_gate_list(to_gate_list(c))`` binds back to ``c`` exactly. Raises what ``validate``
    raises for a circuit that is not valid.
    """
    validate(circuit)
    steps: list[list[dict[str, object]]] = []
    for op in circuit.ops:
        if op.is_measurement:
            steps.append([_item("measure", "", op.wires)])
        elif op.gate == "x":
            steps.append([_item("x", "X", op.wires, op.controls, op.open_controls)])
        elif op.gate == "swap":
            steps.append([_item("swap", "", op.wires, op.controls, op.open_controls)])
        else:
            label = label_of(op.gate, op.params)
            steps.append([_item("gate", label, op.wires, op.controls, op.open_controls)])
    return {
        "format": CIRCUIT_FORMAT,
        "kind": "quantum-sim",
        "status": "parsed",
        "label": "",
        "wires": circuit.n_wires,
        "wire_labels": [str(w) for w in range(1, circuit.n_wires + 1)],
        "parts": [steps],
    }
