"""The OpenQASM 3 export: one circuit part at a time, refusals as data."""

from __future__ import annotations

import pytest

from docingest.domain.circuits import CircuitSteps, GateOp
from docingest.domain.qasm import QasmExport, to_qasm


def op(
    kind: str = "gate",
    label: str = "H",
    *,
    targets: tuple[int, ...] = (1,),
    controls: tuple[int, ...] = (),
    open_controls: tuple[int, ...] = (),
    classical: bool = False,
    source: str = "",
) -> GateOp:
    return GateOp(
        kind=kind,
        label=label,
        targets=targets,
        controls=controls,
        open_controls=open_controls,
        classical=classical,
        source=source,
    )


def step(*items: GateOp) -> tuple[GateOp, ...]:
    return items


def circuit(
    *parts: tuple[tuple[GateOp, ...], ...],
    wires: int = 1,
    status: str = "parsed",
) -> CircuitSteps:
    legends = tuple(f"{wire} = \\ket{{q_{wire - 1}}}" for wire in range(1, wires + 1))
    return CircuitSteps("quantikz", status, "", legends, parts)


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


def test_one_qubit_gate_on_several_targets_is_the_tensor_product() -> None:
    export = to_qasm(circuit((step(op(label="H", targets=(1, 2))),), wires=2))
    assert body(export) == ["h q[0];", "h q[1];"]


def test_a_controlled_multi_target_gate_is_refused() -> None:
    item = op(label="H", targets=(1, 2), controls=(3,))
    export = to_qasm(circuit((step(item),), wires=3))
    assert export.status == "refused"
    assert export.programs == ()
    assert "ambiguous" in export.reasons[0]


# ------------------------------------------------------------------ parameters


def test_parameter_is_a_qasm_expression() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\pi/2)")),)))
    assert body(export) == ["ry(pi/2) q[0];"]


def test_frac_becomes_a_division() -> None:
    export = to_qasm(circuit((step(op(label="R_z(\\frac{\\pi}{4})")),)))
    assert body(export) == ["rz(pi/4) q[0];"]


def test_implicit_multiplication() -> None:
    export = to_qasm(circuit((step(op(label="R_x(2\\pi)")),)))
    assert body(export) == ["rx(2*pi) q[0];"]


def test_negative_angle() -> None:
    export = to_qasm(circuit((step(op(label="R_x(-\\pi/2)")),)))
    assert body(export) == ["rx((-pi)/2) q[0];"]


def test_sqrt_parameter() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\sqrt{2})")),)))
    assert body(export) == ["ry(sqrt(2)) q[0];"]


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
    assert body(export) == ["ry(pi/4) q[0];"]


def test_a_binding_key_may_keep_its_backslash() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\theta)")),)), values={"\\theta": "0.5"})
    assert body(export) == ["ry(0.5) q[0];"]


def test_a_bound_symbol_scales() -> None:
    export = to_qasm(circuit((step(op(label="R_y(2\\theta)")),)), values={"theta": "pi/4"})
    assert body(export) == ["ry(2*(pi/4)) q[0];"]


def test_a_bad_binding_is_refused() -> None:
    export = to_qasm(circuit((step(op(label="R_y(\\theta)")),)), values={"theta": "\\cos(1)"})
    assert export.status == "refused"
    assert "value for 'theta'" in export.reasons[0]


def test_exponentiation_is_refused() -> None:
    export = to_qasm(circuit((step(op(label="R_y(2^3)")),)))
    assert export.status == "refused"


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


def test_a_barrier_after_a_measurement_is_allowed() -> None:
    steps = (
        step(op(kind="measure", label="", targets=(1,))),
        step(op(kind="barrier", label="", targets=(1,))),
    )
    export = to_qasm(circuit(steps))
    assert export.status == "ok"
    assert body(export) == ["measure q[0] -> c[0];", "barrier q[0];"]


