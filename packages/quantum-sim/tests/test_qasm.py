"""The OpenQASM 3 export: one circuit part at a time, refusals as data."""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quantum_sim import run
from quantum_sim.qasm import QasmExport, to_qasm, validate_bindings


def op(
    kind: str = "gate",
    label: str = "H",
    *,
    targets: tuple[int, ...] = (1,),
    controls: tuple[int, ...] = (),
    open_controls: tuple[int, ...] = (),
    classical: bool = False,
    source: str = "",
) -> dict:
    return {
        "kind": kind,
        "label": label,
        "targets": list(targets),
        "controls": list(controls),
        "open_controls": list(open_controls),
        "classical": classical,
        "source": source,
    }


def step(*items: dict) -> list[dict]:
    return list(items)


def circuit(
    *parts: tuple[tuple[dict, ...], ...],
    wires: int = 1,
    status: str = "parsed",
    legends: tuple[str, ...] | None = None,
) -> dict:
    if legends is None:
        legends = tuple(str(wire) for wire in range(1, wires + 1))
    return {
        "format": "circuit/1",
        "kind": "quantikz",
        "status": status,
        "label": "",
        "wires": wires,
        "wire_labels": list(legends),
        "parts": [[list(s) for s in part] for part in parts],
    }


def body(export: QasmExport, part: int = 0) -> list[str]:
    lines = export.programs[part].text.splitlines()
    return lines[lines.index("") + 1 :]


def header(export: QasmExport, part: int = 0) -> list[str]:
    lines = export.programs[part].text.splitlines()
    return lines[: lines.index("")]


# ------------------------------------------------------------------ gates and wires


def test_one_qubit_gate() -> None:
    export = to_qasm(circuit((step(op()),)))
    assert export.status == "ok"
    assert export.ok
    assert export.reasons == ()
    assert body(export) == ["h q[0];"]
    assert export.programs[0].n_qubits == 1
    assert export.programs[0].n_clbits == 0


def test_wire_numbers_are_one_based_top_to_bottom() -> None:
    export = to_qasm(circuit((step(op(label="X", targets=(2,))),), wires=2))
    assert body(export) == ["x q[1];"]


def test_bell_pair() -> None:
    circuit_steps = (
        step(op()),
        step(op(kind="x", label="X", targets=(2,), controls=(1,))),
    )
    export = to_qasm(circuit(circuit_steps, wires=2))
    assert body(export) == ["h q[0];", "cx q[0], q[1];"]


def test_toffoli_uses_ccx() -> None:
    item = op(kind="x", label="X", targets=(3,), controls=(1, 2))
    export = to_qasm(circuit((step(item),), wires=3))
    assert body(export) == ["ccx q[0], q[1], q[2];"]


def test_more_than_two_controls_chains() -> None:
    item = op(kind="x", label="X", targets=(4,), controls=(1, 2, 3))
    export = to_qasm(circuit((step(item),), wires=4))
    assert body(export) == ["ctrl @ ctrl @ ctrl @ x q[0], q[1], q[2], q[3];"]


def test_open_control_is_conjugated_with_x() -> None:
    item = op(label="X", targets=(2,), open_controls=(1,))
    export = to_qasm(circuit((step(item),), wires=2))
    assert body(export) == ["x q[0];", "cx q[0], q[1];", "x q[0];"]


def test_closed_and_open_controls_together() -> None:
    item = op(label="X", targets=(3,), controls=(1,), open_controls=(2,))
    export = to_qasm(circuit((step(item),), wires=3))
    assert body(export) == ["x q[1];", "ccx q[0], q[1], q[2];", "x q[1];"]


def test_z_dots_are_a_cz() -> None:
    item = op(kind="z_dots", label="Z", targets=(), controls=(1, 2))
    export = to_qasm(circuit((step(item),), wires=2))
    assert body(export) == ["cz q[0], q[1];"]


def test_three_z_dots_are_a_controlled_z() -> None:
    item = op(kind="z_dots", label="Z", targets=(), controls=(1, 2, 3))
    export = to_qasm(circuit((step(item),), wires=3))
    assert body(export) == ["ctrl @ ctrl @ z q[0], q[1], q[2];"]


