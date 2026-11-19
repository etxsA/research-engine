r"""docingest's copy of the wire-name grammar gives the same verdicts as quantum-sim's.

``_WIRE_NAME`` decides which marks of a quantikz drawing are skipped (a ``\wire`` that only names
a wire). It copies quantum-sim's ``_SYMBOL``. The domain never imports quantum_sim (only
``docingest.adapters.qasm`` does), so the two are
tested against one list of samples, kept in quantum-sim's tests
(packages/quantum-sim/tests/fixtures/wire_names.json) and run by
packages/quantum-sim/tests/test_wire_names.py against its own grammar. The folder is only read.
"""

import json
from pathlib import Path

import pytest

from docingest.domain.circuits import _WIRE_NAME, _plain_wire_text

SAMPLES_FILE = (
    Path(__file__).resolve().parents[3] / "quantum-sim" / "tests" / "fixtures" / "wire_names.json"
)
SAMPLES = json.loads(SAMPLES_FILE.read_text(encoding="utf-8"))


def test_the_samples_are_where_quantum_sim_keeps_them():
    assert sum(s["name"] for s in SAMPLES) >= 20
    assert sum(not s["name"] for s in SAMPLES) >= 40


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: repr(s["text"]))
def test_a_sample_is_a_wire_name_as_the_list_says(sample):
    assert (_WIRE_NAME.fullmatch(sample["text"]) is not None) is sample["name"]


@pytest.mark.parametrize(
    "word", ["omitted", "\\text{omitted}", "\\mathrm{dots}", "\\text{n times}"]
)
def test_a_word_is_never_a_wire_name_even_in_text_or_mathrm(word):
    assert not _plain_wire_text(word)
    assert not _plain_wire_text(f"${word}$")


@pytest.mark.parametrize(
    ("text", "plain"),
    [
        ("", True),
        ("  ", True),
        ("q", True),
        ("$q_{1}$", True),
        ("{q}", True),
        ("{$q$}", True),
        ("$$", True),  # empty between dollar signs
        ("$q", False),  # the dollar sign does not close
        ("q$", False),
        ("{a}_{b}", False),
        ("$\\times n$", False),
        ("\\mathrm{c1}", False),
    ],
)
def test_the_dollar_signs_and_braces_around_a_name_are_the_only_wrapping(text, plain):
    assert _plain_wire_text(text) is plain
