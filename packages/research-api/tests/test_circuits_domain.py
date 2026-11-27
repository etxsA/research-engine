from __future__ import annotations

import json

import pytest
from docingest.application.qasm import _filename as docingest_filename
from quantum_sim.angles import MAX_CHARS as ANGLE_MAX_CHARS

from research_api.domain.circuits import (
    MAX_STATE_WIRES,
    MAX_UNITARY_WIRES,
    MAX_VALUE_CHARS,
    CircuitCaps,
    ensure_paper_within_caps,
    ensure_within_caps,
    operation_count,
    paper_origin,
    pasted_qasm_filename,
    qasm_filename,
    read_json_strict,
    values_pointer,
    wire_count,
)
from research_api.domain.errors import BadRequestError, InvalidInputError


def circuit(wires: int = 2, ops: int = 2) -> dict[str, object]:
    steps = [[{"kind": "gate", "label": "H", "targets": [1]}] for _ in range(ops)]
    return {"format": "circuit/1", "wires": wires, "parts": [steps]}


def test_json_is_read_like_json() -> None:
    assert read_json_strict('{"a": [1, 2.5, "x", null, true], "b": {"c": {}}}', what="x") == {
        "a": [1, 2.5, "x", None, True],
        "b": {"c": {}},
    }


def test_bytes_are_read_too() -> None:
    assert read_json_strict(b'{"a": 1}', what="x") == {"a": 1}


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1, "a": 2}',
        '{"a": {"b": 1, "b": 1}}',
        '[{"k": 1}, {"j": 1, "j": 2}]',
        '{"a": 1, "\\u0061": 2}',  # the same key spelled with an escape
    ],
)
def test_a_key_written_twice_is_refused_wherever_it_is(text: str) -> None:
    with pytest.raises(BadRequestError, match="twice"):
        read_json_strict(text, what="circuit_json")


def test_the_message_names_the_input_and_the_key() -> None:
    with pytest.raises(BadRequestError) as stop:
        read_json_strict('{"controls": [1], "controls": [2]}', what="circuit_json")
    assert stop.value.detail.startswith("circuit_json gives the key 'controls' twice")


def test_a_very_long_key_is_not_echoed_in_full() -> None:
    key = "k" * 5000
    with pytest.raises(BadRequestError) as stop:
        read_json_strict(json.dumps({key: 1}).replace("}", f', "{key}": 2}}'), what="x")
    assert len(stop.value.detail) < 300


@pytest.mark.parametrize("text", ["NaN", "[Infinity]", '{"a": -Infinity}'])
def test_constants_json_does_not_have_are_refused(text: str) -> None:
    with pytest.raises(BadRequestError, match="does not allow"):
        read_json_strict(text, what="x")


@pytest.mark.parametrize("text", ["", "{", '{"a": }', "nope", "1" * 5000])
def test_text_that_is_not_json_is_refused_without_echoing_it(text: str) -> None:
    with pytest.raises(BadRequestError, match="not valid JSON") as stop:
        read_json_strict(text, what="circuit_json")
    assert text[:20] not in stop.value.detail or text == ""


def test_text_that_is_not_utf8_is_refused() -> None:
    with pytest.raises(BadRequestError, match="not valid JSON"):
        read_json_strict(b"\xff\xfe", what="x")


def test_a_document_nested_too_deeply_is_refused() -> None:
    with pytest.raises(BadRequestError, match="nested too deeply"):
        read_json_strict("[" * 100_000, what="x")


def test_wire_count_reads_integers_only() -> None:
    assert wire_count({"wires": 3}) == 3
    for document in ({"wires": True}, {"wires": "3"}, {"wires": 2.0}, {}, [], "x", None):
        assert wire_count(document) is None


def test_operations_are_counted_per_target() -> None:
    document = {
        "parts": [
            [
                [{"kind": "gate", "targets": [1, 2, 3]}, {"kind": "x", "targets": [1]}],
                [{"kind": "z_dots", "controls": [1, 2]}, {"kind": "measure", "targets": []}],
            ],
            [[{"kind": "swap", "targets": [1, 2]}]],
        ]
    }
    # gate 3, x 1, z_dots 1, a measure with no target 1, a swap 2: a count that errs high
    assert operation_count(document) == 3 + 1 + 1 + 1 + 2


def test_barriers_and_text_cost_nothing() -> None:
    step = [{"kind": "barrier", "targets": [1, 2]}, {"kind": "text", "targets": [1]}]
    assert operation_count({"parts": [[step]]}) == 0


@pytest.mark.parametrize(
    "document",
    [None, [], "x", {}, {"parts": 3}, {"parts": [3]}, {"parts": [[3]]}, {"parts": [[[3, None]]]}],
)
def test_a_document_that_is_not_shaped_like_a_circuit_counts_as_nothing(document: object) -> None:
    assert operation_count(document) == 0


CAPS = CircuitCaps(max_ops=5)


def test_a_circuit_inside_the_caps_passes() -> None:
    ensure_within_caps([("/c", circuit(MAX_STATE_WIRES, 5))], CAPS, max_wires=MAX_STATE_WIRES)