def test_swap_and_controlled_swap() -> None:
    plain = op(kind="swap", label="SWAP", targets=(1, 2))
    controlled = op(kind="swap", label="SWAP", targets=(2, 3), controls=(1,))
    export = to_qasm(circuit((step(plain), step(controlled)), wires=3))
    assert body(export) == ["swap q[0], q[1];", "cswap q[0], q[1], q[2];"]


def test_one_qubit_box_over_several_wires_is_refused() -> None:
    export = to_qasm(circuit((step(op(label="H", targets=(1, 2))),), wires=2))
    assert export.status == "refused"
    assert export.programs == ()


def test_a_controlled_multi_target_gate_is_refused() -> None:
    item = op(label="H", targets=(1, 2), controls=(3,))
    export = to_qasm(circuit((step(item),), wires=3))
    assert export.status == "refused"
    assert export.programs == ()


# ------------------------------------------------------------------ parameters


def test_parameter_is_a_qasm_expression() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\pi/2)")),)))
    assert body(export) == ["ry(pi/2.0) q[0];"]


def test_frac_becomes_a_division() -> None:
    export = to_qasm(circuit((step(op(label="R_z(\\frac{\\pi}{4})")),)))
    assert body(export) == ["rz(pi/4.0) q[0];"]


def test_implicit_multiplication() -> None:
    export = to_qasm(circuit((step(op(label="R_x(2\\pi)")),)))
    assert body(export) == ["rx(2.0*pi) q[0];"]


def test_negative_angle() -> None:
    export = to_qasm(circuit((step(op(label="R_x(-\\pi/2)")),)))
    assert body(export) == ["rx((-pi)/2.0) q[0];"]


def test_sqrt_parameter() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\sqrt{2})")),)))
    assert body(export) == ["ry(sqrt(2.0)) q[0];"]


def test_unbound_symbol_is_refused() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\theta)")),)))
    assert export.status == "refused"
    assert export.programs == ()
    assert "theta" in export.reasons[0]
    assert "part 1" in export.reasons[0]


def test_a_bound_symbol_is_substituted() -> None:
    export = to_qasm(
        circuit((step(op(label="R_y(\\theta)")),)),
        values={"theta": "pi/4"},
    )
    assert body(export) == ["ry(pi/4.0) q[0];"]


def test_a_binding_key_may_keep_its_backslash() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\theta)")),)), values={"\\theta": "0.5"})
    assert body(export) == ["ry(0.5) q[0];"]


def test_a_binding_key_may_be_braced() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\theta)")),)), values={"{ \\theta }": "0.5"})
    assert body(export) == ["ry(0.5) q[0];"]


@pytest.mark.parametrize("key", ["theta", "\\theta", "{\\theta}"])
def test_the_export_and_the_simulator_take_the_same_binding_names(key: str) -> None:
    document = circuit((step(op(label="R_y(\\theta)")),))
    export = to_qasm(document, values={key: "0.5"})
    result = run(document, values={key: "0.5"})
    assert export.status == "ok", export.reasons
    assert export.symbols == ("theta",) and export.unmatched == ()
    assert result["status"] == "ok", result["refused"]
    assert result["values"] == {"theta": 0.5}


@pytest.mark.parametrize("value", ["pi/4", "\\pi/4", "\\frac{pi}{4}", "pie/4", "2 pie"])
def test_the_export_and_the_simulator_read_a_value_by_the_same_rule(value: str) -> None:
    document = circuit((step(op(label="R_y(\\theta)")),))
    export = to_qasm(document, values={"theta": value})
    result = run(document, values={"theta": value})
    assert (export.status == "ok") is (result["status"] == "ok"), (export.reasons, result)
    assert export.ok is ("pie" not in value)
    if export.ok:
        assert body(export) == ["ry(pi/4.0) q[0];"]
        assert result["values"] == {"theta": 0.7853981633974483}


