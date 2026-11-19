"""The kets that docingest lets stand on a wire, against the samples its copy is tested on too.

docingest keeps a copy of this grammar (``_BASIS_KET`` in docingest.domain.circuits) to decide which
text on a wire it may leave in a gate list: empty, or a basis ket |0> or |1>. quantum_sim must never
import docingest, and docingest's domain must never import quantum_sim (only its QASM adapter
does), so the two copies cannot be compared
directly. Both packages are tested against the one list in ``tests/fixtures/basis_kets.json``: a
change to either grammar that moves a verdict fails the test of that package until the list and the
other grammar follow.
"""

import json
from pathlib import Path

import pytest

from quantum_sim.formats import _basis_ket

SAMPLES = json.loads(
    (Path(__file__).parent / "fixtures" / "basis_kets.json").read_text(encoding="utf-8")
)


def test_the_samples_hold_kets_and_non_kets():
    assert sum(s["ket"] for s in SAMPLES) >= 12
    assert sum(not s["ket"] for s in SAMPLES) >= 30


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: repr(s["text"]))
def test_a_sample_is_a_basis_ket_as_the_list_says(sample):
    assert (_basis_ket(sample["text"]) is not None) is sample["ket"]
