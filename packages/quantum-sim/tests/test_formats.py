"""The circuit/1 reader and writer: what is read, every refusal, parts, symbols, round trips."""

import contextlib
import copy
import json
import math
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quantum_sim import (
    MAX_OPS,
    MAX_REASONS,
    AngleError,
    Circuit,
    Op,
    ParametricCircuit,
    RefusedError,
    formats,
    from_gate_list,
    statevector,
    to_gate_list,
)
from quantum_sim.formats import MAX_ITEMS
from quantum_sim.labels import MAX_LABEL_CHARS

PI = math.pi


def item(kind="gate", label="", targets=(), controls=(), open_controls=(), **more):
    return {
        "kind": kind,
        "label": label,
        "targets": list(targets),
        "controls": list(controls),
        "open_controls": list(open_controls),
        "classical": False,
        "source": "",
    } | more


def gate(label, *targets, controls=(), open_controls=()):
    return item("gate", label, targets, controls, open_controls)


def doc(*steps, wires=2, **more):
    """A one-part document; each argument is a step (an item or a list of items)."""
    part = [[s] if isinstance(s, dict) else list(s) for s in steps]
    return {"format": "circuit/1", "wires": wires, "parts": [part]} | more


def read_ops(*steps, wires=2, **more):
    reading = from_gate_list(doc(*steps, wires=wires, **more))
    return reading.circuits[0].bind().ops


def reasons_of(document) -> tuple[str, ...]:
    with pytest.raises(RefusedError) as caught:
        from_gate_list(document)
    return caught.value.reasons


def refused(document, text):
    reasons = reasons_of(document)
    assert any(text in reason for reason in reasons), reasons
    return reasons


# --- what is read -------------------------------------------------------------------------------


def test_bell_as_docingest_writes_it():
    document = {
        "format": "circuit/1",
        "kind": "quantikz",
        "status": "parsed",
        "label": "",
        "wires": 2,
        "wire_labels": ["1 = \\ket{0}", "2 = \\ket{0}"],
        "parts": [
            [
                [item("gate", "H", [1])],
                [item("x", "X", [2], [1])],
            ]
        ],
    }
    reading = from_gate_list(document)
    assert reading.signs == () and reading.notes == ()
    (circuit,) = reading.circuits
    assert circuit.wire_labels == ("1 = \\ket{0}", "2 = \\ket{0}")
    assert circuit.bind() == Circuit(2, (Op("h", (1,)), Op("x", (2,), controls=(1,))))
    assert not circuit.is_symbolic and circuit.symbols == ()


def test_a_hand_written_minimal_document_is_enough():
    document = {
        "format": "circuit/1",
        "wires": 2,
        "parts": [[[{"kind": "gate", "label": "H", "targets": [1]}]]],
    }
    assert from_gate_list(document).circuits[0].bind() == Circuit(2, (Op("h", (1,)),))


def test_x_items_with_closed_and_open_controls_and_several_targets():
    assert read_ops(item("x", "X", [3], [1, 2]), wires=3) == (Op("x", (3,), controls=(1, 2)),)
    assert read_ops(item("x", "X", [2], (), [1])) == (Op("x", (2,), open_controls=(1,)),)
    assert read_ops(item("x", "", [2, 3], [1]), wires=3) == (
        Op("x", (2,), controls=(1,)),
        Op("x", (3,), controls=(1,)),
    )
    assert read_ops(item("x", "X", [1])) == (Op("x", (1,)),)


def test_z_dots_are_a_z_carried_by_the_last_closed_dot():
    assert read_ops(item("z_dots", "Z", (), [1, 2])) == (Op("z", (2,), controls=(1,)),)
    assert read_ops(item("z_dots", "Z", (), [1, 2, 3]), wires=3) == (
        Op("z", (3,), controls=(1, 2)),
    )
    assert read_ops(item("z_dots", "Z", (), [1, 3], [2]), wires=3) == (
        Op("z", (3,), controls=(1,), open_controls=(2,)),
    )


def test_swap_and_controlled_swap():
    assert read_ops(item("swap", "", [1, 2])) == (Op("swap", (1, 2)),)
    assert read_ops(item("swap", "", [2, 3], [1]), wires=3) == (Op("swap", (2, 3), controls=(1,)),)
    assert read_ops(item("swap", "", [2, 3], (), [1]), wires=3) == (
        Op("swap", (2, 3), open_controls=(1,)),
    )


def test_gate_items_with_controls_and_labels():
    assert read_ops(gate("H", 2, controls=(1,))) == (Op("h", (2,), controls=(1,)),)
    assert read_ops(gate(r"R_y(\pi/2)", 2, open_controls=(1,))) == (
        Op("ry", (2,), open_controls=(1,), params=(PI / 2,)),
    )
    assert read_ops(gate("CZ", 1, 2)) == (Op("z", (2,), controls=(1,)),)
    assert read_ops(gate("CZ", 2, 3, controls=(1,)), wires=3) == (Op("z", (3,), controls=(2, 1)),)
    assert read_ops(gate(r"R_{zz}(1)", 1, 2)) == (Op("rzz", (1, 2), params=(1.0,)),)
    assert read_ops(gate("iSWAP", 1, 2)) == (Op("iswap", (1, 2)),)
    assert read_ops(gate("R_2", 1)) == (Op("rk", (1,), params=(2.0,)),)


def test_a_one_qubit_label_over_several_wires_is_one_gate_on_each():
    assert read_ops(gate("H", 1, 2)) == (Op("h", (1,)), Op("h", (2,)))
    assert read_ops(gate(r"R_x(1)", 1, 2, controls=(3,)), wires=3) == (
        Op("rx", (1,), controls=(3,), params=(1.0,)),
        Op("rx", (2,), controls=(3,), params=(1.0,)),
    )


def test_measurements_barriers_and_kets():
    ops = read_ops(
        gate("H", 1),
        item("barrier", "", [1, 2]),
        item("text", r"\ket{0}", [2]),
        item("text", "", [2]),
        item("text", r"|1\rangle", [2]),
        [item("measure", "", [1]), item("measure", "Z", [2])],
    )
    assert ops == (Op("h", (1,)), Op("measure", (1,)), Op("measure", (2,)))
    assert read_ops(item("measure", "", [1, 2])) == (Op("measure", (1,)), Op("measure", (2,)))


def test_several_items_in_one_step_keep_their_order():
    assert read_ops([gate("H", 1), gate("X", 2)]) == (Op("h", (1,)), Op("x", (2,)))


def test_symbols_become_parameters_and_bind_to_values():
    reading = from_gate_list(doc(gate(r"R_y(\theta)", 1), gate(r"R_z(-2\gamma)", 2)))
    (circuit,) = reading.circuits
    assert circuit.is_symbolic and circuit.symbols == ("gamma", "theta")
    assert reading.symbols == ("gamma", "theta")
    assert circuit.bind({"theta": 1.0, "gamma": 0.25}) == Circuit(
        2, (Op("ry", (1,), params=(1.0,)), Op("rz", (2,), params=(-0.5,)))
    )
    with pytest.raises(AngleError, match="no value for the symbol 'gamma'"):
        circuit.bind({"theta": 1.0})
    with pytest.raises(AngleError):
        circuit.bind()


def test_parts_split_at_drawn_equalities():
    document = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [
            [[gate("H", 1)], [gate("Z", 1)], [gate("H", 1)]],
            [[item("sep", "=")], [gate("X", 1)]],
            [[item("sep", r"\equiv")], [gate("X", 1)]],
            [[item("sep", r"\to")]],
        ],
    }
    reading = from_gate_list(document)
    assert reading.signs == ("=", r"\equiv", r"\to")
    assert [len(c.ops) for c in reading.circuits] == [3, 1, 1, 0]
    assert [reading.claims_equality(i) for i in range(3)] == [True, True, False]


def test_a_wire_legend_with_a_ket_1_becomes_a_note():
    document = doc(gate("H", 1), wire_labels=["1 = \\ket{0}", "2 = \\ket{1}"])
    reading = from_gate_list(document)
    assert len(reading.notes) == 1 and reading.notes[0].startswith("wire 2: the legend")
    assert "not read as an input state" in reading.notes[0]
    assert reading.input_kets == ((1, "0"), (2, "1"))
    plain = from_gate_list(doc(gate("H", 1), wire_labels=["1 = \\ket{0}", "2"]))
    assert plain.notes == ()
    assert plain.input_kets == ((1, "0"),)


# the spellings of a computational-basis ket that docingest copies from a paper
KET_SPELLINGS = [
    r"\ket{{{}}}",
    r"\ket{{ {} }}",
    r"\ket {}",
    r"\ket{}",
    r"|{}\rangle",
    r"| {} \rangle",
    r"\left|{}\right\rangle",
    r"\left| {} \right\rangle",
    r"\lvert {}\rangle",
    r"\vert {}\rangle",
    r"\left\lvert {}\right\rangle",
    r"$\ket{{{}}}$",
    r"{{\ket{{{}}}}}",
    r"${{\ket{{{}}}}}$",
]