def test_the_export_and_the_simulator_refuse_the_same_binding_name() -> None:
    document = circuit((step(op(label="R_y(\\theta)")),))
    export = to_qasm(document, values={"the{ta}": "0.5"})
    result = run(document, values={"the{ta}": "0.5"})
    assert export.status == "refused"
    assert "'the{ta}' is not a parameter name" in export.reasons[0]
    assert result["status"] == "refused"
    assert "'the{ta}' is not a parameter name" in result["refused"][0]


@pytest.mark.parametrize("key", ["thetta", "pi", "\\pi", "e", "x y", "\\\\theta", "$\\theta$"])
def test_a_binding_key_that_is_not_one_symbol_is_refused(key: str) -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\theta)")),)), values={"theta": "1", key: "1"})
    assert export.status == "refused" and export.programs == ()
    assert export.reasons == (
        f"{key!r} is not a parameter name: write one symbol, such as theta, \\theta or "
        "\\theta_{1}",
    )
    with pytest.raises(ValueError, match="is not a parameter name"):
        validate_bindings({key: "1"})


def test_a_bound_symbol_scales() -> None:
    export = to_qasm(circuit((step(op(label="R_y(2\\theta)")),)), values={"theta": "pi/4"})
    assert body(export) == ["ry(2.0*(pi/4.0)) q[0];"]


def test_a_bad_binding_is_refused() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\theta)")),)), values={"theta": "\\cos(1)"})
    assert export.status == "refused"
    assert "value for 'theta'" in export.reasons[0]


def test_exponentiation_is_a_power() -> None:
    export = to_qasm(circuit((step(op(label="R_y(2^3)")),)))
    assert body(export) == ["ry(2.0**3.0) q[0];"]


def test_a_negative_exponent_is_a_float_literal() -> None:
    export = to_qasm(circuit((step(op(label="R_z(\\pi 2^{-1})")),)))
    assert body(export) == ["rz(pi*(2.0**(-1.0))) q[0];"]


def test_a_fraction_of_pi_keeps_its_value() -> None:
    export = to_qasm(circuit((step(op(label="R_z(\\frac{1}{2}\\pi)")),)))
    assert body(export) == ["rz((1.0/2.0)*pi) q[0];"]


INTEGER = re.compile(r"(?<![\w.])\d+(?![\d.])")


@settings(max_examples=40, deadline=None, derandomize=True)
@given(st.integers(min_value=0, max_value=100), st.integers(min_value=1, max_value=100))
def test_no_emitted_parameter_contains_an_integer_literal(numerator: int, denominator: int) -> None:
    label = f"R_z({numerator}/{denominator})"
    export = to_qasm(circuit((step(op(label=label)),)))
    line = body(export)[0]
    parameter = line[line.index("(") + 1 : line.rindex(")")]
    assert not INTEGER.search(parameter), line


def test_a_bound_value_is_in_the_header() -> None:
    export = to_qasm(
        circuit((step(op(label="R_y(\\theta)")),)),
        values={"theta": "pi/4"},
        tool="docingest",
    )
    assert export.programs[0].text.startswith("// Generated by docingest (qasm format 1)\n")
    assert "// bound: theta = pi/4" in header(export)
    assert export.symbols == ("theta",)


def test_unknown_symbol_is_refused() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\aleph)")),)))
    assert export.status == "refused"


# ------------------------------------------------------------------ measurements


def test_measurement_declares_bits() -> None:
    export = to_qasm(circuit((step(op(kind="measure", label="", targets=(1,))),)))
    assert export.programs[0].n_clbits == 1
    assert "bit[1] c;" in header(export)
    assert body(export) == ["measure q[0] -> c[0];"]


def test_measurement_keeps_the_wire_number() -> None:
    item = op(kind="measure", label="", targets=(3,))
    export = to_qasm(circuit((step(item),), wires=3))
    assert "bit[3] c;" in header(export)
    assert body(export) == ["measure q[2] -> c[2];"]


def test_a_gate_after_its_measurement_is_refused() -> None:
    steps = (
        step(op(kind="measure", label="", targets=(1,))),
        step(op(label="H", targets=(1,))),
    )
    export = to_qasm(circuit(steps))
    assert export.status == "refused"
    assert "after its measurement" in export.reasons[0]


