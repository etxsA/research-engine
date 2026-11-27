"""CircuitExporter contract: what ``QasmExportService`` relies on from every exporter.

The service narrows the "matches no symbol" warning with ``unmatched`` and names the circuits
refused for lack of a value with ``unbound``, so an exporter must report both as the port says.
"""

from __future__ import annotations

import pytest

from docingest.adapters.qasm import QuantumSimExporter, export_document
from docingest.domain.circuits import CircuitSteps, gate_list
from docingest.ports import CircuitExporter, QasmExport

H = "\\begin{quantikz}\n& \\gate{H} & \\qw\n\\end{quantikz}"
THETA = "\\begin{quantikz}\n& \\gate{R_y(\\theta)} & \\qw\n\\end{quantikz}"
TWO = "\\begin{quantikz}\n& \\gate{R_y(\\theta)} & \\gate{R_z(\\phi)} & \\qw\n\\end{quantikz}"


def circuit(source: str) -> CircuitSteps:
    [steps] = gate_list(source).circuits
    return steps


@pytest.fixture(params=[QuantumSimExporter], ids=["quantum-sim"])
def exporter(request) -> CircuitExporter:
    return request.param()


def test_an_exporter_is_a_circuit_exporter(exporter):
    assert isinstance(exporter, CircuitExporter)


def test_an_ok_export_has_programs_and_no_reasons(exporter):
    export = exporter.export(circuit(H), origin="a.md, block 1, circuit 1")
    assert isinstance(export, QasmExport)
    assert export.status == "ok" and export.ok and export.reasons == ()
    [program] = export.programs
    assert program.part == 1 and "h q[0];" in program.text
    assert export.unbound == ()


def test_a_key_that_matches_no_symbol_comes_back_in_unmatched(exporter):
    export = exporter.export(circuit(THETA), values={"theta": "0.5", "\\phi": "1"})
    assert export.status == "ok"
    assert export.unmatched == ("\\phi",)
    assert exporter.export(circuit(THETA), values={"\\theta": "0.5"}).unmatched == ()


def test_the_symbols_without_a_value_of_a_refused_export_are_unbound(exporter):
    export = exporter.export(circuit(TWO), values={"\\theta": "0.5"})
    assert export.status == "refused" and export.programs == ()
    assert export.symbols == ("phi", "theta")
    assert export.unbound == ("phi",)


ONE_INPUT_THETA = (
    "\\begin{quantikz}\n\\lstick{\\ket{1}} & \\gate{R_y(\\theta)} & \\qw\n\\end{quantikz}"
)
THETA_RZZ = (
    "\\begin{quantikz}\n& \\gate{R_y(\\theta)} & \\gate[2]{R_{zz}(0.5)} & \\qw \\\\\n"
    "& \\qw & & \\qw\n\\end{quantikz}"
)
BAD_AT_THE_VALUE = (
    "\\begin{quantikz}\n& \\gate{R_y(\\frac{1}{\\theta})} & \\gate{R_z(\\phi)} & \\qw\n"
    "\\end{quantikz}"
)


@pytest.mark.parametrize("source", [ONE_INPUT_THETA, THETA_RZZ])
def test_a_circuit_refused_for_another_reason_has_no_unbound_symbols(exporter, source):
    # a value for theta would not export it: unbound names a symbol only when values for the
    # unbound symbols would make the circuit export
    export = exporter.export(circuit(source))
    assert export.status == "refused" and export.symbols == ("theta",)
    assert export.unbound == ()


def test_a_bound_value_that_breaks_the_circuit_leaves_no_unbound_symbols(exporter):
    # theta = 0 divides by zero: a value for phi alone would not export the circuit
    export = exporter.export(circuit(BAD_AT_THE_VALUE), values={"theta": "0"})
    assert export.status == "refused" and export.unbound == ()
    assert exporter.export(circuit(BAD_AT_THE_VALUE), values={"theta": "2"}).unbound == ("phi",)


def gate(angle: str) -> str:
    return "\\begin{quantikz}\n& \\gate{R_y(" + angle + ")} & \\qw\n\\end{quantikz}"