@pytest.mark.parametrize("spelling", KET_SPELLINGS)
def test_every_spelling_of_the_ket_1_in_a_legend_gets_a_note(spelling):
    legend = "1 = " + spelling.format("1")
    reading = from_gate_list(doc(gate("H", 2), wire_labels=[legend, "2"]))
    (note,) = reading.notes
    assert note.startswith("wire 1: the legend") and "not read as an input state" in note


@pytest.mark.parametrize("spelling", KET_SPELLINGS)
def test_every_spelling_of_the_ket_0_in_a_legend_is_read_with_no_note(spelling):
    legend = "1 = " + spelling.format("0")
    assert from_gate_list(doc(gate("H", 2), wire_labels=[legend, "2"])).notes == ()


@pytest.mark.parametrize("spelling", KET_SPELLINGS)
@pytest.mark.parametrize("bit", ["0", "1"])
def test_the_same_spellings_are_the_text_item_that_is_a_ket(spelling, bit):
    text = spelling.format(bit)
    reading = from_gate_list(doc(item("text", text, [1]), gate("H", 2)))
    assert reading.circuits[0].bind().ops == (Op("h", (2,)),)
    assert len(reading.notes) == (bit == "1")


@pytest.mark.parametrize(
    "legend",
    [
        "1",
        "1 =",
        "1 = ",
        "1=",
        "q_0",  # a legend without its number
        "",
        "1 = A",
        "1 = q_0",
        "1 = q0",
        "1 = q_{12}",
        "1 = S_{2m-1}",
        "1 = q_{2n,0}",
        "1 = x'",
        "1 = x_1'",
        "1 = {$v_0$}",
        "1 = $v_0$",
        "1 = {{q_0}}",
        r"1 = \mathrm{A}",
        r"1 = \text{q}_0",
        r"1 = {\mathrm{q}_{12}}",
        "1 = a (one label for wires 1 and 2)",
        "1 = a (one label for wires 1, 2 and 3)",
        "1 = a (one label for wires 1 to 40)",
        "1 = q_0, output q_1",
        "1 = q_0, output",
        "1 = output q_1",
        "1 =output q_1",
        "1=output q_1",
        "1 = output",
        "1 = {$v_0$}, output {$v_1$}",
        r"1 = \ket{0}, output q",
        r"1 = \ket{1}, output a (one label for wires 1 and 2)",
        "1 = a (one label for wires 1 and 2), output b (one label for wires 1 and 2)",
        "output q_1",
        "1  =\t q_0 \n",  # white space is one space
        "1 = q_0,\n output\tq_1",
    ],
)
def test_a_legend_that_is_empty_a_plain_name_or_a_basis_ket_is_read(legend):
    from_gate_list(doc(gate("H", 2), wire_labels=[legend, "2"]))


@pytest.mark.parametrize(
    "legend",
    [
        "1 = {q_0x",  # braces and dollar signs are taken off only when they wrap the whole
        "1 = q_0}",
        "1 = {q_0}}",
        "1 = {{q_0}",
        "1 = $q_0",
        "1 = q_0$",
        "1 = {q}_{0}",
        "1 = {q}{0}",
        "1 = ${q}$ ${0}$",
        "1 = q, outputx",  # the word output only splits a legend as a whole word
        "1 = outputq",
        "1 = aoutput b",
        "1 = q, outputs b",
        "1 = q, output_1",
        "1 = x_{",
        "1 = x_{}",
        "1 = x_{123456789}",
        "1 = x_{a b}",
        "1 = x_1_2",
        "1 = x^2",
        "1 = x123",
        "1 = _1",
        "1 = 'x",
        "1 = x_{a}b",
        "1\u00a0= q_0",  # only ASCII white space is white space
        "1 = q_0\u2003",
        "1 = q_0,\u00a0output q_1",
        r"1 = \mathbf{A}",
        r"1 = \mathrm{AB}",
        r"1 = \textbf{A}",
        r"1 = \mathrm{A",
        "1 = ,",
        "1 = , q",
        "1 == q",
        "1 = q (one label for wires)",
        "1 = q (one label for wires 1 and 2) x",
        "1 = (one label for wires 1 and 2)",
        "1 = q (one label for wires 1 and a)",
    ],
)
def test_a_name_or_wrapper_that_is_not_closed_or_not_whole_is_refused(legend):
    refused(doc(gate("H", 1), wire_labels=[legend, "2"]), "wire 1: ")


@pytest.mark.parametrize(
    "legend",
    [
        "1 = a, output v_1",
        "1 = a, output S_{2m-1}",
        "1 = a, output q_{2n,0}",
        "1 = a, output x_{n+1}",
        "1 = a, output q_{12}",
        "1 = a, output q_{a1b}",  # no two letters in a row
        "1 = a, output q_{a-b}",
        r"1 = a, output \mathrm{A}_{12}",
        "1 = q_{postsel}",  # an input is the caller's: a word there is not simulated
        "1 = x_{ps}",
        "1 = P_{proj}",
    ],
)
def test_an_output_name_may_not_hold_two_letters_in_a_row_in_its_subscript(legend):
    from_gate_list(doc(gate("H", 2), wire_labels=[legend, "2"]))


@pytest.mark.parametrize(
    "name", ["q_{postsel}", "x_{ps}", "P_{proj}", "x_{out}", "q_{1ps}", "{$q_{ps}$}"]
)
def test_a_word_in_the_subscript_of_an_output_is_refused(name):
    for legend in (f"1 = a, output {name}", f"1 = output {name}"):
        (reason,) = refused(doc(gate("H", 1), wire_labels=[legend, "2"]), "wire 1: output legend")
        assert "not read" in reason
    assert reasons_of(doc(gate("H", 1), item("barrier", name, [])))


@pytest.mark.parametrize("title", ["", "  ", "t_1", "$t_1$", "{t_{12}}", "A", "S_{2m-1}", "q'"])
def test_a_slice_title_that_is_empty_or_a_plain_name_is_read(title):
    assert read_ops(gate("H", 1), item("barrier", title, [])) == (Op("h", (1,)),)


@pytest.mark.parametrize(
    "title",
    [
        "postselect",
        "postselect on 1",
        "$q_1 = 1$",
        "q_1 = 1",
        r"\rho^{\text{ABC}}",
        r"\ket{1}",
        r"\bra{1}",
        r"|1\rangle",
        r"\Proj{1}",
        r"\Pi_1",
        "output",
        "outcome 1",
        "1",
        "0",
        "$$",
        "t_1 t_2",
        "q_{postsel}",
    ],
)
def test_a_slice_title_that_is_not_a_plain_name_is_refused(title):
    # a barrier has no effect, but its title is free text: "postselect q1 = 1" there would run
    # unprojected, so a title is read like an output, by what it may hold
    (reason,) = refused(doc(gate("H", 1), item("barrier", title, [])), "part 1, step 2, item 1: ")
    assert "the slice title " in reason and "is not read" in reason
    assert "a word there may be a postselection" in reason


def test_a_slice_title_of_more_than_the_label_limit_is_refused_before_a_pattern():
    long = "t_1" + " " * MAX_LABEL_CHARS
    (reason,) = reasons_of(doc(item("barrier", long, [])))
    assert f"a slice title of {len(long):,} characters (at most {MAX_LABEL_CHARS})" in reason
    exact = "t_1" + " " * (MAX_LABEL_CHARS - 3)
    assert read_ops(item("barrier", exact, [])) == ()


@pytest.mark.parametrize(
    "legend",
    [
        "\u0661 = a",  # an Arabic-Indic digit is not a legend number
        "1 = a, output b (one label for wires \u0661 and \u0662)",
        "1 = |1\u00a0\\rangle",  # nor is a no-break space a space inside a ket
        "1 = \\ket{\u20031}",
        "1 = \\ket\u00a01",
    ],
)
def test_only_ascii_is_read_in_a_legend(legend):
    refused(doc(gate("H", 1), wire_labels=[legend, "2"]), "wire 1: ")


@pytest.mark.parametrize("text", ["|1\u00a0\\rangle", "\\ket{\u20031}", "\\ket\u00a01"])
def test_only_ascii_white_space_is_read_in_a_ket_that_is_a_text_item(text):
    refused(doc(item("text", text, [1])), "is not a gate and not a ket")


def test_what_docingest_writes_for_a_legend_is_read_part_by_part():
    split = formats._split_legend
    assert split("1") == ("", "")
    assert split("1 = a") == ("a", "")
    assert split("1 = a, output b") == ("a", "b")
    assert split("1 = output b") == ("", "b")
    assert split("1 =output b") == ("", "b")
    assert split("1 = a (one label for wires 1 and 2), output b") == (
        "a (one label for wires 1 and 2)",
        "b",
    )
    assert split("a, output b") == ("a", "b")
    assert split("1 = , output b") == (",", "b")
    assert split("1 = a, output b, output c") == ("a", "b, output c")
    assert split(" 1  =  a ") == ("a", "")


