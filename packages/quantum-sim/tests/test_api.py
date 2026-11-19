"""``run`` and ``check``: the result format, symbols, targets, limits, and that bad input is a
refusal and never an exception."""

import json
import math

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quantum_sim import CONVENTIONS, RESULT_FORMAT, check, run
from quantum_sim.api import decode, encode

S2 = 1 / math.sqrt(2)


def item(kind, label="", targets=(), controls=(), open_controls=()):
    return {
        "kind": kind,
        "label": label,
        "targets": list(targets),
        "controls": list(controls),
        "open_controls": list(open_controls),
        "classical": False,
        "source": "",
    }


def circuit(wires, *items, parts=None):
    steps = [[i] for i in items]
    return {"format": "circuit/1", "wires": wires, "parts": parts or [steps]}


BELL = circuit(2, item("gate", "H", [1]), item("x", "X", [2], [1]))
HZH = circuit(
    1,
    item("gate", "H", [1]),
    item("gate", "Z", [1]),
    item("gate", "H", [1]),
)


def amplitudes(entry):
    return np.array([complex(re, im) for re, im in entry["state"]])


# --- run ----------------------------------------------------------------------------------------


def test_run_a_bell_pair():
    result = run(BELL)
    assert result["format"] == RESULT_FORMAT and result["command"] == "run"
    assert result["status"] == "ok" and result["refused"] == [] and result["unbound"] == []
    assert result["conventions"] == dict(CONVENTIONS)
    assert result["input"] == "00" and result["equal_to_target"] is None
    (entry,) = result["circuits"]
    assert np.allclose(amplitudes(entry), [S2, 0, 0, S2])
    assert entry["probabilities"] == {"wires": [1, 2], "values": [0.5, 0.0, 0.0, 0.5]}
    assert entry["unitary"] is None and entry["measured_wires"] == [] and entry["sign"] is None
    json.dumps(result, allow_nan=False)


def test_run_lists_the_unitary_when_asked():
    result = run(BELL, unitary=True)
    matrix = np.array(
        [[complex(re, im) for re, im in row] for row in result["circuits"][0]["unitary"]]
    )
    cnot_h = np.array([[1, 0, 1, 0], [0, 1, 0, 1], [0, 1, 0, -1], [1, 0, -1, 0]]) * S2
    assert np.allclose(matrix, cnot_h)
    assert run({"circuit": BELL, "unitary": True})["circuits"][0]["unitary"] is not None
    assert run({"circuit": BELL, "unitary": True}, unitary=False)["circuits"][0]["unitary"] is None


@pytest.mark.parametrize("wanted", ["no", "yes", "false", 1, 0, 2.5, None, [], {}, [True]])
def test_a_unitary_request_that_is_not_true_or_false_is_refused(wanted):
    result = run({"circuit": BELL, "unitary": wanted})
    assert result["status"] == "refused"
    assert any("'unitary' must be true or false" in r for r in result["refused"])
    assert run({"circuit": BELL, "unitary": False})["status"] == "ok"


@pytest.mark.parametrize("flag", [True, False])
def test_a_bad_unitary_key_is_refused_also_when_the_keyword_overrides_it(flag):
    result = run({"circuit": BELL, "unitary": "no"}, unitary=flag)
    assert result["status"] == "refused"
    assert any("'unitary' must be true or false" in r for r in result["refused"])


@pytest.mark.parametrize("bad", [5, "x", [1], True])
def test_a_bad_values_key_is_refused_also_when_the_keyword_overrides_it(bad):
    result = run({"circuit": BELL, "values": bad}, values={})
    assert result["status"] == "refused"
    assert any("'values' must map" in r for r in result["refused"])
    assert run({"circuit": BELL, "values": {}}, values={})["status"] == "ok"


def test_a_value_for_an_unknown_symbol_in_the_request_is_refused_when_the_keyword_overrides():
    result = run({"circuit": BELL, "values": {"theta": 1}}, values={})
    assert result["status"] == "refused"
    assert any("no parameter named 'theta'" in r for r in result["refused"])