def test_a_barrier_after_a_measurement_is_allowed_and_does_not_emit() -> None:
    steps = (
        step(op(kind="measure", label="", targets=(1,))),
        step(op(kind="barrier", label="", targets=(1,))),
    )
    export = to_qasm(circuit(steps))
    assert export.status == "ok"
    assert body(export) == ["measure q[0] -> c[0];"]


def test_measuring_a_wire_twice_is_refused() -> None:
    steps = (
        step(op(kind="measure", label="", targets=(1,))),
        step(op(kind="measure", label="", targets=(1,))),
    )
    export = to_qasm(circuit(steps))
    assert export.status == "refused"
    assert "after its measurement" in export.reasons[0]


def test_a_controlled_measurement_is_refused() -> None:
    item = op(kind="measure", label="", targets=(2,), controls=(1,))
    export = to_qasm(circuit((step(item),), wires=2))
    assert export.status == "refused"


# ------------------------------------------------------------------ neutral marks


def test_barrier_on_wires_and_on_the_register_does_not_emit() -> None:
    steps = (
        step(op(kind="barrier", label="", targets=(1, 2))),
        step(op(kind="barrier", label="", targets=())),
    )
    export = to_qasm(circuit(steps, wires=2))
    assert export.status == "ok"
    assert body(export) == []


def test_text_zero_is_the_default_and_is_skipped() -> None:
    item = op(kind="text", label="\\ket{0}", targets=(1,))
    export = to_qasm(circuit((step(item),)))
    assert export.status == "ok"
    assert body(export) == []


def test_a_text_one_is_refused() -> None:
    item = op(kind="text", label="\\ket{1}", targets=(1,))
    export = to_qasm(circuit((step(item),)))
    assert export.status == "refused"
    assert export.reasons == ("the drawing starts an input in |1>, which the export does not add",)
    assert export.programs == ()


def test_a_text_one_written_with_a_bar_is_refused() -> None:
    item = op(kind="text", label="|1\\rangle", targets=(1,))
    export = to_qasm(circuit((step(item),)))
    assert export.status == "refused"
    assert export.reasons == ("the drawing starts an input in |1>, which the export does not add",)
    assert export.programs == ()


def test_a_wire_legend_one_is_refused() -> None:
    export = to_qasm(circuit((step(op()),), legends=("1 = \\ket{1}",)))
    assert export.status == "refused"
    assert export.reasons == ("the drawing starts an input in |1>, which the export does not add",)
    assert export.programs == ()


def test_a_text_zero_input_stays_ok() -> None:
    item = op(kind="text", label="\\ket{0}", targets=(1,))
    export = to_qasm(circuit((step(item), step(op()))))
    assert export.status == "ok"
    assert body(export) == ["h q[0];"]


def test_a_sign_drawn_on_a_wire_is_refused() -> None:
    empty = to_qasm(circuit((step(op(kind="sign", label="", targets=(1,))),)))
    assert empty.status == "refused"
    labelled = to_qasm(circuit((step(op(kind="sign", label="-", targets=(1,))),)))
    assert labelled.status == "refused"


# ------------------------------------------------------------------ parts


def test_a_drawn_equality_makes_two_programs() -> None:
    left = (step(op(label="H")), step(op(label="Z")), step(op(label="H")))
    right = (step(op(kind="sep", label="=")), step(op(label="X")))
    export = to_qasm(circuit(left, right))
    assert export.status == "ok"
    assert len(export.programs) == 2
    assert export.programs[0].sign is None
    assert export.programs[1].sign == "="
    assert "// sign: =" in export.programs[1].text.splitlines()
    assert "// sign:" not in export.programs[0].text
    assert body(export, 0) == ["h q[0];", "z q[0];", "h q[0];"]
    assert body(export, 1) == ["x q[0];"]


def test_an_empty_part_is_a_valid_program() -> None:
    export = to_qasm(
        circuit(
            (),
        )
    )
    assert export.status == "ok"
    assert body(export) == []
    assert "OPENQASM 3.0;" in header(export)


