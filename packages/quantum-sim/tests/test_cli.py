"""The quantum-sim command."""

import io
import json
import subprocess
import sys

import pytest

from quantum_sim import GATES, __version__
from quantum_sim import entrypoints as entrypoints_module
from quantum_sim.entrypoints import main


def test_conventions_prints_the_table_as_json(capsys):
    assert main(["conventions"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["version"] == __version__
    assert [g["name"] for g in out["gates"]] == list(GATES)
    assert out["limits"] == {
        "statevector_wires": 20,
        "unitary_wires": 10,
        "ops": 10000,
        "random_states": 8,
        "atol": 1e-9,
    }
    assert "most significant" in out["conventions"]["wire_order"]


def test_a_command_is_required(capsys):
    with pytest.raises(SystemExit) as exit_:
        main([])
    assert exit_.value.code == 2


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.strip() == __version__


def test_the_console_script_is_installed():
    script = subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlib.metadata import entry_points; print(entry_points(group='console_scripts')['quantum-sim'].value)",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert script.stdout.strip() == "quantum_sim.entrypoints:main"


# --- run and check ------------------------------------------------------------------------------


def item(kind, label="", targets=(), controls=()):
    return {
        "kind": kind,
        "label": label,
        "targets": list(targets),
        "controls": list(controls),
        "open_controls": [],
        "classical": False,
        "source": "",
    }


def document(wires, *items):
    return {"format": "circuit/1", "wires": wires, "parts": [[[i] for i in items]]}


BELL = document(2, item("gate", "H", [1]), item("x", "X", [2], [1]))
RY = document(1, item("gate", r"R_y(\theta)", [1]))
HZH = document(1, item("gate", "H", [1]), item("gate", "Z", [1]), item("gate", "H", [1]))
X_GATE = document(1, item("gate", "X", [1]))
UNKNOWN = document(1, item("gate", "U_1", [1]))


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


def output(capsys):
    """The one JSON document on standard output (and nothing on standard error)."""
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def stdin(monkeypatch, text):
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(text if isinstance(text, str) else json.dumps(text))
    )


def test_run_a_file(tmp_path, capsys):
    assert main(["run", write(tmp_path, "bell.json", BELL)]) == 0
    result = output(capsys)
    assert result["format"] == "quantum-sim-result/1" and result["command"] == "run"
    assert result["status"] == "ok" and result["input"] == "00"
    assert result["circuits"][0]["probabilities"]["values"] == [0.5, 0.0, 0.0, 0.5]
    assert result["conventions"]["wire_order"].startswith("wire 1 is the top wire")


@pytest.mark.parametrize("argv", [["run"], ["run", "-"]])
def test_run_reads_standard_input(monkeypatch, capsys, argv):
    stdin(monkeypatch, BELL)
    assert main(argv) == 0
    assert output(capsys)["status"] == "ok"


def test_run_options(tmp_path, capsys):
    path = write(tmp_path, "bell.json", BELL)
    assert main(["run", path, "--input", "10", "--unitary"]) == 0
    result = output(capsys)
    assert result["input"] == "10" and result["circuits"][0]["unitary"] is not None
    assert result["circuits"][0]["probabilities"]["values"] == [0.5, 0.0, 0.0, 0.5]


def test_the_unitary_flag_only_adds_and_the_request_file_decides_otherwise(tmp_path, capsys):
    asked = {"circuit": BELL, "unitary": True}
    declined = {"circuit": BELL, "unitary": False}
    for name, request, flag, listed in [
        ("asked.json", asked, [], True),
        ("asked.json", asked, ["--unitary"], True),
        ("declined.json", declined, [], False),
        ("declined.json", declined, ["--unitary"], True),
        ("plain.json", BELL, [], False),
    ]:
        assert main(["run", write(tmp_path, name, request), *flag]) == 0
        entry = output(capsys)["circuits"][0]
        assert (entry["unitary"] is not None) is listed, (name, flag)


def test_run_with_values_for_symbols(tmp_path, capsys):
    path = write(tmp_path, "ry.json", RY)
    assert main(["run", path]) == 0
    symbolic = output(capsys)
    assert symbolic["status"] == "symbolic" and symbolic["unbound"] == ["theta"]
    assert main(["run", path, "--set", "theta=\\pi"]) == 0
    result = output(capsys)
    assert result["status"] == "ok" and result["values"] == {"theta": pytest.approx(3.14159265)}
    assert result["circuits"][0]["probabilities"]["values"] == [0.0, 1.0]
    assert main(["run", path, "--set", "theta=1"]) == 0
    assert output(capsys)["values"] == {"theta": 1.0}


@pytest.mark.parametrize("assignment", ["theta=pi/4", "theta=\\pi/4", " theta = pi/4 "])
def test_set_takes_pi_without_the_backslash_as_docingest_does(tmp_path, capsys, assignment):
    path = write(tmp_path, "ry.json", RY)
    assert main(["run", path, "--set", assignment]) == 0
    result = output(capsys)
    assert result["status"] == "ok" and result["values"] == {"theta": pytest.approx(0.78539816)}


def test_a_name_given_twice_to_set_is_refused(tmp_path, capsys):
    path = write(tmp_path, "ry.json", RY)
    assert main(["run", path, "--set", "theta=0", "--set", " theta = \\pi"]) == 1
    result = output(capsys)
    assert result["status"] == "refused" and "'theta' twice" in result["refused"][0]


@pytest.mark.parametrize("pair", [("theta=0", r"\theta=\pi"), (r"\theta=\pi", "theta=0")])
def test_a_name_given_to_set_in_two_spellings_is_refused(tmp_path, capsys, pair):
    path = write(tmp_path, "ry.json", RY)
    assert main(["run", path, "--set", pair[0], "--set", pair[1]]) == 1
    result = output(capsys)
    assert result["status"] == "refused" and "values give 'theta' twice" in result["refused"][0]


def test_a_refused_circuit_is_a_json_result_and_exit_1(tmp_path, capsys):
    assert main(["run", write(tmp_path, "bad.json", UNKNOWN)]) == 1
    result = output(capsys)
    assert result["status"] == "refused" and result["circuits"] == []
    assert "unknown gate label 'U_1'" in result["refused"][0]


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("", "is not valid JSON"),
        ("not json", "is not valid JSON"),
        ("{", "is not valid JSON"),
        ("[" * 100000, "is not valid JSON"),
        ("[]", "must be a JSON object"),
        ("1", "must be a JSON object"),
        ("null", "must be a JSON object"),
        ("{}", "needs a 'circuit'"),
        ('{"format": "circuit/1", "wires": NaN, "parts": [[]]}', "wires"),
    ],
)
def test_bad_json_is_a_refusal(tmp_path, capsys, content, reason):
    assert main(["run", write(tmp_path, "x.json", content)]) == 1
    result = output(capsys)
    assert result["status"] == "refused" and reason in " ".join(result["refused"])