@pytest.mark.parametrize(
    "label", [r"R_y(\theta^{(0)})", r"R_y(\theta^{(1)})", r"R_y(\theta^{(2)})"]
)
def test_a_layer_index_in_parentheses_is_not_run_as_a_power(label):
    # R_y(\theta^{(0)}) used to be R_y(1 radian) whatever the value of theta
    result = run(circuit(1, item("gate", label, [1])), values={"theta": 0.3})
    assert result["status"] == "refused" and result["circuits"] == []
    assert any("a superscript in parentheses is an index" in r for r in result["refused"])
    assert result["parameters"] == []


@pytest.mark.parametrize("bad", ["2", "000", "01", 5, [1, 0, 0]])
def test_a_bad_input_key_is_refused_also_when_the_keyword_overrides_it(bad):
    result = run({"circuit": HZH, "input": bad}, input_state="0")
    assert result["status"] == "refused"
    assert result["circuits"] == []


def test_the_keyword_input_wins_over_a_good_input_key():
    result = run({"circuit": HZH, "input": "0"}, input_state="1")
    assert result["status"] == "ok" and result["input"] == "1"


def test_a_keyword_that_is_not_true_or_false_is_refused():
    result = run(BELL, unitary="yes")  # type: ignore[arg-type]
    assert result["status"] == "refused"
    assert any("'unitary' must be true or false" in r for r in result["refused"])


def test_a_text_item_without_a_label_is_run_and_checked_not_raised():
    text = {"kind": "text"}
    unlabelled = {"format": "circuit/1", "wires": 2, "parts": [[[text], [item("gate", "H", [1])]]]}
    ran = run(unlabelled)
    assert ran["status"] == "ok"
    assert ran["circuits"][0]["probabilities"]["values"] == pytest.approx([0.5, 0, 0.5, 0])
    assert check({"a": unlabelled, "b": circuit(2, item("gate", "H", [1]))})["status"] == "ok"


def test_run_takes_an_input_label_or_amplitudes():
    assert run(BELL, input_state="10")["input"] == "10"
    from_ten = amplitudes(run(BELL, input_state="10")["circuits"][0])
    assert np.allclose(from_ten, [S2, 0, 0, -S2])  # (|00> - |11>) / sqrt 2
    assert run({"circuit": BELL, "input": "01"})["input"] == "01"
    hadamard = circuit(1, item("gate", "H", [1]))
    plus = run(hadamard, input_state=[S2, S2])
    assert np.allclose(amplitudes(plus["circuits"][0]), [1, 0])
    assert plus["input"] == [[0.707106781187, 0.0], [0.707106781187, 0.0]]
    flipped = run(circuit(1, item("gate", "X", [1])), input_state=[[0, 0], [1, 0]])
    assert np.allclose(amplitudes(flipped["circuits"][0]), [1, 0])  # X|1> = |0>
    assert flipped["input"] == [[0.0, 0.0], [1.0, 0.0]]
    imaginary = run(circuit(1, item("gate", "X", [1])), input_state=[[0, 0], [0, 1]])
    assert np.allclose(amplitudes(imaginary["circuits"][0]), [1j, 0])


def test_run_refuses_bad_inputs_with_a_reason():
    for state, text in (
        ("11", "not a basis label"),
        ("ab", "not a basis label"),
        ([1, 0, 0], "expected 2 amplitudes"),
        ([1, 1], "norm"),
        ([[1, 2, 3], [0, 0, 0]], "pair"),
        ([True, False], "number or a [re, im] pair"),
        ("", "not a basis label"),
        ([], "non-empty"),
        ([1, "a"], "number or a [re, im] pair"),
    ):
        result = run(circuit(1, item("gate", "H", [1])), input_state=state)
        assert result["status"] == "refused", state
        assert text in " ".join(result["refused"]), (state, result["refused"])


def test_a_circuit_with_a_measurement_reports_the_measured_wires():
    measured = circuit(2, item("gate", "H", [1]), item("measure", "", [1]))
    entry = run(measured)["circuits"][0]
    assert entry["measured_wires"] == [1]
    assert entry["probabilities"] == {"wires": [1], "values": [0.5, 0.5]}
    refused_unitary = run(measured, unitary=True)
    assert refused_unitary["status"] == "ok"
    assert refused_unitary["circuits"][0]["unitary"] is None
    assert "no unitary" in refused_unitary["notes"][0]


