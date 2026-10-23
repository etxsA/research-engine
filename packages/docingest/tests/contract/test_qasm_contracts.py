"""OpenQASM 3 export contracts: golden programs parse, the emitter is pure, every kind is read.

The golden fixtures are ``circuit/1`` documents and the exact program the emitter must write for
them. The parser is a development dependency: it checks the syntax of every golden program and of
the programs a seeded property test generates, so a gate spelling or a modifier that QASM does not
accept fails here even when the string tests still pass.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from openqasm3 import parser

from docingest.domain import qasm as qasm_module
from docingest.domain.circuits import CircuitSteps, GateOp
from docingest.domain.qasm import to_qasm

FIXTURES = Path(__file__).parents[1] / "fixtures" / "qasm"


def circuit_from_dict(data: dict) -> CircuitSteps:
    """Build the IR from a ``circuit/1`` document, the format of ``CircuitSteps.to_dict``."""
    assert data["format"] == "circuit/1"
    return CircuitSteps(
        kind=data["kind"],
        status=data["status"],
        label=data["label"],
        wires=tuple(data["wire_labels"]),
        parts=tuple(
            tuple(
                tuple(
                    GateOp(
                        kind=item["kind"],
                        label=item["label"],
                        targets=tuple(item["targets"]),
                        controls=tuple(item["controls"]),
                        open_controls=tuple(item["open_controls"]),
                        classical=item["classical"],
                        source=item["source"],
                    )
                    for item in step
                )
                for step in part
            )
            for part in data["parts"]
        ),
    )


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_golden_programs_match_and_parse(fixture: Path) -> None:
    circuit = circuit_from_dict(json.loads(fixture.read_text()))
    export = to_qasm(circuit, origin=f"{fixture.stem}.tex, block 1, circuit 1")
    expected = sorted(FIXTURES.glob(f"{fixture.stem}.qasm")) + sorted(
        FIXTURES.glob(f"{fixture.stem}.*.qasm")
    )
    assert export.status == "ok", export.reasons
    assert len(expected) == len(export.programs)
    for program, path in zip(export.programs, expected, strict=True):
        assert program.text == path.read_text()
        assert program.text.endswith("\n") and "\r" not in program.text
        parser.parse(program.text)


def test_the_emitter_imports_only_the_standard_library() -> None:
    """The domain layer stays pure: no I/O and no package outside the standard library."""
    tree = ast.parse(Path(qasm_module.__file__).read_text())
    allowed = {"__future__", "re", "collections", "dataclasses"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert {alias.name.split(".")[0] for alias in node.names} <= allowed
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            assert node.module is not None and node.module.split(".")[0] in allowed
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open", "exec", "eval", "compile"}


def test_every_known_kind_is_handled_or_refused() -> None:
    kinds = {
        "gate": (GateOp("gate", "H", (1,), (), (), False, ""), True),
        "x": (GateOp("x", "X", (1,), (), (), False, ""), True),
        "z_dots": (GateOp("z_dots", "Z", (), (1, 2), (), False, ""), True),
        "phase": (GateOp("phase", "\\pi", (1,), (), (), False, ""), True),
        "dots_gate": (GateOp("dots_gate", "SWAP", (1, 2), (), (), False, ""), True),
        "swap": (GateOp("swap", "SWAP", (1, 2), (), (), False, ""), True),
        "measure": (GateOp("measure", "", (1,), (), (), False, ""), True),
        "barrier": (GateOp("barrier", "", (), (), (), False, ""), True),
        "text": (GateOp("text", "\\ket{0}", (1,), (), (), False, ""), True),
        "sign": (GateOp("sign", "", (1,), (), (), False, ""), True),
        "control": (GateOp("control", "", (), (1,), (), False, ""), False),
        "discard": (GateOp("discard", "", (1,), (), (), False, ""), False),
        "dots": (GateOp("dots", "", (1,), (), (), False, ""), False),
        "sep": (GateOp("sep", "=", (), (), (), False, ""), False),
        "unparsed": (GateOp("unparsed", "", (1,), (), (), False, "\\foo"), False),
        "unparsed_op": (GateOp("unparsed_op", "", (1,), (), (), False, "\\foo"), False),
        "unparsed_group": (GateOp("unparsed_group", "", (1,), (), (), False, "\\foo"), False),
        "unparsed_outside": (GateOp("unparsed_outside", "", (), (), (), False, "\\foo"), False),
    }
    for kind, (item, ok) in kinds.items():
        export = to_qasm(CircuitSteps("quantikz", "parsed", "", ("1", "2"), (((item,),),)))
        assert export.ok is ok, (kind, export.reasons)
        assert (export.status == "ok") is ok, kind


def test_the_same_circuit_always_gives_the_same_program() -> None:
    circuit = circuit_from_dict(json.loads((FIXTURES / "parts.json").read_text()))
    first = to_qasm(circuit, origin="same")
    second = to_qasm(circuit, origin="same")
    assert first == second


OPS = [
    ("gate", "H", (1,), (), ()),
    ("gate", "X", (2,), (), ()),
    ("gate", "R_y(\\pi/2)", (1,), (), ()),
    ("gate", "R_{zz}(0.5)", (1, 2), (), ()),
    ("z_dots", "Z", (), (1, 2), ()),
    ("x", "X", (2,), (1,), ()),
    ("swap", "SWAP", (1, 2), (), ()),
    ("gate", "S", (2,), (), ()),
    ("gate", "X", (2,), (), (1,)),
]


@settings(max_examples=40, deadline=None)
@given(st.lists(st.sampled_from(OPS), min_size=1, max_size=6))
def test_random_circuits_emit_parsable_qasm(ops: list[tuple]) -> None:
    part = tuple(
        (
            GateOp(
                kind=kind,
                label=label,
                targets=targets,
                controls=controls,
                open_controls=open_controls,
                classical=False,
                source="",
            ),
        )
        for kind, label, targets, controls, open_controls in ops
    )
    export = to_qasm(CircuitSteps("quantikz", "parsed", "", ("1", "2"), (part,)))
    assert export.status == "ok", export.reasons
    for program in export.programs:
        parser.parse(program.text)