def test_a_key_given_twice_in_the_json_is_refused_not_resolved(tmp_path, capsys):
    # the last value would win silently: a CNOT would become an X, a postselecting legend would
    # be replaced by one that is read
    cnot = '{"kind": "x", "targets": [2], "controls": [1], "controls": []}'
    documents = {
        "controls": f'{{"format": "circuit/1", "wires": 2, "parts": [[[{cnot}]]]}}',
        "wire_labels": (
            '{"format": "circuit/1", "wires": 2, "parts": [[[{"kind": "gate", "label": "H",'
            ' "targets": [1]}]]], "wire_labels": ["1 = q", "2 = q, output \\\\ket{1}"],'
            ' "wire_labels": ["1 = q", "2 = q"]}'
        ),
        "label": '{"format": "circuit/1", "wires": 1, "parts": [[[{"kind": "gate", '
        '"label": "H", "label": "X", "targets": [1]}]]]}',
        "theta": '{"circuit": ' + json.dumps(RY) + ', "values": {"theta": 0, "theta": 1}}',
    }
    for key, content in documents.items():
        assert main(["run", write(tmp_path, f"{key}.json", content)]) == 1, key
        result = output(capsys)
        assert result["status"] == "refused" and result["circuits"] == [], key
        assert f"gives the key {key!r} twice" in result["refused"][0], key
    # the same key in two different objects is not a repeat
    twice = '{"format": "circuit/1", "wires": 1, "parts": [[[{"kind": "gate", "label": "H",'
    twice += ' "targets": [1]}], [{"kind": "gate", "label": "H", "targets": [1]}]]]}'
    assert main(["run", write(tmp_path, "ok.json", twice)]) == 0
    assert output(capsys)["status"] == "ok"


def test_unreadable_input_is_a_refusal(tmp_path, capsys):
    assert main(["run", str(tmp_path / "missing.json")]) == 1
    assert "cannot read" in output(capsys)["refused"][0]
    assert main(["run", str(tmp_path)]) == 1
    assert "cannot read" in output(capsys)["refused"][0]
    binary = tmp_path / "binary.json"
    binary.write_bytes(b"\xff\xfe\x00")
    assert main(["run", str(binary)]) == 1
    assert "cannot read" in output(capsys)["refused"][0]