def test_a_drawn_equality_is_simulated_part_by_part():
    document = circuit(
        1,
        parts=[
            [[item("gate", "H", [1])], [item("gate", "Z", [1])], [item("gate", "H", [1])]],
            [[item("sep", "=")], [item("gate", "X", [1])]],
        ],
    )
    result = run(document)
    assert [c["part"] for c in result["circuits"]] == [1, 2]
    assert [c["sign"] for c in result["circuits"]] == [None, "="]
    assert np.allclose(amplitudes(result["circuits"][0]), amplitudes(result["circuits"][1]))


def test_symbols_are_reported_until_they_have_values():
    document = circuit(2, item("gate", r"R_y(\theta)", [1]), item("gate", r"R_z(2\phi_1)", [2]))
    result = run(document)
    assert result["status"] == "symbolic"
    assert result["parameters"] == ["phi_1", "theta"] and result["unbound"] == ["phi_1", "theta"]
    assert result["circuits"] == [
        {"part": 1, "wires": 2, "operations": 2, "parameters": ["phi_1", "theta"]}
    ]
    half = run(document, values={"theta": 1.0})
    assert half["status"] == "symbolic" and half["unbound"] == ["phi_1"]
    done = run(document, values={"theta": r"\pi/2", "phi_1": 0.25})
    assert done["status"] == "ok" and done["values"] == {"theta": PI_2, "phi_1": 0.25}
    assert np.allclose(np.abs(amplitudes(done["circuits"][0]))[[0, 2]], [S2, S2])


PI_2 = math.pi / 2


@pytest.mark.parametrize("text", ["pi/4", r"\pi/4", "pi / 4", r"\frac{pi}{4}"])
def test_a_value_may_write_pi_without_the_backslash_as_the_export_does(text):
    document = circuit(1, item("gate", r"R_y(\theta)", [1]))
    result = run(document, values={"theta": text})
    assert result["status"] == "ok", result["refused"]
    assert result["values"] == {"theta": math.pi / 4}
    target = circuit(1, item("gate", r"R_y(\pi/4)", [1]))
    checked = check({"a": document, "b": target}, values={"theta": text})
    assert checked["status"] == "ok" and checked["equal_to_target"] is True


def test_pi_inside_a_longer_word_is_still_refused_as_a_value():
    document = circuit(1, item("gate", r"R_y(\theta)", [1]))
    result = run(document, values={"theta": "pie/4"})
    assert result["status"] == "refused"
    assert "the letters 'pie' in a row" in " ".join(result["refused"])


def test_values_accept_names_with_a_backslash_and_braces_and_check_them():
    document = circuit(1, item("gate", r"R_y(\alpha_1)", [1]))
    assert run(document, values={r"\alpha_{1}": 0.5})["status"] == "ok"
    assert run(document, values={"alpha_1": 0.5})["values"] == {"alpha_1": 0.5}
    for values, text in (
        ({"beta": 1}, "no parameter named 'beta' (the circuit has: alpha_1)"),
        ({"alpha_1": "x"}, "free symbols"),
        ({"alpha_1": True}, "must be a number"),
        ({"alpha_1": None}, "must be a number"),
        ({"alpha_1": [1]}, "must be a number"),
        ({"alpha_1": math.inf}, "not finite"),
        ({"alpha_1": math.nan}, "not finite"),
        ({"alpha_1": "1/0"}, "division by zero"),
        ([("alpha_1", 1)], "must map"),
        ("alpha_1=1", "must map"),
    ):
        result = run(document, values=values)
        assert result["status"] == "refused", values
        assert text in " ".join(result["refused"]), (values, result["refused"])
    assert "none" in run(BELL, values={"x": 1})["refused"][0]


@pytest.mark.parametrize(
    "keys",
    [
        ("theta", r"\theta"),
        (r"\theta", "theta"),
        ("theta", "{theta}"),
        ("theta", r"{\theta}"),
        ("theta_1", "theta_{1}"),
        (r"\theta_{1}", r"\theta_1"),
    ],
)
def test_one_symbol_given_two_values_under_two_spellings_is_refused(keys):
    # the keys differ for JSON, the name is the same: which value is meant is not known
    name = "theta_1" if "_" in keys[0] else "theta"
    document = circuit(1, item("gate", rf"R_x(\{name})", [1]))
    values = {keys[0]: 0, keys[1]: r"\pi"}
    for result in (
        run(document, values=values),
        run({"circuit": document, "values": values}),
        check({"a": document, "b": document, "values": values}),
        check({"a": document, "b": document}, values=values),
    ):
        assert result["status"] == "refused"
        (reason,) = result["refused"]
        assert f"values give {name!r} twice" in reason
        assert repr(keys[0]) in reason and repr(keys[1]) in reason