@pytest.mark.parametrize(
    ("angle", "unbound"),
    [
        ("\\frac{1}{\\theta - \\phi}", ("phi", "theta")),
        ("\\frac{1}{\\theta + \\phi - 2\\psi}", ("phi", "psi", "theta")),
        ("\\frac{1}{\\theta - 1}", ("theta",)),
        ("\\frac{\\theta}{\\phi}", ("phi", "theta")),
    ],
)
def test_symbols_that_only_lack_a_value_are_unbound_even_when_their_difference_divides(
    exporter, angle, unbound
):
    # every unbound symbol gets its own placeholder, so theta - phi is not zero at them
    export = exporter.export(circuit(gate(angle)))
    assert export.status == "refused"
    assert export.unbound == unbound


def test_a_circuit_singular_at_every_placeholder_only_loses_the_hint(exporter):
    # sqrt(theta - 1) needs theta >= 1, and the placeholders are below 1: no hint, never a false
    # one; theta = 2 exports it
    export = exporter.export(circuit(gate("\\sqrt{\\theta - 1}")))
    assert export.status == "refused" and export.unbound == ()
    assert exporter.export(circuit(gate("\\sqrt{\\theta - 1}")), values={"theta": "2"}).ok


@pytest.mark.parametrize(
    ("angle", "values", "unbound"),
    [
        ("\\theta_{12}", None, ("theta_{12}",)),
        ("x_{ab}", None, ("x_{ab}",)),
        ("\\theta_{12} + \\phi", {"phi": "1"}, ("theta_{12}",)),
        ("\\theta_{1} + \\theta_{12}", {"\\theta_{12}": "1"}, ("theta_{1}",)),
    ],
)
def test_a_symbol_with_a_subscript_of_several_characters_keeps_the_hint(
    exporter, angle, values, unbound
):
    # the reader names theta_12, which --set refuses; unbound gives the binding key theta_{12}
    export = exporter.export(circuit(gate(angle)), values=values)
    assert export.status == "refused"
    assert export.unbound == unbound
    for name in export.unbound:
        exporter.check_values({name: "1"})
    filled = {**(values or {}), **dict.fromkeys(export.unbound, "1")}
    assert exporter.export(circuit(gate(angle)), values=filled).ok


def test_every_one_of_many_unbound_symbols_gets_its_own_placeholder(exporter):
    # 25 symbols, sorted x_01 ... x_25; 1/(x_23 - x_24) is singular if two placeholders are
    # equal, and 1/(x_23 - x_24 + 1) is singular at no placeholders
    names = [f"x_{{{n:02d}}}" for n in range(1, 26)]
    gates = " & ".join(f"\\gate{{R_y({name})}}" for name in names)
    apart = "\\gate{R_y(\\frac{1}{x_{23} - x_{24}})}"
    last = "\\gate{R_y(\\frac{1}{x_{23} - x_{24} + 1})}"
    source = f"\\begin{{quantikz}}\n& {gates} & {apart} & {last} & \\qw\n\\end{{quantikz}}"
    export = exporter.export(circuit(source))
    assert export.status == "refused"
    assert export.unbound == tuple(names)


def test_a_key_that_is_not_one_symbol_is_a_value_error(exporter):
    exporter.check_values({"theta": "pi/4", "\\theta_{1}": "1"})
    with pytest.raises(ValueError, match="is not a parameter name"):
        exporter.check_values({"thetta": "1"})


@pytest.mark.parametrize("source", [H, THETA, TWO])
@pytest.mark.parametrize("values", [None, {"theta": "0.5"}])
def test_export_document_gives_what_export_gives_for_the_same_circuit(source, values):
    """The public function is the exporter's own, so a caller holding a document gets one answer."""
    steps = circuit(source)
    args = {"values": values, "origin": "a.md, block 1, circuit 1", "tool": "docingest"}
    assert export_document(steps.to_dict(), **args) == QuantumSimExporter().export(steps, **args)
