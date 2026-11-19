"""The circuit/1 documents saved for quantum-sim are still what ``gate_list`` returns.

quantum-sim reads ``CircuitSteps.to_dict()`` without importing docingest, so its bridge tests
(packages/quantum-sim/tests/test_bridge.py) use documents saved in
packages/quantum-sim/tests/fixtures/bridge/, one ``NAME.json`` for each ``NAME.tex`` source.
This test is the other half of that bridge: it makes the documents again from the sources and
fails when docingest's output has changed. When it does, run
``uv run --package docingest python packages/quantum-sim/tools/make_bridge_fixtures.py`` and look
at the diff. The folder is only read; this test does not import quantum_sim (in docingest only
``docingest.adapters.qasm`` does).
"""

import json
import random
from pathlib import Path

import pytest
from test_circuit_consumption import TEXT_TOKENS, UNKNOWN_COMMANDS, circuits_of, rebuilt, with_token

from docingest.domain.circuits import CIRCUIT_FORMAT, gate_list

FIXTURES = Path(__file__).resolve().parents[3] / "quantum-sim" / "tests" / "fixtures" / "bridge"
SOURCES = sorted(FIXTURES.glob("*.tex"))


def test_the_fixture_folder_is_where_quantum_sim_keeps_it():
    assert len(SOURCES) >= 22, FIXTURES


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.stem)
def test_the_saved_document_is_what_gate_list_returns(source):
    found = gate_list(source.read_text(encoding="utf-8"))
    # a partial_* source is a drawing with a left-out part, which quantum-sim has to refuse
    expected = "partial" if source.stem.startswith("partial_") else "parsed"
    assert found.status == expected and len(found.circuits) == 1
    saved = json.loads(source.with_suffix(".json").read_text(encoding="utf-8"))
    assert saved["format"] == CIRCUIT_FORMAT
    assert found.circuits[0].to_dict() == saved


@pytest.mark.parametrize("source", SOURCES, ids=lambda p: p.stem)
@pytest.mark.parametrize("tokens", [UNKNOWN_COMMANDS, TEXT_TOKENS], ids=["command", "text"])
def test_an_unknown_command_or_a_text_token_in_a_saved_source_is_never_parsed(source, tokens):
    # the consumption check on the sources quantum-sim keeps: whatever the reader does not
    # understand, put anywhere between the tokens of a cell, can not leave the status parsed
    rng = random.Random(f"{source.stem}{tokens[0]}")
    for kind, body, options in circuits_of(source.read_text(encoding="utf-8")):
        for _ in range(6):
            injected = with_token(kind, body, rng.choice(tokens), rng)
            assert gate_list(rebuilt(kind, injected, options)).status != "parsed", injected


@pytest.mark.parametrize(
    "source", [p for p in SOURCES if not p.stem.startswith("partial_")], ids=lambda p: p.stem
)
def test_text_outside_a_saved_source_makes_it_partial_and_a_math_wrapper_does_not(source):
    # the consumption check covers the whole block: a power or a word beside the circuit is
    # unparsed, and the delimiters of math around it are not
    text = source.read_text(encoding="utf-8")
    plain = gate_list(text)
    assert plain.status == "parsed"
    for around in (f"${text}$", f"\\[{text}\\]", f"\\begin{{center}}{text}\\end{{center}}"):
        assert gate_list(around).circuits == plain.circuits, around
    for outside in ("^{\\otimes n}", "^{\\times 3}", " repeated n times", "\\def\\x{y}"):
        found = gate_list(text + outside)
        assert found.status == "partial", outside
        assert "outside the circuit" in found.text