@pytest.mark.parametrize("key", ["the{ta}", r"\\theta", "thetta", "pi", r"\pi", "e", "x y"])
def test_a_values_key_that_is_not_one_symbol_is_refused(key):
    # the{ta} and a doubled backslash used to bind theta: a name is one symbol of the grammar
    document = circuit(1, item("gate", r"R_x(\theta)", [1]))
    values = {key: 0}
    for result in (
        run(document, values=values),
        run({"circuit": document, "values": values}),
        check({"a": document, "b": document}, values=values),
    ):
        assert result["status"] == "refused"
        (reason,) = result["refused"]
        assert reason == (
            f"{key!r} is not a parameter name: write one symbol, such as theta, \\theta or "
            "\\theta_{1}"
        )


@pytest.mark.parametrize("key", ["", "  "])
def test_an_empty_values_key_is_refused_as_the_export_refuses_it(key):
    result = run(circuit(1, item("gate", r"R_x(\theta)", [1])), values={key: 0})
    assert result["refused"] == ["a parameter binding has an empty name"]


def test_two_symbols_and_the_keyword_over_the_request_are_still_fine():
    two = circuit(1, item("gate", r"R_x(\theta)", [1]), item("gate", r"R_y(\phi)", [1]))
    assert run(two, values={"theta": 0, r"\phi": 1})["status"] == "ok"
    one = circuit(1, item("gate", r"R_x(\theta)", [1]))
    overridden = run({"circuit": one, "values": {"theta": 0}}, values={r"\theta": 1})
    assert overridden["status"] == "ok" and overridden["values"] == {"theta": 1.0}


def test_values_that_break_an_expression_are_a_refusal():
    document = circuit(1, item("gate", r"R_y(1/\theta)", [1]))
    result = run(document, values={"theta": 0})
    assert result["status"] == "refused"
    assert result["refused"] == ["part 1: division by zero"]


def test_a_refused_document_gives_the_reasons():
    document = circuit(2, item("gate", "U_1", [1]), item("dots", "", [1]))
    result = run(document)
    assert result["status"] == "refused" and result["circuits"] == []
    assert len(result["refused"]) == 2 and "unknown gate label 'U_1'" in result["refused"][0]
    assert result["conventions"] == dict(CONVENTIONS)


def test_requests_that_are_not_requests_are_refused():
    for request, text in (
        (None, "must be a JSON object"),
        ([], "must be a JSON object"),
        ("circuit", "must be a JSON object"),
        ({}, "needs a 'circuit'"),
        ({"circuit": BELL, "inputt": "00"}, "unknown key 'inputt'"),
        ({"circuit": None}, "must be an object"),
        ({"format": "circuit/9"}, "expected 'circuit/1'"),
    ):
        result = run(request)
        assert result["status"] == "refused", request
        assert text in " ".join(result["refused"]), (request, result["refused"])


def test_wide_circuits_are_listed_by_their_probabilities():
    wide = circuit(13, item("gate", "H", [1]), item("x", "X", [13], [1]))
    result = run(wide, unitary=True)
    entry = result["circuits"][0]
    assert entry["state"] is None and entry["unitary"] is None
    assert result["input"] == "0" * 13
    assert entry["probabilities"]["top"] == [["0" * 13, 0.5], ["1000000000001", 0.5]]
    assert entry["probabilities"]["truncated"] is False
    assert any("state of 13 wires is not listed" in n for n in result["notes"])
    many = run(circuit(13, *[item("gate", "H", [w]) for w in range(1, 8)]))
    top = many["circuits"][0]["probabilities"]
    assert len(top["top"]) == 64 and top["truncated"] is True
    nine = run(circuit(9, item("gate", "H", [1])), unitary=True)
    assert nine["circuits"][0]["unitary"] is None
    assert any("unitary of 9 wires is not listed" in n for n in nine["notes"])