def test_a_sep_must_open_a_following_part() -> None:
    export = to_qasm(circuit((step(op(kind="sep", label="=")), step(op(label="H")))))
    assert export.status == "refused"


def test_a_sep_inside_a_part_is_refused() -> None:
    steps = (step(op(label="H")), step(op(kind="sep", label="=")))
    export = to_qasm(circuit(steps))
    assert export.status == "refused"
    assert "equality" in export.reasons[0]


def test_a_part_that_fails_refuses_the_document() -> None:
    bad = (step(op(label="H")), step(op(kind="unparsed_op", label="")))
    good = (step(op(kind="sep", label="=")), step(op(label="X")))
    export = to_qasm(circuit(bad, good))
    assert export.status == "refused"
    assert export.programs == ()
    assert "part 1" in export.reasons[0]


# ------------------------------------------------------------------ refusals


def test_a_partial_reading_is_refused() -> None:
    export = to_qasm(circuit((step(op()),), status="partial"))
    assert export.status == "refused"
    assert "partial" in export.reasons[0]


def test_an_unsupported_gate_refuses_the_whole_drawing() -> None:
    first = (step(op(label="H")),)
    second = (
        step(op(kind="sep", label="=")),
        step(op(label="R_{zz}(0.5)", targets=(1, 2))),
    )
    export = to_qasm(circuit(first, second, wires=2))
    assert export.status == "refused"
    assert export.programs == ()
    assert "stdgates.inc has no rzz" in export.reasons[0]


def test_an_unparsed_item_is_refused() -> None:
    export = to_qasm(circuit((step(op(kind="unparsed", label="", source="\\foo")),)))
    assert export.status == "refused"
    assert "unparsed" in export.reasons[0]


def test_a_lone_control_is_refused() -> None:
    item = op(kind="control", label="", targets=(), controls=(1,))
    export = to_qasm(circuit((step(item),)))
    assert export.status == "refused"
    assert "control" in export.reasons[0]


def test_a_classically_controlled_item_is_refused() -> None:
    item = op(label="X", targets=(2,), controls=(1,), classical=True)
    export = to_qasm(circuit((step(item),), wires=2))
    assert export.status == "refused"
    assert "classically" in export.reasons[0]


def test_a_wire_named_twice_is_refused() -> None:
    item = op(label="X", targets=(1,), controls=(1,))
    export = to_qasm(circuit((step(item),)))
    assert export.status == "refused"
    assert "twice" in export.reasons[0]


def test_a_wire_outside_the_circuit_is_refused() -> None:
    export = to_qasm(circuit((step(op(label="X", targets=(2,))),)))
    assert export.status == "refused"
    assert "not in 1..1" in export.reasons[0]


def test_a_circuit_without_wires_is_refused() -> None:
    export = to_qasm(
        {
            "format": "circuit/1",
            "status": "parsed",
            "wires": 0,
            "parts": [],
        }
    )
    assert export.status == "refused"
    assert "wires" in export.reasons[0]


def test_a_circuit_without_parts_is_refused() -> None:
    export = to_qasm(
        {
            "format": "circuit/1",
            "status": "parsed",
            "wires": 1,
            "parts": [],
        }
    )
    assert export.status == "refused"
    assert "parts" in export.reasons[0]


# ------------------------------------------------------------------ labels


def test_dagger_labels() -> None:
    steps = (
        step(op(label="S^\\dagger")),
        step(op(label="T^{\\dagger}")),
        step(op(label="S^{-1}")),
    )
    export = to_qasm(circuit(steps))
    assert body(export) == ["sdg q[0];", "tdg q[0];", "sdg q[0];"]


def test_sqrt_x_labels() -> None:
    steps = (step(op(label="\\sqrt{X}")), step(op(label="\\sqrt{X}^\\dagger")))
    export = to_qasm(circuit(steps))
    assert body(export) == ["sx q[0];", "inv @ sx q[0];"]


def test_a_phase_item_is_refused() -> None:
    item = op(kind="phase", label="\\pi/2", targets=(1,))
    export = to_qasm(circuit((step(item),)))
    assert export.status == "refused"
    assert export.programs == ()


