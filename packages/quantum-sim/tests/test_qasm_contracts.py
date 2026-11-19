"""OpenQASM 3 export contracts: golden programs parse, the emitter is pure, every kind is read.

The golden fixtures are ``circuit/1`` documents and the exact program the emitter must write for
them. The parser is a development dependency: it checks the syntax of every golden program and of
the programs a deterministic property test generates, so a gate spelling or a modifier that QASM does not
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

from quantum_sim import qasm as qasm_module
from quantum_sim.qasm import _STDGATES, to_qasm

FIXTURES = Path(__file__).parent / "fixtures" / "qasm"

# The fixture folders: ``NAME.json`` with ``NAME.qasm`` (one part) or ``NAME.1.qasm``,
# ``NAME.2.qasm``, ... (one per part), and ``refused/NAME.json`` with ``refused/NAME.reason``
# (one reason per line). The tests below fail when a folder is empty or a stem is lost.
GOLDEN_STEMS = {"bell", "measure", "open-control", "parts"}
REFUSED_STEMS = {"bundle", "legend-one", "output-ket"}

# The gate declarations of OpenQASM 3's examples/stdgates.inc. The emitter must not invent a name
# outside this set; ``sxdg`` is written as ``inv @ sx`` instead.
STDGATES = frozenset(
    {
        "gphase",
        "U",
        "u3",
        "u2",
        "u1",
        "p",
        "id",
        "x",
        "y",
        "z",
        "h",
        "s",
        "sdg",
        "t",
        "tdg",
        "sx",
        "rx",
        "ry",
        "rz",
        "cx",
        "cy",
        "cz",
        "cp",
        "crx",
        "cry",
        "crz",
        "ch",
        "cu",
        "swap",
        "ccx",
        "cswap",
    }
)


def document(*parts: list[list[dict]], wires: int = 1) -> dict:
    return {
        "format": "circuit/1",
        "kind": "quantikz",
        "status": "parsed",
        "label": "",
        "wires": wires,
        "wire_labels": [str(wire) for wire in range(1, wires + 1)],
        "parts": [list(part) for part in parts],
    }


def item(
    kind: str = "gate",
    label: str = "H",
    *,
    targets: tuple[int, ...] = (1,),
    controls: tuple[int, ...] = (),
    open_controls: tuple[int, ...] = (),
) -> dict:
    return {
        "kind": kind,
        "label": label,
        "targets": list(targets),
        "controls": list(controls),
        "open_controls": list(open_controls),
        "classical": False,
        "source": "",
    }


@pytest.mark.parametrize("fixture", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_golden_programs_match_and_parse(fixture: Path) -> None:
    export = to_qasm(
        json.loads(fixture.read_text()), origin=f"{fixture.stem}.tex, block 1, circuit 1"
    )
    expected = sorted(FIXTURES.glob(f"{fixture.stem}.qasm")) + sorted(
        FIXTURES.glob(f"{fixture.stem}.*.qasm")
    )
    assert export.status == "ok", export.reasons
    assert len(expected) == len(export.programs)
    for program, path in zip(export.programs, expected, strict=True):
        assert program.text == path.read_text()
        assert program.text.endswith("\n") and "\r" not in program.text
        parser.parse(program.text)


def test_the_fixture_folders_hold_every_expected_fixture() -> None:
    goldens = {path.stem for path in FIXTURES.glob("*.json")}
    assert goldens == GOLDEN_STEMS
    for stem in goldens:
        assert list(FIXTURES.glob(f"{stem}.qasm")) or list(FIXTURES.glob(f"{stem}.*.qasm")), stem
    refused = FIXTURES / "refused"
    assert {path.stem for path in refused.glob("*.json")} == REFUSED_STEMS
    assert {path.stem for path in refused.glob("*.reason")} == REFUSED_STEMS


def test_refused_fixtures_match_their_reasons() -> None:
    refused = FIXTURES / "refused"
    fixtures = sorted(refused.glob("*.json"))
    assert {fixture.stem for fixture in fixtures} == REFUSED_STEMS
    for fixture in fixtures:
        export = to_qasm(json.loads(fixture.read_text()))
        expected = tuple(fixture.with_suffix(".reason").read_text().splitlines())
        assert export.status == "refused", fixture.stem
        assert export.reasons == expected, fixture.stem
        assert export.programs == (), fixture.stem


def test_every_emitted_gate_is_in_stdgates() -> None:
    assert _STDGATES <= STDGATES
    assert "sxdg" not in _STDGATES
    assert "rxx" not in _STDGATES
    assert "ryy" not in _STDGATES
    assert "rzz" not in _STDGATES


def test_the_emitter_imports_only_the_standard_library() -> None:
    """The format layer stays pure: no I/O and no package outside the standard library."""
    tree = ast.parse(Path(qasm_module.__file__).read_text())
    allowed = {"__future__", "re", "collections", "dataclasses", "typing", "quantum_sim"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert {alias.name.split(".")[0] for alias in node.names} <= allowed
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            assert node.module is not None and node.module.split(".")[0] in allowed
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"open", "exec", "eval", "compile"}


def test_every_known_kind_is_handled_or_refused() -> None:
    kinds = {
        "gate": (item("gate", "H"), True),
        "x": (item("x", "X"), True),
        "z_dots": (item("z_dots", "Z", targets=(), controls=(1, 2)), True),
        "phase": (item("phase", "\\pi"), False),
        "dots_gate": (item("dots_gate", "SWAP", targets=(1, 2)), False),
        "swap": (item("swap", "SWAP", targets=(1, 2)), True),
        "measure": (item("measure", "", targets=(1,)), True),
        "barrier": (item("barrier", "", targets=()), True),
        "text": (item("text", "\\ket{0}"), True),
        "sign": (item("sign", "", targets=(1,)), False),
        "control": (item("control", "", targets=(), controls=(1,)), False),
        "discard": (item("discard", "", targets=(1,)), False),
        "dots": (item("dots", "", targets=(1,)), False),
        "sep": (item("sep", "=", targets=()), False),
        "unparsed": (item("unparsed", "", targets=(1,)), False),
        "unparsed_op": (item("unparsed_op", "", targets=(1,)), False),
        "unparsed_group": (item("unparsed_group", "", targets=(1,)), False),
        "unparsed_outside": (item("unparsed_outside", "", targets=()), False),
    }
    for kind, (entry, ok) in kinds.items():
        export = to_qasm(document([[entry]], wires=2))
        assert export.ok is ok, (kind, export.reasons)
        assert (export.status == "ok") is ok, kind


def test_the_same_circuit_always_gives_the_same_program() -> None:
    data = json.loads((FIXTURES / "parts.json").read_text())
    assert to_qasm(data, origin="same") == to_qasm(data, origin="same")


OPS = [
    ("gate", "H", (1,), (), ()),
    ("gate", "X", (2,), (), ()),
    ("gate", "R_y(\\pi/2)", (1,), (), ()),
    ("gate", "R_z(0.5)", (1,), (), ()),
    ("gate", "\\sqrt{X}^\\dagger", (2,), (), ()),
    ("z_dots", "Z", (), (1, 2), ()),
    ("x", "X", (2,), (1,), ()),
    ("swap", "SWAP", (1, 2), (), ()),
    ("gate", "S", (2,), (), ()),
    ("gate", "X", (2,), (), (1,)),
]


@settings(max_examples=40, deadline=None, derandomize=True)
@given(st.lists(st.sampled_from(OPS), min_size=1, max_size=6))
def test_random_circuits_emit_parsable_qasm(ops: list[tuple]) -> None:
    part = [
        {
            "kind": kind,
            "label": label,
            "targets": list(targets),
            "controls": list(controls),
            "open_controls": list(open_controls),
            "classical": False,
            "source": "",
        }
        for kind, label, targets, controls, open_controls in ops
    ]
    export = to_qasm(document([[entry] for entry in part], wires=2))
    assert export.status == "ok", export.reasons
    for program in export.programs:
        parser.parse(program.text)