def test_the_wire_limit_is_checked_before_anything_is_made():
    for wires in (21, 200):
        result = run(circuit(wires, item("gate", "H", [1])))
        assert result["status"] == "refused"
        assert result["refused"] == [f"{wires} wires; the limit is 20"]
    huge = run(circuit(10**400, item("gate", "H", [1])))
    assert huge["refused"] == ["1" + "0" * 36 + "... wires; the limit is 20"]
    assert len(huge["refused"][0]) < 70
    big = run(circuit(20, item("gate", "H", [1])), input_state="1" * 20)
    assert big["status"] == "ok"
    eleven = run(circuit(11, item("gate", "H", [1])), unitary=True)
    assert eleven["status"] == "ok" and eleven["circuits"][0]["unitary"] is None


def test_a_legend_note_travels_with_the_result():
    document = circuit(1, item("gate", "H", [1])) | {"wire_labels": ["1 = \\ket{1}"]}
    result = run(document)
    assert result["status"] == "ok" and "not read as an input state" in result["notes"][0]
    assert result["input"] == "0"


# --- check --------------------------------------------------------------------------------------


def test_check_two_circuits():
    x_gate = circuit(1, item("gate", "X", [1]))
    result = check({"a": HZH, "b": x_gate})
    assert result["status"] == "ok" and result["equal_to_target"] is True
    (comparison,) = result["comparisons"]
    assert comparison["between"] == ["a", "b"] and comparison["equal"] and comparison["exact"]
    assert comparison["method"] == "unitary" and comparison["max_deviation"] < 1e-12
    different = check({"a": HZH, "b": circuit(1, item("gate", "Y", [1]))})
    assert different["status"] == "ok" and different["equal_to_target"] is False
    assert different["comparisons"][0]["equal"] is False


def test_check_says_that_an_input_is_not_used_by_a_comparison_of_circuits():
    x_gate = circuit(1, item("gate", "X", [1]))
    assert check({"a": HZH, "b": x_gate})["notes"] == []
    drawn = circuit(
        1,
        parts=[[[item("gate", "X", [1])]], [[item("sep", "=")], [item("gate", "X", [1])]]],
    )
    for request in ({"a": HZH, "b": x_gate, "input": "1"}, {"circuit": drawn, "input": "1"}):
        result = check(request)
        assert result["status"] == "ok"
        (note,) = result["notes"]
        assert note.startswith("input is not used")
    # against a unitary the input is not used either
    unitary = check({"circuit": x_gate, "target": {"unitary": [[0, 1], [1, 0]]}, "input": "1"})
    assert unitary["equal_to_target"] is True
    (note,) = unitary["notes"]
    assert note.startswith("input is not used")
    assert check({"circuit": x_gate, "target": {"unitary": [[0, 1], [1, 0]]}})["notes"] == []
    # against a state the input is the one compared, so it is used and no note says otherwise
    state = check({"circuit": x_gate, "target": {"state": "0"}, "input": "1"})
    assert state["notes"] == [] and state["equal_to_target"] is True


def test_check_up_to_global_phase_is_not_exact():
    rz = circuit(1, item("gate", r"R_z(\pi)", [1]))
    z = circuit(1, item("gate", "Z", [1]))
    comparison = check({"a": rz, "b": z})["comparisons"][0]
    assert comparison["equal"] is True and comparison["exact"] is False
    assert comparison["phase"] == pytest.approx(-math.pi / 2)


def test_check_a_drawn_equality():
    document = circuit(
        1,
        parts=[
            [[item("gate", "H", [1])], [item("gate", "Z", [1])], [item("gate", "H", [1])]],
            [[item("sep", "=")], [item("gate", "X", [1])]],
            [[item("sep", r"\to")], [item("gate", "Y", [1])]],
        ],
    )
    result = check(document)
    assert [c["between"] for c in result["comparisons"]] == [
        ["part 1", "part 2"],
        ["part 2", "part 3"],
    ]
    assert [c["equal"] for c in result["comparisons"]] == [True, False]
    assert result["equal_to_target"] is False
    assert any(r"'\\to'" in n or "\\to" in n for n in result["notes"])
    only = check({"circuit": document})
    assert only["status"] == "ok"