@pytest.mark.parametrize("text", [r"\ket{0}", r"|0\rangle", "", "  ", r"\lvert 0\rangle"])
def test_a_ket_0_or_an_empty_text_item_gets_no_note(text):
    reading = from_gate_list(doc(item("text", text, [1]), gate("H", 2)))
    assert reading.notes == ()
    assert reading.input_kets == (((1, "0"),) if text.strip() else ())


@pytest.mark.parametrize("text", [r"\ket{1}", r"$\ket{1}$", r"|1\rangle", r"\lvert 1\rangle"])
def test_a_ket_1_in_a_text_item_gets_a_note_with_its_place(text):
    # a first column of kets is the same information as the legends
    reading = from_gate_list(doc(item("text", text, [1]), gate("H", 2)))
    (note,) = reading.notes
    assert note.startswith("part 1, step 1, item 1: the text ")
    assert "is a ket, not read as an input state" in note and "|0...0>" in note
    assert reading.input_kets == ((1, "1"),)
    assert reading.circuits[0].bind().ops == (Op("h", (2,)),)


# What a wire can hold that is not a basis ket, as reviews 3 to 7 found it: a bra, a bar in any
# spelling, a projector or an inner product with a macro of the paper's own, an expectation value,
# a ket that is not |0> or |1>, an operator or a mark on a ket, a word that names a projection.
# None of them is looked for by its spelling: they are outside the grammar.
BRA_SPELLINGS = [
    r"\bra{0}",
    r"\bra{1}",
    r"\bra{+}",
    r"\bra{\psi}",
    r"$\bra{1}$",
    r"\bra 1",
    r"\bra1",
    r"\bra  {1}",
    r"\Bra{1}",
    r"\bra*{1}",
    r"$\bra 1$",
    r"\langle 1|",
    r"\langle1|",
    r"\langle 1 \rvert",
    r"\langle 1 \vert",
    r"\left\langle 1\right|",
    r"\mathinner{\langle 1|}",
    r"\langle\!\langle 1|",
    "\u27e81|",
    "$\\langle 0|$",
    "$<1|$",
    "<1|",
    "< + |",
    r"\ket{0}\bra{0}",
    r"|1\rangle\langle 1|",
    r"\ketbra{1}{1}",
    r"\ketbra*{1}",
    r"\proj{1}",
    r"\dyad{1}",
    r"\op{1}{1}",
    r"\op 1",
    r"\outerproduct{1}{1}",
    r"\outerproduct 1 1",
    r"\outerproduct*{1}{1}",
]
BAR_BRAS = [
    r"$\left<1\right\vert$",
    r"$\left< 1 \right\rvert$",
    r"$\left<1\middle\vert$",
    r"$<1\rvert$",
    r"$<1\right|$",
    r"\textlangle 1|",
    r"\lAngle 1|",
    "\uff1c1|",
    r"\braket{1|1}",
    r"\mid 1",
    r"a \Vert b",
    r"\lVert 1 \rVert",
    "1 \u2223 0",
    "\uff5c1",
    r"|1>",
    r"x \mvert y",
    r"$\left<1\right\arrowvert$",
    r"$\left<1\right\Arrowvert$",
    r"$\left<1\right\bracevert$",
    r"<1\shortmid",
    r"\textlangle 1\textbar",
    r"\textlangle 1\textbardbl",
    "<1\u2502",
    "<1\u2503",
    "<1\u01c0",
    "<1\u23d0",
    "<1\u2758",
    r"a \vline b",
    r"$\left<1\right\vert, \left\vert 0\right\rangle$",
    r"\textlangle 1\lvert\,\psi\rangle",
    r"\left<1\right\vert\psi\rangle",
    "<1",
    r"\lt 1",
    r"\textlangle 1",
    r"\lAngle 1",
    r"\llangle 1",
    r"\guilsinglleft 1",
    "\u27e81",
    "\u23291",
    "\u30081",
    "\u20391",
    r"\left<1\right.",
    r"\ket{1}^\dagger",
    r"\ket{1}^{\dagger}",
    r"\ket{1}^\dag",
    r"(|1\rangle)^\dagger",
    r"\ket1^\dagger",
    "\\ket{1}\u2020",
    r"\ket{\{1}<1\vert",
    r"\ket{ <1\rvert",
]
UNKNOWN_PROJECTIONS = [
    r"\kb{1}{1}",
    r"\Proj{1}",
    r"\projector{1}",
    r"\dm{1}",
    r"\bk{1}",
    r"\pbra{1}",
    r"\pure{1}",
    r"\braket{1}{1}",
    r"\ip{1}{1}",
    r"\innerproduct{1}{1}",
    r"\Braket{1}",
    "\u27ea1\u2225",
    "\ufe641\uffe8",
    "\u276c1\u2759",
    "\\textless 1\u00a6",
    r"\lang 1\divides",
    "\u03c8",
    r"\Pi_1",
    r"\operatorname{P}_1",
    r"\ket{0}\Anc",
    r"\Anc",
    r"\Sys",
    r"\projection",
    r"\opening",
    r"\brackets",
    r"\outerproducts",
]
EXPECTATION_VALUES = [
    r"\langle Z\rangle",
    r"$\langle X \rangle$",
    "\u27e8Z\u27e9",
    r"\langle Z \rangle_A",
    r"\expval{Z}",
    r"\expval*{Z}{\psi}",
    r"\ev{Z}",
    r"\expectationvalue{Z}",
    r"\mel{1}{Z}{1}",
    r"\matrixel{1}{Z}{1}",
    r"\matrixelement{1}{Z}{1}",
    r"\eval{Z}",
    r"\evolve",
    r"\melody",
    r"\expvalue",
    r"\langle \psi|H|\psi\rangle",
    r"\langle 0|\psi\rangle",
]
KETS_THAT_ARE_NOT_A_BASIS_STATE = [
    r"\ket{+}",
    r"\ket{-}",
    r"\ket+",
    r"\ket-",
    r"\ket{\psi}",
    r"\ket\psi",
    r"\ket{\psi_{1}}",
    r"\ket{p}",
    r"\ket{q_0}",
    r"|q_0\rangle",
    r"|\psi\rangle",
    "|+\\rangle",
    r"\ket{00}",
    r"\ket{01}",
    r"|10\rangle",
    r"\ket{2}",
    r"\ket{}",
    r"\ket",
    r"\ket}",
    r"\ketone",
    r"\Ket{1}",
    r"\Ket1",
    r"\ket*{1}",
    r"\ket*\psi",
    r"\ket{1}\ket{0}",
    r"\ket{0}, \ket{1}",
    r"\ket{\ket{0}}",
    r"\ket{a|b}",
    r"\ket{",
    "|1\u27e9",
    "|\u03c8\u27e9",
    r"\ket{\u03c8}",
    r"\ket{0}^{T}",
    r"\ket{0}^\perp",
    r"\ket{1}^{\ast}",
    r"\ket{0}_x",
    r"\ket{0}_{+}",
    r"\ket{0}_{\pm}",
    r"\ket{0}_{a_1}",
    r"\ket0_{a_1}",
    r"\ket{0}\Anc",
    r"\mid 0\rangle",
    r"\Vert 0\rangle",
    r"\| 0\rangle",
    r"\rvert 0\rangle",
    r"\textbar 0\rangle",
    r"\ket{0} (one label for wires 1 and 2)",
    r"\ket{0}\ (one label for wires 1 and 2)",
    r"H\ket{0}",
    r"X\ket{0}",
    r"\sigma_x\ket{0}",
    r"\hat{X}\ket{0}",
    r"X\,\ket{0}",
    r"H|0\rangle",
    r"H\left|0\right\rangle",
    r"a_1\;\ket{0}",
    r"a{=}\ket0",
    r"(\ket{0})",
    r"\mathrm{in}: \ket{0}",
    r"\ket{0}\ket{0}",
    r"\textcolor{gray}{\ket{0}}",
    r"\ket{0} (ancilla)",
]
WORDS_THAT_NAME_A_PROJECTION = [
    r"\text{postselect } 1",
    r"\text{post-selected on }1",
    r"\text{postselected on 1}",
    r"\mathrm{postselect 1}",
    "post-selected on 1",
    "postselect 1",
    "Postselection 0",
    "post select",
    "POST_SELECTED",
    "postselect b 1",
    r"\ket{postselect}",
    "projected onto 1",
    "measured 1",
    "outcome 1",
    r"\text{the run is postselected on this wire being 1 not 0}",  # more than 16 characters
]
# a word is not a name: it can say that the run is postselected, or what the input is
WORDS_THAT_NAME_A_WIRE = [
    "data 1",
    "anc",
    "ancilla",
    "coin",
    "excited",
    "one",
    "ab",
    "a b",
    r"\text{anc}",
    r"\text{data 1}",
    r"\mathrm{anc}",
    r"\mathrm{data 1}",
    r"{\text{anc}}",
]
NUMBERS = ["0", "1", "2", "10", "= 1", "= $0$", "$1$", "$0$", "1 0", ": 1"]
OUTPUT_PLACES = [
    "1 = output {}",
    r"1 = \ket{{0}}, output {}",
    "1 = a, output {}",
    "1 = q_0,output {}",
]
NOT_READ = [
    *BRA_SPELLINGS,
    *BAR_BRAS,
    *UNKNOWN_PROJECTIONS,
    *EXPECTATION_VALUES,
    *KETS_THAT_ARE_NOT_A_BASIS_STATE,
    *WORDS_THAT_NAME_A_PROJECTION,
    *WORDS_THAT_NAME_A_WIRE,
]