def test_joined_phase_labels_are_refused() -> None:
    item = op(kind="phase", label="\\alpha, \\beta", targets=(1,))
    export = to_qasm(circuit((step(item),)))
    assert export.status == "refused"


def test_a_labelled_dot_gate_is_refused() -> None:
    item = op(kind="dots_gate", label="SWAP", targets=(1, 2))
    export = to_qasm(circuit((step(item),), wires=2))
    assert export.status == "refused"


def test_ambiguous_box_labels_are_refused() -> None:
    export = to_qasm(circuit((step(op(label="CNOT", targets=(1, 2))),), wires=2))
    assert export.status == "refused"
    assert "ambiguous" in export.reasons[0]


@pytest.mark.parametrize("label", ["U", "iSWAP", "R_k(2)", "\\foo"])
def test_gates_without_an_exact_qasm_form_are_refused(label: str) -> None:
    export = to_qasm(circuit((step(op(label=label)),)))
    assert export.status == "refused"


# ------------------------------------------------------------------ format


def test_header_is_deterministic_and_names_the_wires() -> None:
    legends = ("1 = q_0", "2 = q_1")
    first = to_qasm(circuit((step(op()),), wires=2, legends=legends), origin="document.md block 3")
    second = to_qasm(circuit((step(op()),), wires=2, legends=legends), origin="document.md block 3")
    assert first.programs[0].text == second.programs[0].text
    lines = header(first)
    assert lines[0] == "// Generated by quantum-sim (qasm format 1)"
    assert lines[1] == "// source: document.md block 3, part 1/1"
    assert lines[2] == "// wire 1 (top) = q[0]; the simulator reads it as the most significant bit"
    assert lines[3] == "// wires: 1 = q_0; 2 = q_1"
    assert lines[4] == "OPENQASM 3.0;"
    assert lines[5] == 'include "stdgates.inc";'
    assert lines[6] == "qubit[2] q;"
    assert len(lines) == 7


def test_the_classical_register_lines_come_with_the_register() -> None:
    item = op(kind="measure", label="", targets=(1,))
    export = to_qasm(circuit((step(op()), step(item)), wires=2))
    lines = header(export)
    assert lines[1:4] == [
        "// wire 1 (top) = q[0]; the simulator reads it as the most significant bit",
        "// c[i] holds q[i]; c reads c[n-1] ... c[0], so wire 1 is its rightmost bit",
        "// bit[n] declares one classical bit per wire, not only the measured ones",
    ]
    assert "bit[2] c;" in lines


def test_a_newline_in_provenance_does_not_break_the_comment() -> None:
    export = to_qasm(circuit((step(op()),)), origin="line one\nline two")
    assert any(line.startswith("// source: line one line two") for line in header(export))


@pytest.mark.parametrize("separator", ["\r", "\v", "\f", "\x1c", "\x85", "\u2028", "\u2029"])
def test_no_line_break_of_any_kind_reaches_the_header(separator: str) -> None:
    export = to_qasm(
        circuit((step(op(label="R_y(\\theta)")),)),
        values={"theta": f"0.25{separator}+{separator}pi"},
        origin=f"one{separator}OPENQASM 2.0;",
    )
    assert export.status == "ok", export.reasons
    lines = export.programs[0].text.splitlines()
    assert lines[1] == "// source: one OPENQASM 2.0;, part 1/1"
    assert [line for line in lines if line.startswith("// bound:")] == [
        "// bound: theta = 0.25 + pi"
    ]
    start = lines.index("OPENQASM 3.0;")
    assert all(line.startswith("//") for line in lines[:start])


def test_a_binding_name_with_a_newline_is_refused_before_the_header() -> None:
    key = "x\nOPENQASM 2.0;\n//"
    export = to_qasm(circuit((step(op(label="R_y(\\theta)")),)), values={"theta": "0.5", key: "1"})
    assert export.status == "refused" and export.programs == ()
    assert export.unmatched == (key,)
    assert export.reasons[0].startswith(f"{key!r} is not a parameter name")
    assert "\n" not in export.reasons[0]
    with pytest.raises(ValueError, match="is not a parameter name"):
        validate_bindings({key: "1"})