def test_check_a_circuit_against_a_unitary():
    cnot = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]]
    cnot_circuit = circuit(2, item("x", "X", [2], [1]))
    result = check({"circuit": cnot_circuit, "target": {"unitary": cnot}})
    assert result["equal_to_target"] is True
    pairs = [[[v, 0] for v in row] for row in cnot]
    assert check({"circuit": cnot_circuit, "target": {"unitary": pairs}})["equal_to_target"] is True
    times_i = [[[0, v] for v in row] for row in cnot]  # i * CNOT, equal up to global phase
    phase_result = check({"circuit": cnot_circuit, "target": {"unitary": times_i}})
    assert phase_result["equal_to_target"] is True
    assert phase_result["comparisons"][0]["exact"] is False
    assert phase_result["comparisons"][0]["phase"] == pytest.approx(-math.pi / 2)
    wrong = [[0, 1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    assert (
        check({"circuit": cnot_circuit, "target": {"unitary": wrong}})["equal_to_target"] is False
    )


def test_check_refuses_a_target_matrix_that_is_not_unitary():
    cnot_circuit = circuit(2, item("x", "X", [2], [1]))
    scaled = [[2, 0, 0, 0], [0, 2, 0, 0], [0, 0, 0, 2], [0, 0, 2, 0]]  # 2 * CNOT
    projector = [[1, 0, 0, 0]] + [[0, 0, 0, 0]] * 3
    off = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 1e-6]]
    for matrix in (scaled, projector, off):
        result = check({"circuit": cnot_circuit, "target": {"unitary": matrix}})
        assert result["status"] == "refused" and result["equal_to_target"] is None
        assert (
            "the target matrix is not unitary: U†U differs from the identity by "
            in (result["refused"][0])
        )
    assert "by 3" in check({"circuit": cnot_circuit, "target": {"unitary": scaled}})["refused"][0]
    # within the tolerance of the engine's checks, the matrix is compared as before
    near = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 1e-12]]
    assert check({"circuit": cnot_circuit, "target": {"unitary": near}})["status"] == "ok"
    # a matrix of the wrong size is refused for its size, not for being unitary
    assert (
        "2 x 2"
        in check({"circuit": HZH, "target": {"unitary": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}})[
            "refused"
        ][0]
    )


def test_check_a_circuit_with_measurements_against_a_state_is_refused_like_two_circuits():
    measure_only = circuit(2, item("measure", "", [1, 2]))
    bell_measured = circuit(
        2, item("gate", "H", [1]), item("x", "X", [2], [1]), item("measure", "", [1])
    )
    for measured in (measure_only, bell_measured):
        for target in ({"state": "00"}, {"state": [1, 0, 0, 0]}):
            result = check({"circuit": measured, "target": target})
            assert result["status"] == "refused" and result["equal_to_target"] is None
            assert "measures wire(s) [1" in result["refused"][0]
        assert check({"a": measured, "b": measured})["status"] == "refused"
        assert check({"circuit": measured, "target": {"unitary": [[1, 0], [0, 1]]}})["status"] == (
            "refused"
        )
    # unmeasured, the same circuits are compared
    assert check({"circuit": BELL, "target": {"state": [S2, 0, 0, S2]}})["status"] == "ok"
    # and run still reports a measured circuit's state and probabilities
    assert run(measure_only)["status"] == "ok"


def test_check_a_circuit_against_a_state():
    result = check({"circuit": BELL, "target": {"state": [S2, 0, 0, S2]}})
    assert result["equal_to_target"] is True and result["input"] == "00"
    assert result["comparisons"][0]["between"] == ["circuit", "state"]
    assert (
        check({"circuit": BELL, "target": {"state": [S2, 0, 0, -S2]}})["equal_to_target"] is False
    )
    times_i = [[0, S2], [0, 0], [0, 0], [0, S2]]
    assert check({"circuit": BELL, "target": {"state": times_i}})["equal_to_target"] is True
    from_ten = check({"circuit": BELL, "input": "10", "target": {"state": [S2, 0, 0, -S2]}})
    assert from_ten["equal_to_target"] is True and from_ten["input"] == "10"
    assert (
        check({"circuit": circuit(1, item("gate", "X", [1])), "target": {"state": "1"}})[
            "equal_to_target"
        ]
        is True
    )