def bell_with(legend):
    # \lstick{\ket{0}} & \gate{H} & \ctrl{1} & \rstick{X} \\ \lstick{\ket{0}} & & \targ{} &
    return doc(
        gate("H", 1),
        item("x", "X", [2], controls=[1]),
        wire_labels=[legend, r"2 = \ket{0}"],
    )


@pytest.mark.parametrize("spelling", NOT_READ)
@pytest.mark.parametrize("place", ["1 = {}", r"1 = \ket{{0}}, {}", "1 = a, {}", "1 = {}, a"])
def test_a_legend_input_that_is_not_a_name_or_a_basis_ket_is_refused(place, spelling):
    (reason,) = refused(bell_with(place.format(spelling)), "wire 1: input legend not read: ")
    assert "output legend not read" not in reason


@pytest.mark.parametrize("spelling", [*NOT_READ, *NUMBERS])
@pytest.mark.parametrize("place", OUTPUT_PLACES)
def test_a_legend_output_that_is_not_a_plain_name_is_refused(place, spelling):
    # \rstick{X} where X is any of them: the drawing may postselect wire 1, which is not simulated
    (reason,) = refused(bell_with(place.format(spelling)), "wire 1: output legend not read: ")
    assert "input legend not read" not in reason and "a postselection" in reason


@pytest.mark.parametrize(
    "spelling", [r"\ket{0}", r"\ket{1}", r"|1\rangle", r"\left|0\right\rangle"]
)
@pytest.mark.parametrize("place", OUTPUT_PLACES)
def test_an_output_ket_is_refused_even_a_basis_ket(place, spelling):
    refused(bell_with(place.format(spelling)), "output legend not read")


def test_the_reason_for_an_output_that_is_not_read_names_the_legend_and_the_rule():
    (reason,) = refused(bell_with(r"1 = \ket{0}, output \bra{1}"), "wire 1: ")
    assert reason.startswith(r"wire 1: output legend not read: '1 = \\ket{0}, output \\bra{1}'")
    assert "may be a postselection, which is not simulated" in reason
    (reason,) = refused(bell_with(r"1 = \ket{+}"), "wire 1: ")
    assert reason.startswith(r"wire 1: input legend not read: ") and r"\ket{0}" in reason


def test_an_input_and_an_output_that_are_not_read_are_two_causes():
    reasons = refused(bell_with(r"1 = \ket{+}, output 1"), "wire 1: input legend not read")
    assert len(reasons) == 2 and "output legend not read" in reasons[1]


@pytest.mark.parametrize("spelling", NOT_READ)
def test_a_bra_postselected_bell_circuit_is_refused_in_every_spelling(spelling):
    refused(bell_with(r"1 = \ket{0}, output " + spelling), "wire 1: output legend not read")
    refused(bell_with(r"1 = \ket{0}, output " + spelling), "wire 1")


def test_the_postselection_of_the_bell_circuit_is_refused_and_not_simulated_as_a_bell_state():
    # \lstick{\ket{0}} & \gate{H} & \ctrl{1} & \rstick{\bra{1}} \\ \lstick{\ket{0}} & & \targ{} &
    # accepted as the Bell state, it gives [0.5, 0, 0, 0.5]; the drawing keeps only |11>
    document = bell_with(r"1 = \ket{0}, output \bra{1}")
    refused(document, "output legend not read")
    without_bra = doc(*document["parts"][0], wire_labels=[r"1 = \ket{0}", r"2 = \ket{0}"])
    assert from_gate_list(without_bra).notes == ()


@pytest.mark.parametrize("spelling", NOT_READ)
def test_a_text_item_that_is_not_a_ket_is_refused(spelling):
    # `& \gate{H} & \bra{1}` is a postselection, not an annotation; nothing is looked for by its
    # spelling: the text is read only if it is a ket |0> or |1>
    refused(doc(gate("H", 1), item("text", spelling, [1])), "part 1, step 2, item 1: text")
    (reason,) = reasons_of(doc(item("text", spelling, [1])))
    assert "is not a gate and not a ket |0> or |1>" in reason


def test_a_legend_or_a_text_label_of_more_than_the_gate_label_limit_is_refused_before_a_pattern():
    limit = MAX_LABEL_CHARS
    for filler in (r"\langle ", r"\ket{", r"\lvert ", "<"):
        long = filler * (limit // len(filler) + 1)
        assert len(long) > limit
        (reason,) = refused(doc(gate("H", 1), wire_labels=[long, "2"]), "wire 1")
        assert f"a legend of {len(long):,} characters (at most {limit})" in reason
        (reason,) = reasons_of(doc(item("text", long, [1])))
        assert f"text of {len(long):,} characters on a wire (at most {limit})" in reason
    huge = r"\langle " * 8000  # 64 KB: refused by its length alone, not scanned
    refused(doc(gate("H", 1), wire_labels=[huge, "2"]), "wire 1")
    assert reasons_of(doc(item("text", huge, [1])))


def test_a_legend_or_a_text_label_of_exactly_the_limit_is_still_read():
    legend = "1 = a" + " " * (MAX_LABEL_CHARS - 5)
    assert len(legend) == MAX_LABEL_CHARS
    from_gate_list(doc(gate("H", 1), wire_labels=[legend, "2"]))
    ket = r"\ket{0}" + " " * (MAX_LABEL_CHARS - 7)
    assert len(ket) == MAX_LABEL_CHARS
    from_gate_list(doc(item("text", ket, [1])))


@pytest.mark.parametrize("text", [r"\ket{0}", r"\ket{1}", r"|0\rangle", r"$\ket{1}$"])
def test_a_ket_after_a_gate_on_its_wire_is_refused(text):
    # `& \gate{H} & \ket{1}`: after a gate a ket is a reset or a postselection
    (reason,) = refused(
        doc(gate("H", 1), item("text", text, [1])), "part 1, step 2, item 1: the text "
    )
    assert "is a ket after a gate on its wire or in the column of one" in reason
    assert "reset or a postselection" in reason


def test_a_ket_after_a_gate_is_refused_for_every_way_the_wire_was_used():
    controlled = item("x", "X", [2], controls=[1])
    for step in (controlled, item("measure", "", [2]), gate("H", 2, open_controls=[1])):
        refused(doc(step, item("text", r"\ket{0}", [1, 2])), "is a ket after a gate on its wire")
    # a ket on a wire no gate has used yet is the input: |0> is silent
    assert from_gate_list(doc(gate("H", 1), item("text", r"\ket{0}", [2]))).notes == ()
    assert from_gate_list(doc(item("text", r"\ket{0}", [1]), gate("H", 1))).notes == ()
    # another part starts with its own wires
    two_parts = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [
            [[gate("H", 1)]],
            [[item("sep", "=")], [item("text", r"\ket{0}", [1])]],
        ],
    }
    assert from_gate_list(two_parts).notes == ()


@pytest.mark.parametrize("text", [r"\ket{0}", r"\ket{1}"])
def test_a_ket_in_the_step_of_a_gate_on_its_wire_is_refused_in_either_order(text):
    # a step is a column and has no order: a ket listed before the gate is as ambiguous as one after
    # it (a hand-written Bell pair with `\ket{0}` on wire 2 beside the CNOT would be accepted)
    controlled = item("x", "X", [2], controls=[1])
    ket = item("text", text, [2])
    for step in ([ket, controlled], [controlled, ket], [ket, item("measure", "", [2])]):
        (reason,) = refused(doc(gate("H", 1), step), "part 1, step 2, item ")
        assert "reset or a postselection" in reason
    # on a wire no other item of the column uses it is still an input
    column = [item("text", r"\ket{0}", [2]), gate("H", 1)]
    assert from_gate_list(doc(column)).notes == ()


def test_a_ket_on_no_wire_is_refused():
    for text in (r"\ket{0}", r"\ket{1}"):
        (reason,) = refused(doc(gate("H", 1), item("text", text, [])), "part 1, step 2, item 1")
        assert "is a ket on no wire" in reason
    # other text with no wire is still the empty annotation it was
    assert from_gate_list(doc(item("text", "", []))).notes == ()