def test_too_many_wires_is_a_422_that_names_the_field() -> None:
    with pytest.raises(InvalidInputError, match="13 wires; this check takes at most 12") as stop:
        ensure_within_caps([("/c", circuit(13))], CAPS, max_wires=MAX_STATE_WIRES)
    assert [e.path for e in stop.value.errors] == ["/c"]
    assert "quantum-sim" in stop.value.detail


def test_the_unitary_limit_is_smaller() -> None:
    ensure_within_caps([("/c", circuit(MAX_UNITARY_WIRES))], CAPS, max_wires=MAX_UNITARY_WIRES)
    with pytest.raises(InvalidInputError, match="9 wires"):
        ensure_within_caps([("/c", circuit(9))], CAPS, max_wires=MAX_UNITARY_WIRES)


def test_the_second_circuit_is_checked_and_named() -> None:
    with pytest.raises(InvalidInputError) as stop:
        ensure_within_caps([("/a", circuit(2)), ("/b", circuit(20))], CAPS, max_wires=12)
    assert [e.path for e in stop.value.errors] == ["/b"]


def test_too_many_operations_is_a_422() -> None:
    with pytest.raises(InvalidInputError, match="6 operations; this endpoint takes at most 5"):
        ensure_within_caps([("/c", circuit(2, 6))], CAPS, max_wires=12)


def test_operations_are_added_up_over_the_circuits() -> None:
    with pytest.raises(InvalidInputError, match="6 operations"):
        ensure_within_caps([("/a", circuit(2, 3)), ("/b", circuit(2, 3))], CAPS, max_wires=12)


def test_a_document_that_is_not_a_circuit_is_left_to_the_simulator() -> None:
    ensure_within_caps([("/c", [1, 2, 3])], CAPS, max_wires=12)


# --- the OpenQASM 3 export -------------------------------------------------------------------


def test_the_export_has_no_wire_cap_but_keeps_the_operation_cap() -> None:
    ensure_within_caps([("/c", circuit(40, 5))], CAPS, max_wires=None)
    with pytest.raises(InvalidInputError, match="6 operations") as stop:
        ensure_within_caps([("/c", circuit(40, 6))], CAPS, max_wires=None)
    assert [e.path for e in stop.value.errors] == ["/c"]
    assert "quantum-sim" in stop.value.detail


def test_the_circuits_of_a_paper_share_one_operation_budget() -> None:
    ensure_paper_within_caps([circuit(2, 3), circuit(2, 2)], CAPS)
    with pytest.raises(InvalidInputError) as stop:
        ensure_paper_within_caps([circuit(2, 3), circuit(2, 3)], CAPS)
    assert stop.value.detail.startswith(
        "The paper's circuits ask for 6 operations; this endpoint takes at most 5."
    )
    assert "`docingest circuits export --doc`" in stop.value.detail
    assert stop.value.errors == ()


def test_a_paper_has_no_wire_cap_and_a_paper_with_no_circuit_passes() -> None:
    ensure_paper_within_caps([circuit(40, 5)], CAPS)
    ensure_paper_within_caps([], CAPS)


@pytest.mark.parametrize(
    ("block", "circuit_no", "part"), [(1, 1, 1), (2, 10, 3), (12, 1, 2), (100, 7, 100)]
)
def test_a_papers_program_is_named_as_docingest_names_it(
    block: int, circuit_no: int, part: int
) -> None:
    doc_id = "0123456789abcdef" + "f" * 48
    name = qasm_filename(doc_id, block, circuit_no, part)
    assert name == docingest_filename(doc_id, block, circuit_no, part)
    assert name.startswith("0123456789abcdef-b") and name.endswith(".qasm")


def test_the_file_names_are_zero_padded_to_two_digits() -> None:
    assert qasm_filename("a" * 64, 1, 2, 3) == "aaaaaaaaaaaaaaaa-b01-c02-p03.qasm"
    assert pasted_qasm_filename(1) == "circuit-p01.qasm"
    assert pasted_qasm_filename(12) == "circuit-p12.qasm"


def test_the_source_line_of_a_paper_names_the_file_block_and_circuit() -> None:
    assert paper_origin("paper.tex", 2, 1) == "paper.tex, block 2, circuit 1"


@pytest.mark.parametrize(
    ("key", "pointer"),
    [
        ("theta", "/values/theta"),
        ("\\theta_{1}", "/values/\\theta_{1}"),
        ("a/b~c", "/values/a~1b~0c"),
    ],
)
def test_a_values_key_is_pointed_at_with_json_pointer_escapes(key: str, pointer: str) -> None:
    assert values_pointer(key) == pointer


def test_a_value_is_cut_at_the_length_the_angle_reader_accepts() -> None:
    # The API refuses a key or a value longer than this before reading it; the reader refuses a
    # longer text anyway, so the two limits must not drift apart.
    assert MAX_VALUE_CHARS == ANGLE_MAX_CHARS