def test_check_symbols_are_reported_or_bound():
    rx_theta = circuit(1, item("gate", r"R_x(\theta)", [1]))
    rx_pi = circuit(1, item("gate", r"R_x(\pi)", [1]))
    request = {"a": rx_theta, "b": rx_pi}
    symbolic = check(request)
    assert symbolic["status"] == "symbolic" and symbolic["unbound"] == ["theta"]
    assert symbolic["equal_to_target"] is None and symbolic["comparisons"] == []
    assert check(request, values={"theta": math.pi})["equal_to_target"] is True
    assert check({**request, "values": {"theta": 1.0}})["equal_to_target"] is False
    target = {"circuit": rx_theta, "target": {"state": [0, -1j]}}
    assert check(target)["status"] == "symbolic"
    # R_x(pi)|0> = -i|1>
    assert (
        check({**target, "target": {"state": [[0, 0], [0, -1]]}}, values={"theta": math.pi})[
            "equal_to_target"
        ]
        is True
    )


def test_the_norm_of_a_state_is_checked_to_the_stated_tolerance():
    one_wire = circuit(1, item("gate", "X", [1]))
    off = 1 + 1e-6
    assert check({"circuit": one_wire, "target": {"state": [0, off]}})["status"] == "refused"
    assert run(one_wire, input_state=[off, 0])["status"] == "refused"
    near = 1 + 1e-10
    assert check({"circuit": one_wire, "target": {"state": [0, near]}})["equal_to_target"] is True
    assert run(one_wire, input_state=[near, 0])["status"] == "ok"


def test_check_refuses_what_it_cannot_compare():
    cnot = circuit(2, item("x", "X", [2], [1]))
    for request, text in (
        ({}, "needs 'a' and 'b'"),
        ({"a": HZH}, "both 'a' and 'b'"),
        ({"b": HZH}, "both 'a' and 'b'"),
        ({"a": HZH, "b": HZH, "circuit": HZH}, "both 'a' and 'b'"),
        ({"circuit": HZH}, "needs two circuits"),
        ({"a": HZH, "b": cnot}, "cannot be compared"),
        ({"a": HZH, "b": circuit(1, item("measure", "", [1]))}, "measurements"),
        ({"circuit": HZH, "target": {"state": [1, 0], "unitary": [[1]]}}, "must be"),
        ({"circuit": HZH, "target": {}}, "must be"),
        ({"circuit": HZH, "target": []}, "must be"),
        ({"circuit": HZH, "target": {"state": [1, 0, 0]}}, "needs 2 amplitudes"),
        ({"circuit": HZH, "target": {"state": [1, 1]}}, "norm"),
        ({"circuit": HZH, "target": {"state": "00"}}, "not a basis label"),
        ({"circuit": HZH, "target": {"unitary": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}}, "2 x 2"),
        ({"circuit": HZH, "target": {"unitary": [[1, 0, 0], [0, 1, 0]]}}, "2 x 2"),
        ({"circuit": HZH, "target": {"unitary": [1, 0]}}, "list of non-empty rows"),
        ({"circuit": HZH, "target": {"unitary": [[1, 0], [0]]}}, "same length"),
        ({"circuit": HZH, "target": {"unitary": "I"}}, "non-empty list"),
        ({"a": HZH, "b": HZH, "target": {"state": [1, 0]}}, "'circuit' and 'target' only"),
        ({"a": HZH, "b": HZH, "extra": 1}, "unknown key 'extra'"),
        (
            {"a": circuit(1, parts=[[[item("gate", "H", [1])]], [[item("sep", "=")]]]), "b": HZH},
            "a: give one circuit",
        ),
        (
            {
                "circuit": circuit(1, parts=[[[item("gate", "H", [1])]], [[item("sep", "=")]]]),
                "target": {"state": [1, 0]},
            },
            "without a drawn equality",
        ),
        (
            {"a": circuit(1, item("gate", "U_1", [1])), "b": HZH},
            "a: part 1, step 1, item 1: unknown",
        ),
        (
            {"a": HZH, "b": circuit(1, item("gate", "U_1", [1]))},
            "b: part 1, step 1, item 1: unknown",
        ),
        (None, "must be a JSON object"),
    ):
        result = check(request)
        assert result["status"] == "refused", request
        assert result["command"] == "check"
        assert text in " ".join(result["refused"]), (request, result["refused"])