@pytest.mark.parametrize(
    "legends", [["2 = q", "1 = r"], ["12", "2"], ["7 = q", "9 = r"], ["01", "2"]]
)
def test_a_legend_numbered_for_another_wire_is_refused(legends):
    # which legend is on which wire is unknown, as when the count is wrong
    reasons = refused(doc(gate("H", 1), wire_labels=legends), "is numbered for another wire")
    assert reasons[0].startswith("wire 1: ")
    assert from_gate_list(doc(gate("H", 1), wire_labels=["1 = q", "2 = r"])).notes == ()


@pytest.mark.parametrize("value", [["postselect q_2 = 0"], {"a": 1}, 7, 1.5, True, None])
def test_a_document_label_that_is_not_text_is_refused(value):
    refused(doc(gate("H", 1), label=value), "label: the document label must be text")


@pytest.mark.parametrize("value", [["quantikz"], {"a": 1}, 7, True, None])
def test_a_document_kind_that_is_not_text_is_refused(value):
    # it would turn off the rule for one box over several wires
    refused(doc(gate("H", 1, 2), kind=value), "kind: the document kind must be text")


def test_the_document_label_is_not_read_and_a_note_says_so():
    assert from_gate_list(doc(gate("H", 1))).notes == ()
    assert from_gate_list(doc(gate("H", 1), label="  ")).notes == ()
    for label in ("fig:bell", "Bell pair postselected on $q_2=1$"):
        (note,) = from_gate_list(doc(gate("H", 1), label=label)).notes
        assert note.startswith(f"label: the document label {label!r} is not read")
        assert "not postselected" in note
    long = "x" * 200
    (note,) = from_gate_list(doc(gate("H", 1), label=long)).notes
    assert long not in note


def test_a_text_item_without_a_label_is_read_and_never_crashes():
    # a label is optional in circuit/1: {"kind": "text"} is an empty annotation
    document = {"format": "circuit/1", "wires": 1, "parts": [[[{"kind": "text"}]]]}
    assert from_gate_list(document).circuits[0].bind().ops == ()
    for more in ({"targets": [1]}, {"label": ""}):
        assert from_gate_list(document | {"parts": [[[{"kind": "text"} | more]]]}).notes == ()


@pytest.mark.parametrize(
    "kind",
    ["gate", "x", "swap", "z_dots", "measure", "barrier", "text", "dots", "control", "sep", "?"],
)
def test_an_item_with_only_a_kind_is_a_reading_or_a_refusal(kind):
    document = {"format": "circuit/1", "wires": 2, "parts": [[[{"kind": kind}]]]}
    with contextlib.suppress(RefusedError):
        from_gate_list(document)


def test_notes_are_listed_up_to_the_limit_and_the_rest_are_counted():
    steps = [[item("text", r"\ket{1}", [1])] for _ in range(MAX_REASONS + 3)]
    notes = from_gate_list(doc(*steps)).notes
    assert len(notes) == MAX_REASONS + 1 and notes[-1] == "... and 3 more"


def test_the_documents_are_not_changed_by_reading_them():
    document = doc(gate(r"R_y(\theta)", 1), item("x", "X", [2], [1]))
    before = copy.deepcopy(document)
    from_gate_list(document)
    assert document == before


# --- what is refused ----------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, 1, "circuit/1", [], [doc(gate("H", 1))]])
def test_a_document_must_be_an_object(bad):
    assert reasons_of(bad) == ("a circuit document must be an object",)


def test_the_format_wires_and_parts_are_checked():
    refused(doc(gate("H", 1)) | {"format": "circuit/2"}, "format: expected 'circuit/1'")
    refused({"wires": 1, "parts": [[]]}, "format: expected 'circuit/1'")
    for wires in (0, -1, True, 1.5, "2", None):
        refused(doc(gate("H", 1)) | {"wires": wires}, "wires: must be an integer")
    for parts in (None, [], {}, "x"):
        refused(doc() | {"parts": parts}, "parts: must be a non-empty list")
    refused(doc() | {"parts": [None]}, "each part must be a list of steps")
    refused(doc() | {"parts": [[None]]}, "each part must be a list of steps")
    refused(doc() | {"parts": [[{"a": 1}]]}, "each part must be a list of steps")


@pytest.mark.parametrize("status", ["partial", "unparsed", "", None, 1])
def test_only_a_parsed_circuit_is_read(status):
    refused(doc(gate("H", 1)) | {"status": status}, "status: the circuit was read as")


def test_a_circuit_docingest_could_not_read_is_refused_for_its_status():
    # such a circuit has no wires and no parts: the status is the reason to give
    unread = {"format": "circuit/1", "kind": "quantikz", "status": "unparsed", "label": ""}
    unread |= {"wires": 0, "wire_labels": [], "parts": []}
    assert reasons_of(unread) == ("status: the circuit was read as 'unparsed', not 'parsed'",)
    assert reasons_of(unread | {"status": "partial"}) == (
        "status: the circuit was read as 'partial', not 'parsed'",
    )


def test_numbers_in_a_message_are_cut_to_forty_characters():
    huge = 10**3000
    (reason,) = reasons_of(doc(gate("H", huge), wires=2))
    assert reason == "part 1, step 1, item 1: wire " + "1" + "0" * 36 + "... is not in 1..2"
    reasons = reasons_of(doc(gate("H", 1), wires=huge, wire_labels=["a"]))
    assert reasons == ("wire_labels: 1 legends for " + "1" + "0" * 36 + "... wires",)


@pytest.mark.parametrize("kind", ["unparsed", "unparsed_op", "unparsed_group", "unparsed_outside"])
def test_unparsed_items_refuse_the_whole_circuit(kind):
    document = doc(gate("H", 1), item(kind, "", (), source=r"\gate[3]{U"))
    reasons = refused(document, f"part 1, step 2, item 1: {kind} item")
    assert "not understood" in reasons[0]
    assert r"\gate[3]{U" in reasons[0]


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        ("dots", "variable number"),
        ("dots_gate", "named by a label beside control dots"),
        ("control", "nothing joined"),
        ("discard", "partial trace"),
        ("sign", "sign drawn on a wire"),
        ("phase", "diag(1, e^{i theta})"),
        ("sep", "drawn equality in the middle"),
        ("mystery", "does not know"),
        ("", "does not know"),
    ],
)
def test_items_that_cannot_be_simulated_are_refused(kind, text):
    refused(doc(item(kind, "A", [1])), text)


@pytest.mark.parametrize(
    "label",
    [r"U_1", r"U", "R(\\theta)", r"R_\phi", r"\hat{U}_t(\theta_t/2)", "CNOT", "Toffoli", "foo"],
)
def test_unknown_and_ambiguous_labels_are_refused_with_the_label(label):
    refused(doc(gate(label, 1)), "unknown gate label")
    reasons = reasons_of(doc(gate(label, 1)))
    assert reasons[0].startswith("part 1, step 1, item 1: unknown gate label")


def test_a_classically_controlled_item_is_refused():
    for built in (
        item("gate", "X", [2], [1], classical=True),
        item("x", "X", [2], [1], classical=True),
        item("measure", "", [1], classical=True),
        item("barrier", "", [1], classical=True),
    ):
        refused(doc(built), "classically controlled")


def test_an_item_with_drawn_text_beside_it_is_refused():
    refused(doc(item("gate", "H", [1], source=r"\dstick{a}")), "carries drawn text")
    refused(doc(item("swap", "", [1, 2], source="x")), "carries drawn text")


def test_malformed_items_are_refused_not_crashed_on():
    refused(doc("H"), "an item must be an object")
    refused(doc(item(kind=None)), "needs a text 'kind'")
    refused(doc(item("gate", None, [1])), "needs a text 'kind' and a text 'label'")
    refused(doc(item("gate", "H", [1], source=3)), "'source' must be text")
    refused(doc(item("gate", "H", [1], classical="no")), "'classical' true or false")
    for field in ("targets", "controls", "open_controls"):
        for bad in ("1", [1.5], [True], [None], [[1]], {"a": 1}):
            refused(doc(item("gate", "H", [1]) | {field: bad}), f"{field} must be a list")


@pytest.mark.parametrize("wire", [0, 3, -1, 10**30])
def test_a_wire_out_of_range_is_refused(wire):
    refused(doc(gate("H", wire)), f"wire {wire} is not in 1..2")
    refused(doc(gate("H", 1, controls=(wire,))), "is not in 1..2")
    refused(doc(item("x", "X", [wire])), "is not in 1..2")


@pytest.mark.parametrize(
    "label", ["iSWAP", r"\sqrt{SWAP}", r"\mathrm{iSWAP}", "S", "X", "CZ", "swap"]
)
def test_a_swap_item_with_another_label_is_refused(label):
    refused(doc(item("swap", label, [1, 2])), f"swap with label {label!r}")


@pytest.mark.parametrize("label", ["S", "T", "CZ", "H", "z", r"\sqrt{Z}", "R_k"])
def test_control_dots_with_another_label_are_refused(label):
    refused(doc(item("z_dots", label, (), [1, 2])), f"control dots with label {label!r}")