# ------------------------------------------------------------------ smaller refusals


@pytest.mark.parametrize(
    ("item", "wires"),
    [
        (op(label="H", targets=()), 1),
        (op(label="SWAP", targets=(1,)), 2),
        (op(label="RZZ(\\pi)", targets=(1, 2, 3)), 3),
        (op(kind="z_dots", label="Z", targets=(), controls=(1,)), 2),
        (op(kind="z_dots", label="Z", targets=(), open_controls=(1, 2)), 2),
        (op(kind="phase", label="\\pi", targets=(1, 2)), 2),
        (op(kind="phase", label="", targets=(1,)), 1),
        (op(kind="dots_gate", label="H", targets=(1, 2)), 2),
        (op(kind="dots_gate", label="SWAP(1)", targets=(1, 2)), 2),
        (op(kind="dots_gate", label="SWAP", targets=(1,)), 1),
        (op(kind="swap", label="SWAP", targets=(1,)), 1),
        (op(kind="barrier", label="", targets=(1,), controls=(2,)), 2),
        (op(kind="text", label="hello", targets=(1,)), 1),
        (op(label="\\sqrt{Y}"), 1),
        (op(label="H(\\pi)"), 1),
        (op(label="RX"), 1),
        (op(label="(\\pi)"), 1),
        (op(label="H)"), 1),
    ],
)
def test_items_outside_the_contract_are_refused(item: dict, wires: int) -> None:
    export = to_qasm(circuit((step(item),), wires=wires))
    assert export.status == "refused"
    assert export.programs == ()


def test_two_drawn_equalities_in_one_part_are_refused() -> None:
    first = (step(op(label="H")),)
    second = (step(op(kind="sep", label="="), op(kind="sep", label="=")), step(op(label="X")))
    export = to_qasm(circuit(first, second))
    assert export.status == "refused"
    assert "does not start" in export.reasons[0]


def test_a_drawn_equality_sharing_its_step_is_refused() -> None:
    first = (step(op(label="H")),)
    second = (step(op(kind="sep", label="="), op(label="X")),)
    export = to_qasm(circuit(first, second))
    assert export.status == "refused"
    assert "does not start" in export.reasons[0]


def test_a_blank_wire_legend_is_omitted() -> None:
    export = to_qasm(circuit((step(op()),), legends=(" ",)))
    assert not any(line.startswith("// wires:") for line in header(export))


# ------------------------------------------------------------------ expression details


def test_addition_and_braces_and_pi() -> None:
    steps = (
        step(op(label="R_x(\\pi+1)")),
        step(op(label="R_y({\\pi})")),
        step(op(label="R_z(\\pi/2)")),
    )
    export = to_qasm(circuit(steps))
    assert body(export) == ["rx(pi+1.0) q[0];", "ry(pi) q[0];", "rz(pi/2.0) q[0];"]


def test_a_bare_greek_name_is_a_symbol() -> None:
    export = to_qasm(circuit((step(op(label="R_x(\\theta)")),)), values={"theta": "0.25"})
    assert body(export) == ["rx(0.25) q[0];"]


def test_decimal_forms_are_normalized() -> None:
    steps = (step(op(label="R_x(.5)")), step(op(label="R_y(5.)")))
    export = to_qasm(circuit(steps))
    assert body(export) == ["rx(0.5) q[0];", "ry(5.0) q[0];"]


def test_labels_may_be_dollar_wrapped_or_wrapped_in_text() -> None:
    steps = (step(op(label="$H$")), step(op(label="\\mathrm{H}")), step(op(label="H\\,")))
    export = to_qasm(circuit(steps))
    assert body(export) == ["h q[0];", "h q[0];", "h q[0];"]


@pytest.mark.parametrize(
    "label",
    [
        "R_x(2#3)",
        "R_x(*2)",
        "R_x(\\frac{\\pi}{2)",
        "R_x(\\sqrt{\\pi)",
        "R_x(\\sqrt{\\pi)+1)",
        "R_x((\\pi}{2)",
    ],
)
def test_a_malformed_expression_is_refused(label: str) -> None:
    export = to_qasm(circuit((step(op(label=label)),)))
    assert export.status == "refused"