def test_check_compares_big_circuits_through_random_states():
    wide = circuit(11, item("gate", "H", [1]), item("x", "X", [11], [1]))
    same = circuit(11, item("gate", "H", [1]), item("x", "X", [11], [1]))
    assert check({"a": wide, "b": same})["comparisons"][0]["method"] == "random-states"
    other = circuit(11, item("gate", "H", [1]), item("x", "X", [10], [1]))
    assert check({"a": wide, "b": other})["equal_to_target"] is False
    assert check({"a": circuit(21, item("gate", "H", [1])), "b": HZH})["status"] == "refused"


# --- encoding -----------------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.tuples(st.floats(-1, 1, allow_nan=False), st.floats(-1, 1, allow_nan=False)),
        min_size=1,
        max_size=8,
    )
)
def test_complex_numbers_round_trip_within_the_rounding(pairs):
    array = np.array([complex(re, im) for re, im in pairs])
    again = decode(encode(array))
    assert np.allclose(again, array, atol=1e-11)
    assert all(re + 0.0 == re for re, _ in encode(array))


def test_numbers_that_are_not_finite_or_too_large_are_refused():
    nan, inf, huge = float("nan"), float("inf"), 10**400
    one_wire = circuit(1, item("gate", "H", [1]))
    for state, text in (
        ([nan, 0], "must be finite"),
        ([inf, 0], "must be finite"),
        ([[nan, 0], [0, 0]], "must be finite"),
        ([huge, 0], "too large for a float"),
        ([[huge, 0], [0, 0]], "too large for a float"),
    ):
        result = run(one_wire, input_state=state)
        assert result["status"] == "refused", state
        assert text in result["refused"][0], (state, result["refused"])
    rx = circuit(1, item("gate", r"R_x(\theta)", [1]))
    assert "too large for a float" in run(rx, values={"theta": huge})["refused"][0]
    assert "not finite" in run(rx, values={"theta": nan})["refused"][0]
    target = {"unitary": [[huge, 0], [0, 1]]}
    assert "too large" in check({"circuit": one_wire, "target": target})["refused"][0]
    assert decode([[1, 0], [0, 1]], 2).shape == (2, 2)  # a 2 x 2 matrix, not two pairs
    assert decode([[1, 0], [0, 1]], 1).shape == (2,)  # two [re, im] pairs


def test_negative_zero_is_not_written():
    text = json.dumps(encode(np.array([-1e-20 + 1e-20j, -0.0 - 0.0j])))
    assert "-0.0" not in text


ANYTHING = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=6),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(
            st.sampled_from(
                [
                    *["format", "wires", "parts", "circuit", "a", "b", "target", "state"],
                    *["unitary", "input", "values", "kind", "label", "targets"],
                ]
            ),
            children,
            max_size=5,
        )
    ),
    max_leaves=25,
)


@settings(max_examples=400, deadline=None)
@given(ANYTHING, ANYTHING)
def test_run_and_check_never_raise_on_any_json(request, other):
    for result in (
        run(request),
        check(request),
        run(BELL, values=other),
        check(BELL, values=other),
    ):
        assert result["status"] in ("ok", "symbolic", "refused")
        assert (result["status"] == "refused") == bool(result["refused"])
        json.dumps(result, allow_nan=False)


def test_the_wire_limit_is_checked_before_the_document_is_read():
    # the limit comes first: nothing else of a document of 40 wires is looked at
    bad_item = item("gate", "H", [1]) | {"control": [1]}
    for wires in (21, 40):
        result = run(circuit(wires, bad_item))
        assert result["refused"] == [f"{wires} wires; the limit is 20"]
    result = check({"a": circuit(30, bad_item), "b": circuit(2, item("gate", "H", [1]))})
    assert result["refused"] == ["a: 30 wires; the limit is 20"]
    # a wire count that is not a number is for the reader to refuse
    assert run(circuit(True, item("gate", "H", [1])))["status"] == "refused"
    assert run({"format": "circuit/1", "wires": "40", "parts": []})["status"] == "refused"