def test_control_dots_with_a_target_are_refused():
    # targets [3] with controls [1, 2] was read as CZ on wires 1 and 2: the 3 was dropped
    refused(doc(item("z_dots", "Z", [3], [1, 2]), wires=3), "and 1 target(s)")
    refused(doc(item("z_dots", "", [1], (), [2]), wires=3), "and 1 target(s)")
    assert read_ops(item("z_dots", "", (), [1, 2])) == (Op("z", (2,), controls=(1,)),)
    assert read_ops(item("z_dots", "Z", (), [1, 2])) == (Op("z", (2,), controls=(1,)),)


def test_a_swap_item_with_its_own_label_is_read():
    for label in ("", "SWAP"):
        assert read_ops(item("swap", label, [1, 2])) == (Op("swap", (1, 2)),)


@pytest.mark.parametrize("notation", ["quantikz", "qcircuit", "tikzcd"])
@pytest.mark.parametrize("label", ["H", r"R_z(\theta)", "R_{k=12}", r"\sqrt{X}", "T^\\dagger"])
def test_a_one_qubit_label_on_a_box_over_several_wires_is_refused_when_drawn(notation, label):
    # \gate[2]{H} is one gate on these wires; H on each is a guess
    document = doc(gate(label, 1, 2), kind=notation)
    (reason,) = reasons_of(document)
    assert (
        reason.startswith("part 1, step 1, item 1: gate ") and f"in a {notation} drawing" in reason
    )
    assert "one box over 2 wires" in reason
    # with a control, and for a single wire, the box is a gate as before
    refused(doc(gate("H", 2, 3, controls=(1,)), wires=3, kind=notation), "one box over 2 wires")
    assert read_ops(gate("H", 2), kind=notation)[0].gate == "h"


@pytest.mark.parametrize(
    "notation", ["Quantikz", "quantikz ", " QUANTIKZ", "Qcircuit", "tikz-cd", "TikZ-cd", "tikz_cd"]
)
def test_the_notation_is_compared_without_case_spaces_hyphens_or_underscores(notation):
    refused(doc(gate("H", 1, 2), kind=notation), "one box over 2 wires")


@pytest.mark.parametrize("notation", ["yquant", "quantum-sim", "", None])
def test_a_one_qubit_label_on_several_wires_is_that_gate_on_each_elsewhere(notation):
    # yquant `h a, b` and a hand-written item say so
    more = {} if notation is None else {"kind": notation}
    assert read_ops(gate("H", 1, 2), **more) == (Op("h", (1,)), Op("h", (2,)))


@pytest.mark.parametrize("notation", ["quantikz", "qcircuit", "tikzcd"])
def test_a_drawn_box_over_the_wires_of_a_two_wire_gate_is_read(notation):
    document = doc(gate("CZ", 1, 2), gate(r"R_{zz}(1)", 1, 2), kind=notation)
    assert len(from_gate_list(document).circuits[0].ops) == 2
    x = doc(item("x", "X", [1, 2]), kind=notation)  # \targ on two wires: X on each
    assert len(from_gate_list(x).circuits[0].ops) == 2


def test_the_item_shapes_are_checked():
    refused(doc(gate("H")), "has no target wire")
    refused(doc(gate("SWAP", 1)), "acts on 2 wires, the item has 1")
    refused(doc(gate("CZ", 1, 2, 3), wires=3), "acts on 2 wires, the item has 3")
    refused(doc(gate(r"R_{zz}(1)", 1, 2, 3), wires=3), "acts on 2 wires, the item has 3")
    refused(doc(item("x", "Y", [1])), "x item with label 'Y'")
    refused(doc(item("x", "X", [])), "1 target(s)".replace("1", "0"))
    refused(doc(item("swap", "", [1])), "a swap needs two wires")
    refused(doc(item("swap", "", [1, 2, 3]), wires=3), "a swap needs two wires")
    refused(doc(item("z_dots", "Z", (), [1])), "at least two wires")
    refused(doc(item("z_dots", "Z", (), (), [1, 2])), "all open")
    refused(doc(item("measure", "X", [1])), "computational-basis")
    refused(doc(item("measure", "", [1], [2])), "computational-basis")
    refused(doc(item("measure", "", [])), "computational-basis")
    refused(doc(item("barrier", "", [1], [2])), "a barrier with controls")
    refused(doc(item("text", "U", [1])), "is not a gate and not a ket")
    refused(doc(item("text", "", [2], [1])), "a text item with controls")
    refused(doc(item("text", "", [2], (), [1])), "a text item with controls")
    refused(doc(item("text", r"\;=\;", [1])), "is not a gate and not a ket")


def test_a_wire_named_twice_in_one_item_is_refused():
    # the identity would be read for H on [1, 1], and for X on [2, 2] with a control
    place = "part 1, step 1, item 1: wire 1 is named twice in this item"
    assert reasons_of(doc(gate("H", 1, 1))) == (place,)
    assert reasons_of(doc(gate("H", 1, controls=(1,)))) == (place,)
    assert reasons_of(doc(gate("H", 1, open_controls=(1,)))) == (place,)
    assert reasons_of(doc(item("x", "X", [1], [1]))) == (place,)
    assert reasons_of(doc(item("x", "X", [2, 2], [1]))) == (place.replace("wire 1", "wire 2"),)
    assert reasons_of(doc(item("x", "X", [2], [1], [1]))) == (place.replace("wire 2", "wire 1"),)
    assert reasons_of(doc(item("swap", "", [1, 1]))) == (place,)
    assert reasons_of(doc(item("measure", "", [1, 1]))) == (place,)
    assert reasons_of(doc(item("z_dots", "Z", (), [1, 1]))) == (place,)
    assert reasons_of(doc(gate("CZ", 1, 2, controls=(2,)))) == (place.replace("wire 1", "wire 2"),)


def test_two_items_of_a_step_on_one_wire_are_refused():
    # [H on 1, X on 1] in one step would be read as X*H from the order of the list
    both = [gate("H", 1), item("x", "X", [1])]
    place = "part 1, step 1, item 2: wire 1 is also used by item 1 of this step"
    (reason,) = reasons_of(doc(both))
    assert reason.startswith(place) and "which comes first" in reason
    refused(doc([gate("H", 1), item("x", "X", [2], [1])]), "wire 1 is also used by item 1")
    refused(doc([item("x", "X", [3], [1]), item("x", "X", [2], [3])], wires=3), "wire 3 is also")
    refused(doc([item("measure", "", [1]), gate("H", 1)]), "wire 1 is also used by item 1")
    refused(doc([item("x", "X", [3], [1]), item("x", "X", [4], [1])], wires=4), "wire 1 is also")
    assert reasons_of(doc([gate("H", 1), gate("H", 2), item("x", "X", [2], [1])]))[0].startswith(
        "part 1, step 1, item 3: wire 1 is also used by item 1"
    )


def test_items_of_a_step_on_different_wires_and_neutral_items_are_read():
    ops = read_ops([gate("H", 1), gate("H", 2), item("barrier", "", [1]), item("text", "", [2])])
    assert ops == (Op("h", (1,)), Op("h", (2,)))
    assert read_ops([gate("H", 1)], [gate("X", 1)]) == (Op("h", (1,)), Op("x", (1,)))
    wide = [item("x", "X", [3], [1]), gate("Z", 2)]
    assert read_ops(wide, wires=3) == (Op("x", (3,), controls=(1,)), Op("z", (2,)))


def test_an_operation_after_the_measurement_of_its_wire_is_refused():
    refused(doc(item("measure", "", [1]), gate("H", 1)), "used after its measurement")
    refused(doc(item("measure", "", [1]), item("x", "X", [2], [1])), "used after its measurement")
    assert read_ops(item("measure", "", [1]), gate("H", 2))[1] == Op("h", (2,))


@pytest.mark.parametrize(
    "legend",
    [
        r"1 = \qwbundle{n}",
        r"1 = \ket{0}^{\otimes n}",
        r"1 = {/^n}",
        r"1 = \vdots",
        r"1 = \cdots",
        r"1 = \dots",
        r"1 = \cw",
        r"1 = \cwx",
        r"1 = \setwiretype{c}",
        r"1 = cbit",
        r"1 = \bundle",
        "1 = q, bundle n",  # the words docingest writes for \qwbundle{n}
        "1 = bundle",
        "1 = dots (more wires, not drawn)",  # and for a row of dots
    ],
)
def test_a_wire_legend_that_shows_a_register_or_classical_wire_is_refused(legend):
    refused(doc(gate("H", 1), wire_labels=[legend, "2"]), "wire 1: the legend")


def test_wire_legends_are_checked_for_shape():
    refused(doc(gate("H", 1), wire_labels=["1"]), "wire_labels: 1 legends for 2 wires")
    refused(doc(gate("H", 1), wire_labels="12"), "wire_labels: must be a list of text")
    refused(doc(gate("H", 1), wire_labels=[1, 2]), "wire_labels: must be a list of text")
    refused(doc(gate("H", 1), wire_labels=["1 = \\cwidth", "2"]), "input legend not read")


