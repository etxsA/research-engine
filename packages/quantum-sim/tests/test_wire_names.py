"""The grammar of a wire name, against the samples that docingest's copy is tested on too.

docingest keeps a copy of this grammar (``_WIRE_NAME`` in docingest.domain.circuits) to decide which
marks of a drawing it may skip. quantum_sim must never import docingest, and docingest's domain
must never import quantum_sim (only its QASM adapter does), so the two copies cannot be compared
directly. Both packages are tested against the one list in ``tests/fixtures/wire_names.json``: a
change to either grammar that moves a verdict fails the test of that package until the list and
the other grammar follow.
"""

import json
from pathlib import Path

import pytest

from quantum_sim.formats import _SYMBOL

SAMPLES = json.loads(
    (Path(__file__).parent / "fixtures" / "wire_names.json").read_text(encoding="utf-8")
)


def test_the_samples_hold_names_and_non_names():
    assert sum(s["name"] for s in SAMPLES) >= 20
    assert sum(not s["name"] for s in SAMPLES) >= 40


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: repr(s["text"]))
def test_a_sample_is_a_wire_name_as_the_list_says(sample):
    assert (_SYMBOL.fullmatch(sample["text"]) is not None) is sample["name"]