def test_an_empty_binding_name_or_value_is_refused() -> None:
    empty_name = to_qasm(circuit((step(op()),)), values={" ": "1"})
    assert empty_name.status == "refused"
    empty_value = to_qasm(circuit((step(op(label="R_x(\\theta)")),)), values={"theta": ""})
    assert empty_value.status == "refused"
    assert "value for 'theta'" in empty_value.reasons[0]


# ------------------------------------------------------------------ bindings and their values


@pytest.mark.parametrize(
    ("label", "value", "reason"),
    [
        ("R_y(\\frac{\\pi}{\\theta})", "0", "division by zero"),
        ("R_y(\\sqrt{\\theta})", "-1", "the square root of a negative number"),
        ("R_y(\\theta^{-1})", "0", "a power overflows or divides by zero"),
        ("R_y(\\theta^{64})", "10000000000", "a power overflows or divides by zero"),
    ],
)
def test_an_angle_undefined_at_the_bound_values_is_refused(
    label: str, value: str, reason: str
) -> None:
    document = circuit((step(op(label=label)),))
    export = to_qasm(document, values={"theta": value})
    assert export.status == "refused"
    assert export.programs == ()
    assert export.reasons == (f"part 1: gate 'ry' at the bound values: {reason}",)
    # the same angle with a value the simulator can evaluate is written
    assert to_qasm(document, values={"theta": "2"}).status == "ok"


def test_a_braced_subscript_binds_the_symbol_of_the_grammar() -> None:
    document = circuit((step(op(label="R_y(\\theta_{1})")), step(op(label="R_z(\\theta_2)"))))
    export = to_qasm(document, values={"\\theta_{1}": "0.5", "theta_{2}": "0.25"})
    assert export.status == "ok", export.reasons
    assert body(export) == ["ry(0.5) q[0];", "rz(0.25) q[0];"]
    assert export.unmatched == ()
    assert [line for line in header(export) if line.startswith("// bound:")] == [
        "// bound: theta_1 = 0.5",
        "// bound: theta_2 = 0.25",
    ]


def test_an_integer_too_large_for_a_float_is_refused() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\theta)")),)), values={"theta": 10**400})
    assert export.status == "refused"
    assert export.reasons == ("value for 'theta': the number is too large for a float",)


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({"theta": "0.1", "\\theta": "0.2"}, "parameter 'theta' is bound twice"),
        ({"theta": True}, "value for 'theta': a parameter value cannot be true or false"),
        ({"theta": "\\phi"}, "value for 'theta' has free symbol(s): phi"),
    ],
)
def test_bindings_the_emitter_cannot_use_are_refused(values: dict, reason: str) -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\theta)")),)), values=values)
    assert export.status == "refused"
    assert export.reasons == (reason,)
    with pytest.raises(ValueError, match=re.escape(reason)):
        validate_bindings(values)


def test_a_part_lists_only_the_bindings_it_uses() -> None:
    left = (step(op(label="R_y(\\theta)")),)
    right = (step(op(kind="sep", label="=")), step(op(label="R_z(\\phi)")))
    export = to_qasm(circuit(left, right), values={"phi": "0.5", "theta": "pi/4", "x": "1"})
    assert export.status == "ok", export.reasons
    bound = [
        [line for line in header(export, part) if line.startswith("// bound:")] for part in (0, 1)
    ]
    assert bound == [["// bound: theta = pi/4"], ["// bound: phi = 0.5"]]
    assert export.unmatched == ("x",)


def test_a_refused_document_reports_every_binding_as_unmatched() -> None:
    export = to_qasm(circuit((step(op()),), status="partial"), values={"theta": "1"})
    assert export.status == "refused"
    assert export.unmatched == ("theta",)


def test_an_ok_export_keeps_the_reader_notes() -> None:
    document = {**circuit((step(op()),)), "label": "P_0"}
    export = to_qasm(document)
    assert export.status == "ok"
    assert export.programs
    assert len(export.notes) == 1 and "the document label 'P_0' is not read" in export.notes[0]