def test_a_later_part_must_start_with_its_sign():
    document = doc(gate("H", 1))
    document["parts"].append([[gate("X", 1)]])
    refused(document, "part 2: does not start with the drawn sign")
    document["parts"][1] = []
    refused(document, "part 2: does not start with the drawn sign")
    document["parts"][1] = [[item("sep", "=", ()), gate("X", 1)]]
    refused(document, "part 2: does not start with the drawn sign")
    document["parts"][1] = [[item("sep", None)], [gate("X", 1)]]
    refused(document, "part 2: does not start with the drawn sign")


def test_every_cause_is_listed_not_only_the_first():
    document = doc(
        gate("U_1", 1),
        item("dots", "", [1]),
        item("unparsed", "", (), source="?"),
        gate("CNOT", 1, 2),
    )
    reasons = reasons_of(document)
    assert [r.split(":")[0] for r in reasons] == [
        "part 1, step 1, item 1",
        "part 1, step 2, item 1",
        "part 1, step 3, item 1",
        "part 1, step 4, item 1",
    ]


def test_a_refusal_lists_at_most_max_reasons_and_says_there_may_be_more():
    reasons = reasons_of(doc(*[gate("U_1", 1) for _ in range(MAX_REASONS + 7)]))
    assert len(reasons) == MAX_REASONS + 1
    assert reasons[-1] == f"... reading stopped after {MAX_REASONS} causes; there may be more"
    error = RefusedError(["a", "b"])
    assert error.reasons == ("a", "b") and str(error) == "a; b"


def test_size_limits_are_checked_before_anything_is_read():
    many = [[gate("H", 1)] for _ in range(MAX_ITEMS)]
    refused({"format": "circuit/1", "wires": 1, "parts": [many]}, f"more than {MAX_ITEMS:,} items")
    steps = [[gate("H", 1)] for _ in range(MAX_OPS + 1)]
    refused(
        {"format": "circuit/1", "wires": 1, "parts": [steps]}, f"more than {MAX_OPS:,} operations"
    )
    exact = [[gate("H", 1)] for _ in range(MAX_OPS)]
    assert (
        len(from_gate_list({"format": "circuit/1", "wires": 1, "parts": [exact]}).circuits[0].ops)
        == MAX_OPS
    )
    wide = {"format": "circuit/1", "wires": 128, "parts": [[[gate("H", *range(1, 129))]]]}
    assert len(from_gate_list(wide).circuits[0].ops) == 128


def test_refusals_are_the_only_failure():
    for bad in (
        {"format": "circuit/1", "wires": 2, "parts": [[[gate("H", 1)]]], "wire_labels": [None, 1]},
        {"format": "circuit/1", "wires": 2, "parts": [[[{"kind": "gate", "targets": "ab"}]]]},
    ):
        with pytest.raises(RefusedError):
            from_gate_list(bad)


# --- the writer ---------------------------------------------------------------------------------


def test_the_writer_makes_a_circuit_1_document():
    circuit = Circuit(
        3,
        (
            Op("h", (1,)),
            Op("x", (2,), controls=(1,), open_controls=(3,)),
            Op("swap", (1, 3), controls=(2,)),
            Op("ry", (1,), params=(PI / 2,)),
            Op("measure", (2,)),
        ),
    )
    written = to_gate_list(circuit)
    assert written["format"] == "circuit/1" and written["wires"] == 3
    assert written["status"] == "parsed" and written["wire_labels"] == ["1", "2", "3"]
    steps = written["parts"][0]  # type: ignore[index]
    assert len(steps) == 5 and all(len(step) == 1 for step in steps)
    assert [step[0]["kind"] for step in steps] == ["gate", "x", "swap", "gate", "measure"]
    assert steps[3][0]["label"] == f"R_y({PI / 2!r})"
    assert json.loads(json.dumps(written)) == written


def test_the_writer_refuses_an_invalid_circuit():
    with pytest.raises(Exception, match="unknown gate"):
        to_gate_list(Circuit(1, (Op("nope", (1,)),)))


@st.composite
def circuits(draw):
    n = draw(st.integers(1, 5))
    ops = []
    angle = st.floats(allow_nan=False, allow_infinity=False, width=64)
    for _ in range(draw(st.integers(0, 10))):
        gate_name = draw(
            st.sampled_from(
                [
                    *["id", "h", "x", "y", "z", "s", "sdg", "t", "tdg", "sx", "sxdg"],
                    *["rx", "ry", "rz", "p", "rk", "rxx", "ryy", "rzz", "swap", "iswap"],
                ]
            )
        )
        two = gate_name in ("rxx", "ryy", "rzz", "swap", "iswap")
        if two and n < 2:
            continue
        wires = draw(st.permutations(range(1, n + 1)))
        k = 2 if two else 1
        free = wires[k:]
        n_controls = draw(st.integers(0, len(free)))
        n_closed = draw(st.integers(0, n_controls))
        params = ()
        if gate_name == "rk":
            params = (draw(st.integers(1, 40)),)
        elif gate_name in ("rx", "ry", "rz", "p", "rxx", "ryy", "rzz"):
            params = (draw(angle),)
        ops.append(
            Op(
                gate_name,
                tuple(wires[:k]),
                controls=tuple(free[:n_closed]),
                open_controls=tuple(free[n_closed:n_controls]),
                params=params,
            )
        )
    return Circuit(n, tuple(ops))


@settings(max_examples=400, deadline=None)
@given(circuits())
def test_write_then_read_gives_the_same_circuit(circuit):
    written = json.loads(json.dumps(to_gate_list(circuit)))
    reading = from_gate_list(written)
    assert reading.circuits[0].bind() == circuit
    assert reading.circuits[0].n_wires == circuit.n_wires


def test_write_then_read_with_measurements_at_the_end():
    circuit = Circuit(2, (Op("h", (1,)), Op("measure", (1,)), Op("measure", (2,))))
    assert from_gate_list(to_gate_list(circuit)).circuits[0].bind() == circuit


@pytest.mark.parametrize(
    "angle", [5e-324, 1e-300, 2.5e-10, 1e16, 1.7976931348623157e308, -0.0, 0.1]
)
def test_extreme_angles_survive_the_round_trip(angle):
    circuit = Circuit(1, (Op("rz", (1,), params=(angle,)),))
    document = json.loads(json.dumps(to_gate_list(circuit)))
    assert from_gate_list(document).circuits[0].bind() == circuit


def test_parametric_circuits_come_from_circuits():
    circuit = Circuit(2, (Op("h", (1,)), Op("rx", (2,), controls=(1,), params=(0.5,))))
    parametric = ParametricCircuit.from_circuit(circuit)
    assert parametric.bind() == circuit and parametric.symbols == ()


@settings(max_examples=400, deadline=None)
@given(
    st.recursive(
        st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=8),
        lambda children: (
            st.lists(children, max_size=4)
            | st.dictionaries(
                st.sampled_from(
                    [
                        *["format", "wires", "parts", "kind", "label", "targets", "controls"],
                        *["status", "open_controls", "classical", "source", "wire_labels"],
                    ]
                ),
                children,
                max_size=6,
            )
        ),
        max_leaves=30,
    )
)
def test_any_json_gives_a_reading_or_a_refusal(data):
    try:
        reading = from_gate_list(data)
    except RefusedError as error:
        assert error.reasons  # noqa: PT017  (a property test: the refusal always has a reason)
        return
    assert reading.circuits


KINDS = ["gate", "x", "z_dots", "swap", "measure", "barrier", "text", "dots", "sep", "weird"]
LABELS = ["H", "X", "Z", "CZ", "SWAP", r"R_y(\pi/2)", r"R_z(\theta)", "U_1", "CNOT", "", "Z"]


@settings(max_examples=400, deadline=None)
@given(
    st.lists(
        st.lists(
            st.fixed_dictionaries(
                {
                    "kind": st.sampled_from(KINDS),
                    "label": st.sampled_from(LABELS),
                    "targets": st.lists(st.integers(-1, 4), max_size=3),
                    "controls": st.lists(st.integers(-1, 4), max_size=3),
                    "open_controls": st.lists(st.integers(-1, 4), max_size=2),
                    "classical": st.booleans() | st.just(False),
                    "source": st.sampled_from(["", "", "x"]),
                }
            ),
            max_size=3,
        ),
        max_size=5,
    )
)
def test_random_items_give_a_reading_that_simulates_or_a_refusal(steps):
    try:
        reading = from_gate_list({"format": "circuit/1", "wires": 3, "parts": [steps]})
    except RefusedError:
        return
    circuit = reading.circuits[0].bind({"theta": 0.3})
    statevector(circuit)  # a reading is always something the engine accepts


# --- keys the format does not have --------------------------------------------------------------