def test_input_over_the_size_limit_is_refused(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(entrypoints_module, "MAX_INPUT_BYTES", 10)
    assert main(["run", write(tmp_path, "big.json", json.dumps(BELL))]) == 1
    assert "more than" in output(capsys)["refused"][0]


def test_bad_assignments_are_a_refusal(tmp_path, capsys):
    path = write(tmp_path, "ry.json", RY)
    for text, reason in (
        ("theta", "expects NAME=VALUE"),
        ("=1", "expects NAME=VALUE"),
        (" =1", "expects NAME=VALUE"),
        ("theta=", "empty angle"),
        ("theta=x", "free symbols"),
        ("beta=1", "no parameter named 'beta'"),
        ("pi=1", "'pi' is not a parameter name"),
        ("theta=1/0", "division by zero"),
    ):
        assert main(["run", path, "--set", text]) == 1, text
        assert reason in output(capsys)["refused"][0], text


def test_a_bad_set_value_names_the_position_in_the_value_as_typed(tmp_path, capsys):
    path = write(tmp_path, "ry.json", RY)
    assert main(["run", path, "--set", "theta=   pi ?"]) == 1
    assert "'?' at position 6" in output(capsys)["refused"][0]


def test_usage_errors_exit_2(capsys):
    for argv in (["run", "a", "b"], ["frobnicate"], ["run", "--nope"], ["check", "--input", "1"]):
        with pytest.raises(SystemExit) as stop:
            main(argv)
        assert stop.value.code == 2
    assert capsys.readouterr().out == ""


def test_check_one_request_file(tmp_path, capsys):
    path = write(tmp_path, "r.json", {"a": HZH, "b": X_GATE})
    assert main(["check", path]) == 0
    result = output(capsys)
    assert result["command"] == "check" and result["equal_to_target"] is True


def test_check_two_circuit_files(tmp_path, capsys):
    a, b = write(tmp_path, "a.json", HZH), write(tmp_path, "b.json", X_GATE)
    assert main(["check", a, b]) == 0
    assert output(capsys)["equal_to_target"] is True
    other = write(tmp_path, "y.json", document(1, item("gate", "Y", [1])))
    assert main(["check", a, other]) == 0  # different circuits: the check was done, exit 0
    result = output(capsys)
    assert result["status"] == "ok" and result["equal_to_target"] is False


def test_check_standard_input_and_a_drawn_equality(monkeypatch, capsys):
    drawn = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [
            [[item("gate", "H", [1])], [item("gate", "Z", [1])], [item("gate", "H", [1])]],
            [[item("sep", "=")], [item("gate", "X", [1])]],
        ],
    }
    stdin(monkeypatch, drawn)
    assert main(["check"]) == 0
    assert output(capsys)["equal_to_target"] is True
    stdin(
        monkeypatch,
        {"circuit": BELL, "target": {"state": [0.7071067811865476, 0, 0, 0.7071067811865476]}},
    )
    assert main(["check", "-"]) == 0
    assert output(capsys)["equal_to_target"] is True


def test_check_with_symbols(tmp_path, capsys):
    request = write(tmp_path, "r.json", {"a": RY, "b": document(1, item("gate", r"R_y(\pi)", [1]))})
    assert main(["check", request]) == 0
    assert output(capsys)["status"] == "symbolic"
    assert main(["check", request, "--set", "theta=\\pi"]) == 0
    assert output(capsys)["equal_to_target"] is True


def test_check_refusals_exit_1(tmp_path, capsys):
    assert main(["check", write(tmp_path, "a.json", HZH)]) == 1
    assert "needs two circuits" in output(capsys)["refused"][0]
    paths = [write(tmp_path, f"{n}.json", HZH) for n in "abc"]
    assert main(["check", *paths]) == 1
    assert "at most two files" in output(capsys)["refused"][0]
    assert main(["check", write(tmp_path, "u.json", UNKNOWN), paths[0]]) == 1
    assert output(capsys)["refused"][0].startswith("a: part 1")


def test_the_installed_command_runs_end_to_end(tmp_path):
    code = "import sys; from quantum_sim.entrypoints import main; sys.exit(main())"
    path = write(tmp_path, "bell.json", BELL)
    done = subprocess.run(
        [sys.executable, "-c", code, "run", path],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0 and done.stderr == ""
    assert json.loads(done.stdout)["circuits"][0]["probabilities"]["values"] == [0.5, 0, 0, 0.5]
    bad = subprocess.run(
        [sys.executable, "-c", code, "run", write(tmp_path, "u.json", UNKNOWN)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 1 and json.loads(bad.stdout)["status"] == "refused"