def test_measuring_a_wire_twice_is_refused() -> None:
    steps = (
        step(op(kind="measure", label="", targets=(1,))),
        step(op(kind="measure", label="", targets=(1,))),
    )
    export = to_qasm(circuit(steps))
    assert export.status == "refused"
    assert "twice" in export.reasons[0]


def test_a_controlled_measurement_is_refused() -> None:
    item = op(kind="measure", label="", targets=(2,), controls=(1,))
    export = to_qasm(circuit((step(item),), wires=2))
    assert export.status == "refused"


# ------------------------------------------------------------------ neutral marks


def test_barrier_on_wires_and_on_the_register() -> None:
    steps = (
        step(op(kind="barrier", label="", targets=(1, 2))),
        step(op(kind="barrier", label="", targets=())),
    )
    export = to_qasm(circuit(steps, wires=2))
    assert body(export) == ["barrier q[0], q[1];", "barrier q;"]


def test_text_zero_is_the_default_and_is_skipped() -> None:
    item = op(kind="text", label="\\ket{0}", targets=(1,))
    export = to_qasm(circuit((step(item),)))
    assert export.status == "ok"
    assert body(export) == []


def test_text_one_is_refused() -> None:
    item = op(kind="text", label="|1>", targets=(1,))
    export = to_qasm(circuit((step(item),)))
    assert export.status == "refused"
    assert "|1>" in export.reasons[0]


def test_an_empty_sign_is_skipped_and_a_label_is_refused() -> None:
    empty = to_qasm(circuit((step(op(kind="sign", label="", targets=(1,))),)))
    assert empty.status == "ok"
    assert body(empty) == []
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
    assert "inside a part" in export.reasons[0]


def test_a_part_that_fails_does_not_stop_the_next() -> None:
    bad = (step(op(label="H")), step(op(kind="unparsed_op", label="")))
    good = (step(op(kind="sep", label="=")), step(op(label="X")))
    export = to_qasm(circuit(bad, good))
    assert export.status == "refused"
    assert len(export.programs) == 1
    assert export.programs[0].part == 2
    assert export.reasons[0].startswith("part 1:")


# ------------------------------------------------------------------ refusals


def test_a_partial_reading_is_refused() -> None:
    export = to_qasm(circuit((step(op()),), status="partial"))
    assert export.status == "refused"
    assert "partial" in export.reasons[0]


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
    assert "outside" in export.reasons[0]


def test_a_circuit_without_wires_is_refused() -> None:
    export = to_qasm(CircuitSteps("quantikz", "parsed", "", (), ()))
    assert export.status == "refused"
    assert "no wires" in export.reasons[0]


def test_a_circuit_without_parts_is_refused() -> None:
    export = to_qasm(CircuitSteps("quantikz", "parsed", "", ("1",), ()))
    assert export.status == "refused"
    assert "no parts" in export.reasons[0]


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
    assert body(export) == ["sx q[0];", "sxdg q[0];"]


def test_a_phase_item_becomes_a_p_gate() -> None:
    item = op(kind="phase", label="\\pi/2", targets=(1,))
    export = to_qasm(circuit((step(item),)))
    assert body(export) == ["p(pi/2) q[0];"]


def test_joined_phase_labels_are_refused() -> None:
    item = op(kind="phase", label="\\alpha, \\beta", targets=(1,))
    export = to_qasm(circuit((step(item),)))
    assert export.status == "refused"


def test_a_labelled_dot_gate_must_be_a_swap() -> None:
    item = op(kind="dots_gate", label="SWAP", targets=(1, 2))
    export = to_qasm(circuit((step(item),), wires=2))
    assert body(export) == ["swap q[0], q[1];"]
    bad = op(kind="dots_gate", label="U", targets=(1, 2))
    assert to_qasm(circuit((step(bad),), wires=2)).status == "refused"


def test_ambiguous_box_labels_are_refused() -> None:
    export = to_qasm(circuit((step(op(label="CNOT", targets=(1, 2))),), wires=2))
    assert export.status == "refused"
    assert "control wires" in export.reasons[0]