def bell(**on_cnot):
    """H on wire 1 and a CNOT; the CNOT item has the extra keys in ``on_cnot``."""
    return doc(gate("H", 1), item("x", "X", [2], controls=[1]) | on_cnot)


@pytest.mark.parametrize(
    "extra",
    [
        {"control": [1]},
        {"ctrls": [1]},
        {"condition": "c=1"},
        {"classical_controls": [2]},
        {"basis": "X"},
        {"dagger": True},
        {"power": 0},
        {"repeat": 2},
    ],
)
def test_an_item_key_the_format_does_not_have_is_refused_and_not_dropped(extra):
    # a misspelled "controls" used to leave an X with no control
    (key,) = extra
    (reason,) = reasons_of(doc(item("x", "X", [2], **extra)))
    assert reason.startswith(f"part 1, step 1, item 1: unknown key '{key}'")
    assert "the keys of an item are ['classical', 'controls'" in reason


def test_a_bell_circuit_with_a_misspelled_controls_key_is_refused_with_the_place_of_the_item():
    del_controls = item("x", "X", [2], ctrls=[1])
    del del_controls["controls"]
    (reason,) = reasons_of(doc(gate("H", 1), del_controls))
    assert reason.startswith("part 1, step 2, item 1: unknown key 'ctrls'")
    assert from_gate_list(bell()).circuits[0].bind().ops[1].controls == (1,)


def test_the_first_unknown_key_in_order_is_the_one_reported():
    (reason,) = reasons_of(doc(item("x", "X", [2], zeta=1, alpha=2)))
    assert "unknown key 'alpha'" in reason and "zeta" not in reason


def test_a_key_that_is_not_text_is_reported_too():
    broken = item("x", "X", [2])
    broken[7] = 1  # a Python dict can hold any key
    assert "unknown key '7'" in reasons_of(doc(broken))[0]


@pytest.mark.parametrize("key", ["input", "initial_state", "state", "values", "unitary", "extra"])
def test_a_document_key_the_format_does_not_have_is_refused(key):
    # {"format": "circuit/1", ..., "input": "1"} used to run from |0>
    (reason,) = reasons_of(doc(gate("H", 1), **{key: "1"}))
    assert reason.startswith(f"unknown key '{key}'; the keys of a circuit/1 document are [")
    assert ("request that holds the circuit" in reason) == (key in ("input", "values", "unitary"))


def test_an_unknown_document_key_is_not_reported_before_the_status_or_format():
    assert "not 'parsed'" in reasons_of({"format": "circuit/1", "status": "unparsed", "x": 1})[0]
    assert "expected 'circuit/1'" in reasons_of({"format": "circuit/2", "x": 1})[0]


def test_an_unknown_key_in_the_item_that_opens_a_part_is_refused():
    document = doc(gate("X", 1))
    document["parts"].append([[item("sep", "=", sign="equal")], [gate("X", 1)]])
    (reason,) = reasons_of(document)
    assert reason.startswith("part 2: unknown key 'sign'")


def test_every_key_of_the_format_is_still_read():
    document = {
        "format": "circuit/1",
        "kind": "quantikz",
        "status": "parsed",
        "label": "",
        "wires": 2,
        "wire_labels": ["1", "2"],
        "parts": [[[gate("H", 1)], [item("x", "X", [2], controls=[1])]]],
    }
    assert len(from_gate_list(document).circuits[0].ops) == 2


# --- the grammar is closed -----------------------------------------------------------------------


def test_legends_are_not_read_when_their_number_is_wrong():
    # which legend is on which wire is unknown, so the count is the one cause
    legends = [r"1 = \bra{1}", r"2 = \ket{0}", "3"]
    (reason,) = reasons_of(doc(gate("H", 1), wire_labels=legends))
    assert reason == "wire_labels: 3 legends for 2 wires"


@pytest.mark.parametrize("label", [r"\Proj{1}", r"\Pi_1", "P_1", "M_0", r"\bra{1}", r"|1\rangle"])
def test_a_gate_label_that_is_a_projector_or_an_outcome_is_unknown_and_refused(label):
    # gate labels are read from a closed table: an unknown one is refused, never taken as harmless
    refused(doc(gate(label, 1)), "unknown gate label")


LEGEND_PIECES = [
    "1",
    "0",
    " = ",
    ", ",
    " ",
    "output ",
    "a",
    "q_0",
    "{",
    "}",
    "$",
    "'",
    r"\ket{0}",
    r"\ket{1}",
    r"\ket",
    "|",
    r"\rangle",
    r"\left",
    r"\right",
    r"\vert",
    r"\lvert",
    r"\text{",
    r"\mathrm{",
    r"\bra{1}",
    r"\langle ",
    r"\Proj{1}",
    r"\kb{1}{1}",
    "<",
    ">",
    "postselect",
    "\u27e8",
    "\u03c8",
    "\u00a0",
    r"\\",
]
ALLOWED_COMMANDS = {"ket", "left", "right", "lvert", "vert", "rangle", "text", "mathrm"}


@settings(max_examples=2000, deadline=None)
@given(st.lists(st.sampled_from(LEGEND_PIECES), max_size=12).map("".join))
def test_a_legend_that_is_read_holds_only_names_and_basis_kets_and_nothing_after_output(legend):
    try:
        from_gate_list(doc(gate("H", 2), wire_labels=[legend, "2"]))
    except RefusedError:
        return
    # the check is not the reader's own: no bar, bracket or macro is left that a bra could be
    # written with, outside a ket |0> or |1>, and nothing but a name after the word output
    assert legend.isascii() and not set("<>").intersection(legend)
    assert set(re.findall(r"\\([A-Za-z]+|.)", legend)) <= ALLOWED_COMMANDS
    without_kets = re.sub(r"\|\s*[01]\s*\\rangle", "", legend)
    assert "|" not in without_kets and "rangle" not in without_kets
    after = legend.partition("output")[2]
    assert re.fullmatch(r"[A-Za-z0-9 _{}$',()+-]*", after), legend


@settings(max_examples=1000, deadline=None)
@given(st.lists(st.sampled_from(LEGEND_PIECES), max_size=8).map("".join))
def test_a_bra_put_anywhere_in_a_legend_refuses_the_circuit(legend):
    for bra in (r"\bra{1}", r"\langle 1|", r"\kb{1}{1}", r"<1\vert", "\u27e81\u2225"):
        for text in (f"1 = {legend}{bra}", f"1 = {bra}{legend}", f"1 = a, output {legend}{bra}"):
            with pytest.raises(RefusedError):
                from_gate_list(doc(gate("H", 2), wire_labels=[text, "2"]))


# --- reading stops when the list of causes is full -----------------------------------------------


def counting_reads(monkeypatch):
    calls: list[int] = []
    real = formats._item_ops

    def counted(*args):
        calls.append(1)
        return real(*args)

    monkeypatch.setattr(formats, "_item_ops", counted)
    return calls


def test_reading_stops_once_max_reasons_causes_are_listed(monkeypatch):
    calls = counting_reads(monkeypatch)
    reasons = reasons_of(doc(*[gate("U_1", 1) for _ in range(MAX_REASONS * 40)]))
    assert len(calls) == MAX_REASONS
    assert len(reasons) == MAX_REASONS + 1
    assert reasons[-1] == f"... reading stopped after {MAX_REASONS} causes; there may be more"


def test_nothing_is_said_about_more_when_the_last_item_is_the_last_cause(monkeypatch):
    calls = counting_reads(monkeypatch)
    reasons = reasons_of(doc(*[gate("U_1", 1) for _ in range(MAX_REASONS)]))
    assert len(calls) == MAX_REASONS and len(reasons) == MAX_REASONS
    assert not any(reason.startswith("...") for reason in reasons)


def test_reading_stops_between_parts_too(monkeypatch):
    calls = counting_reads(monkeypatch)
    full = [[gate("U_1", 1)] for _ in range(MAX_REASONS)]
    document = doc(*full)
    document["parts"].append([[item("sep", "=")], [gate("U_1", 1)]])
    document["parts"].append([[item("sep", "=")], [gate("U_1", 1)]])
    reasons = reasons_of(document)
    assert len(calls) == MAX_REASONS
    assert reasons[-1].startswith("... reading stopped")


def test_causes_of_the_legends_count_before_the_first_item_is_read(monkeypatch):
    calls = counting_reads(monkeypatch)
    legends = [rf"{n} = \ket{{+}}, output 1" for n in range(1, 21)]  # two causes each
    reasons = reasons_of(doc(gate("H", 1), wires=20, wire_labels=legends))
    assert len(calls) == 0 and len(reasons) == MAX_REASONS + 1
    assert all(reason.startswith("wire ") for reason in reasons[:-1])


def test_a_valid_document_is_read_to_the_end(monkeypatch):
    calls = counting_reads(monkeypatch)
    from_gate_list(doc(*[gate("H", 1) for _ in range(MAX_REASONS * 4)]))
    assert len(calls) == MAX_REASONS * 4