@pytest.mark.parametrize("label", ["U", "iSWAP", "R_k(2)", "\\foo"])
def test_gates_without_an_exact_qasm_form_are_refused(label: str) -> None:
    export = to_qasm(circuit((step(op(label=label)),)))
    assert export.status == "refused"


# ------------------------------------------------------------------ format


def test_header_is_deterministic_and_names_the_wires() -> None:
    first = to_qasm(circuit((step(op()),), wires=2), origin="document.md block 3")
    second = to_qasm(circuit((step(op()),), wires=2), origin="document.md block 3")
    assert first.programs[0].text == second.programs[0].text
    lines = header(first)
    assert lines[0] == "// Generated by docingest (qasm format 1)"
    assert lines[1] == "// source: document.md block 3, part 1/1"
    assert lines[2] == "// wire 1 (top of the drawing) = q[0]; OpenQASM fixes no bit order"
    assert lines[3] == "// wires: 1 = \\ket{q_0}; 2 = \\ket{q_1}"
    assert lines[4] == "OPENQASM 3.0;"
    assert lines[5] == 'include "stdgates.inc";'
    assert lines[6] == "qubit[2] q;"


def test_a_newline_in_provenance_does_not_break_the_comment() -> None:
    export = to_qasm(circuit((step(op()),)), origin="line one\nline two")
    assert any(line.startswith("// source: line one line two") for line in header(export))


# ------------------------------------------------------------------ smaller refusals


@pytest.mark.parametrize(
    ("item", "wires"),
    [
        (op(kind="measure", label="", targets=(1, 2)), 2),
        (op(label="H", targets=()), 1),
        (op(label="SWAP", targets=(1,)), 2),
        (op(label="RZZ(\\pi)", targets=(1, 2, 3)), 3),
        (op(kind="x", label="X", targets=(1, 2)), 2),
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
        (op(label="H^\\dagger"), 1),
        (op(label="\\sqrt{Y}"), 1),
        (op(label="H(\\pi)"), 1),
        (op(label="RX"), 1),
        (op(label="(\\pi)"), 1),
        (op(label="H)"), 1),
    ],
)
def test_items_outside_the_contract_are_refused(item: GateOp, wires: int) -> None:
    export = to_qasm(circuit((step(item),), wires=wires))
    assert export.status == "refused"
    assert export.programs == ()


def test_two_drawn_equalities_in_one_part_are_refused() -> None:
    first = (step(op(label="H")),)
    second = (step(op(kind="sep", label="="), op(kind="sep", label="=")), step(op(label="X")))
    export = to_qasm(circuit(first, second))
    assert export.status == "refused"
    assert "more than one" in export.reasons[0]


def test_a_drawn_equality_sharing_its_step_is_refused() -> None:
    first = (step(op(label="H")),)
    second = (step(op(kind="sep", label="="), op(label="X")),)
    export = to_qasm(circuit(first, second))
    assert export.status == "refused"
    assert "stand alone" in export.reasons[0]


def test_a_blank_wire_legend_is_omitted() -> None:
    export = to_qasm(CircuitSteps("quantikz", "parsed", "", (" ",), ((step(op()),),)))
    assert not any(line.startswith("// wires:") for line in header(export))


# ------------------------------------------------------------------ expression details


def test_addition_and_braces_and_bare_pi() -> None:
    steps = (
        step(op(label="R_x(\\pi+1)")),
        step(op(label="R_y({\\pi})")),
        step(op(label="R_z(pi/2)")),
    )
    export = to_qasm(circuit(steps))
    assert body(export) == ["rx(pi+1) q[0];", "ry(pi) q[0];", "rz(pi/2) q[0];"]


def test_a_bare_greek_name_is_a_symbol() -> None:
    export = to_qasm(circuit((step(op(label="R_x(theta)")),)), values={"theta": "0.25"})
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
        "R_x(2^3)",
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
