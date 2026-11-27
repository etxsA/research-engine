from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from docingest.adapters.qasm import export_document
from fastapi.testclient import TestClient
from helpers import BELL, SEED_FILE, sign_in_as_member
from quantum_sim.qasm import to_qasm

from research_api.adapters.circuits import ComputeLane
from research_api.adapters.fake import FakeDocumentReader
from research_api.adapters.fake.corpus import PAPERS, TELEPORT, TRANSMON, FakePage, FakePaper
from research_api.bootstrap import Settings, build_container
from research_api.bootstrap.seed import load_seed_users
from research_api.domain.circuits import MAX_VALUE_CHARS, MAX_VALUES
from research_api.entrypoints.cli import openapi_document
from research_api.entrypoints.http import build_app

PROBLEM = "application/problem+json"
ClientFor = Callable[..., TestClient]
JSON = {"content-type": "application/json"}

S2 = 0.7071067811865476


def text(document: object) -> str:
    return json.dumps(document)


def wide(wires: int, ops: int = 1) -> dict[str, Any]:
    step = [{"kind": "gate", "label": "H", "targets": [1]}]
    return {"format": "circuit/1", "wires": wires, "parts": [[step] * ops]}


@pytest.fixture
def client(client: TestClient) -> TestClient:
    """The circuit routes are for members: every test here signs in first."""
    return sign_in_as_member(client)


@pytest.fixture
def run(client: TestClient) -> Callable[..., Any]:
    def post(**body: Any) -> Any:
        return client.post("/api/v1/circuits/run", json=body)

    return post


@pytest.fixture
def check(client: TestClient) -> Callable[..., Any]:
    def post(**body: Any) -> Any:
        return client.post("/api/v1/circuits/check", json=body)

    return post


def client_with(client_for: ClientFor, **caps: Any) -> TestClient:
    settings = Settings.model_validate({"circuit_caps": caps})
    app = build_app(
        build_container(settings, fake=True, seed=load_seed_users(SEED_FILE), cheap_hashing=True)
    )
    return sign_in_as_member(client_for(app))


# --- parse -------------------------------------------------------------------------------------


def test_latex_becomes_a_gate_list_and_circuit_1_documents(client: TestClient) -> None:
    latex = (
        r"\begin{quantikz} \lstick{$\ket{0}$} & \gate{H} & \ctrl{1} & \qw \\"
        r" \lstick{$\ket{0}$} & \qw & \targ{} & \qw \end{quantikz}"
    )
    response = client.post("/api/v1/circuits/parse", json={"latex": latex})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "parsed"
    assert "Step 2: CNOT: control wire 1, target wire 2" in body["text"]
    (circuit,) = body["circuits"]
    assert circuit["format"] == "circuit/1" and circuit["wires"] == 2
    assert circuit["parts"][0][1][0]["controls"] == [1]


def test_text_with_no_circuit_is_unparsed(client: TestClient) -> None:
    body = client.post("/api/v1/circuits/parse", json={"latex": "no circuit here"}).json()
    assert body == {"status": "unparsed", "text": "", "circuits": []}


def test_a_circuit_the_reader_half_understands_is_partial(client: TestClient) -> None:
    latex = r"\begin{quantikz} & \gate{H} & \mymacro{3} & \qw \end{quantikz}"
    body = client.post("/api/v1/circuits/parse", json={"latex": latex}).json()
    assert body["status"] == "partial"
    assert body["circuits"][0]["status"] == "partial"


def test_parse_needs_latex(client: TestClient) -> None:
    response = client.post("/api/v1/circuits/parse", json={})
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "/latex"


# --- run ---------------------------------------------------------------------------------------


def test_run_gives_the_simulator_result_as_it_is(run: Callable[..., Any]) -> None:
    response = run(circuit_json=text(BELL))
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "quantum-sim-result/1" and body["status"] == "ok"
    assert body["circuits"][0]["probabilities"] == {"wires": [1, 2], "values": [0.5, 0, 0, 0.5]}
    assert "wire 1" in " ".join(body["conventions"].values())


def test_run_takes_an_input_label(run: Callable[..., Any]) -> None:
    body = run(circuit_json=text(BELL), input="01").json()
    assert body["circuits"][0]["probabilities"]["values"] == pytest.approx([0, 0.5, 0.5, 0])
    assert body["input"] is not None


def test_run_takes_amplitudes_as_the_input(run: Callable[..., Any]) -> None:
    body = run(circuit_json=text(wide(1, 0)), input=[0, 1]).json()
    assert body["status"] == "ok"


def test_run_lists_the_unitary_on_request(run: Callable[..., Any]) -> None:
    body = run(circuit_json=text(BELL), unitary=True).json()
    assert len(body["circuits"][0]["unitary"]) == 4


@pytest.mark.parametrize("flag", [1, 0, "true", "yes", "on", None])
def test_unitary_is_true_or_false_and_nothing_else(run: Callable[..., Any], flag: object) -> None:
    response = run(circuit_json=text(BELL), unitary=flag)
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "/unitary"


def test_run_binds_symbols(run: Callable[..., Any]) -> None:
    ry = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [[[{"kind": "gate", "label": r"R_y(\theta)", "targets": [1]}]]],
    }
    unbound = run(circuit_json=text(ry)).json()
    assert unbound["status"] == "symbolic" and unbound["unbound"] == ["theta"]
    bound = run(circuit_json=text(ry), values={"theta": r"\pi"}).json()
    assert bound["status"] == "ok"
    assert bound["circuits"][0]["probabilities"]["values"] == pytest.approx([0, 1])


@pytest.mark.parametrize("value", ["pi", r"\pi", "2pi/2"])
def test_a_value_may_write_pi_without_the_backslash(run: Callable[..., Any], value: str) -> None:
    ry = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [[[{"kind": "gate", "label": r"R_y(\theta)", "targets": [1]}]]],
    }
    body = run(circuit_json=text(ry), values={"theta": value}).json()
    assert body["status"] == "ok", body["refused"]
    assert body["values"] == {"theta": pytest.approx(3.141592653589793)}
    assert body["circuits"][0]["probabilities"]["values"] == pytest.approx([0, 1])


def test_a_value_error_names_the_position_in_the_value_as_sent(run: Callable[..., Any]) -> None:
    ry = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [[[{"kind": "gate", "label": r"R_y(\theta)", "targets": [1]}]]],
    }
    body = run(circuit_json=text(ry), values={"theta": "pi pi ?"}).json()
    assert body["status"] == "refused"
    assert any("'?' at position 6" in reason for reason in body["refused"]), body["refused"]


def test_a_circuit_the_simulator_refuses_is_a_200_with_its_reasons(
    run: Callable[..., Any],
) -> None:
    unknown = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [[[{"kind": "gate", "label": "Frobnicate", "targets": [1]}]]],
    }
    response = run(circuit_json=text(unknown))
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refused"
    assert any("unknown gate label 'Frobnicate'" in reason for reason in body["refused"])
    assert body["circuits"] == []


@pytest.mark.parametrize("document", ["[]", "3", "null", '"x"', "{}"])
def test_json_that_is_not_a_circuit_is_refused_by_the_simulator(
    run: Callable[..., Any], document: str
) -> None:
    response = run(circuit_json=document)
    assert response.status_code == 200 and response.json()["status"] == "refused"


def test_a_request_pasted_as_the_circuit_is_not_taken_for_a_request(
    run: Callable[..., Any],
) -> None:
    body = run(circuit_json=text({"circuit": BELL, "input": "11"})).json()
    assert body["status"] == "refused"


def test_run_refuses_a_field_it_does_not_know(run: Callable[..., Any]) -> None:
    response = run(circuit_json=text(BELL), unitry=True)
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "/unitry"


def test_run_needs_the_circuit_as_text(run: Callable[..., Any]) -> None:
    response = run(circuit_json=BELL)
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "/circuit_json"


# --- duplicate keys and bad JSON ---------------------------------------------------------------


def test_a_key_written_twice_in_the_circuit_is_refused_with_its_name(client: TestClient) -> None:
    twice = '{"format": "circuit/1", "wires": 1, "wires": 2, "parts": []}'
    response = client.post("/api/v1/circuits/run", json={"circuit_json": twice})
    assert response.status_code == 400
    assert response.headers["content-type"] == PROBLEM
    assert "circuit_json gives the key 'wires' twice" in response.json()["detail"]


def test_a_repeated_controls_key_in_an_item_is_refused(client: TestClient) -> None:
    twice = (
        '{"format": "circuit/1", "wires": 2, "parts": [[[{"kind": "x", "targets": [2], '
        '"controls": [1], "controls": []}]]]}'
    )
    response = client.post("/api/v1/circuits/run", json={"circuit_json": twice})
    assert response.status_code == 400 and "'controls'" in response.json()["detail"]


def test_a_key_written_twice_in_the_second_circuit_is_refused(client: TestClient) -> None:
    body = {"circuit_json": text(BELL), "target": {"circuit_json": '{"a": 1, "a": 1}'}}
    response = client.post("/api/v1/circuits/check", json=body)
    assert response.status_code == 400
    assert "target.circuit_json gives the key 'a' twice" in response.json()["detail"]


def test_a_key_written_twice_in_the_body_is_refused(client: TestClient) -> None:
    body = '{"circuit_json": "{}", "unitary": true, "unitary": false}'
    response = client.post("/api/v1/circuits/run", content=body, headers=JSON)
    assert response.status_code == 400
    assert "The request body gives the key 'unitary' twice" in response.json()["detail"]


@pytest.mark.parametrize("body", ["{", "not json", '{"circuit_json": }', "\xff"])
def test_a_body_that_is_not_json_is_a_400(client: TestClient, body: str) -> None:
    response = client.post("/api/v1/circuits/run", content=body.encode("latin-1"), headers=JSON)
    assert response.status_code == 400 and response.headers["content-type"] == PROBLEM


def test_a_circuit_that_is_not_json_is_a_400_that_does_not_repeat_it(client: TestClient) -> None:
    secret = "SECRET-CIRCUIT-TEXT"
    response = client.post("/api/v1/circuits/run", json={"circuit_json": "{" + secret})
    assert response.status_code == 400
    assert secret not in response.text


def test_nan_in_the_circuit_is_a_400(client: TestClient) -> None:
    response = client.post("/api/v1/circuits/run", json={"circuit_json": '{"wires": NaN}'})
    assert response.status_code == 400 and "NaN" in response.json()["detail"]


def test_an_empty_body_is_a_422(client: TestClient) -> None:
    assert client.post("/api/v1/circuits/run", content=b"", headers=JSON).status_code == 422


# --- check -------------------------------------------------------------------------------------


def test_a_state_target(check: Callable[..., Any]) -> None:
    body = check(circuit_json=text(BELL), target={"state": [S2, 0, 0, S2]}).json()
    assert body["equal_to_target"] is True
    assert body["comparisons"][0]["between"] == ["circuit", "state"]


def test_a_basis_label_as_the_state_with_an_input(check: Callable[..., Any]) -> None:
    flip = {"format": "circuit/1", "wires": 1, "parts": [[[{"kind": "x", "targets": [1]}]]]}
    body = check(circuit_json=text(flip), target={"state": "1"}, input="0").json()
    assert body["equal_to_target"] is True


def test_a_state_that_is_not_the_result_is_not_equal_and_still_a_200(
    check: Callable[..., Any],
) -> None:
    response = check(circuit_json=text(BELL), target={"state": "00"})
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok" and body["equal_to_target"] is False
    assert body["comparisons"][0]["max_deviation"] > 0.5


def test_a_unitary_target(check: Callable[..., Any]) -> None:
    cnot = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]]
    only_cnot = {
        "format": "circuit/1",
        "wires": 2,
        "parts": [[[{"kind": "x", "targets": [2], "controls": [1]}]]],
    }
    body = check(circuit_json=text(only_cnot), target={"unitary": cnot}).json()
    assert body["equal_to_target"] is True
    assert body["comparisons"][0]["exact"] is True


def test_a_unitary_given_as_re_im_pairs(check: Callable[..., Any]) -> None:
    x = {"format": "circuit/1", "wires": 1, "parts": [[[{"kind": "x", "targets": [1]}]]]}
    matrix = [[[0, 0], [1, 0]], [[1, 0], [0, 0]]]
    assert check(circuit_json=text(x), target={"unitary": matrix}).json()["equal_to_target"]


def test_a_second_circuit_target(check: Callable[..., Any]) -> None:
    hxh = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [
            [
                [{"kind": "gate", "label": "H", "targets": [1]}],
                [{"kind": "gate", "label": "X", "targets": [1]}],
                [{"kind": "gate", "label": "H", "targets": [1]}],
            ]
        ],
    }
    z = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [[[{"kind": "gate", "label": "Z", "targets": [1]}]]],
    }
    body = check(circuit_json=text(hxh), target={"circuit_json": text(z)}).json()
    assert body["equal_to_target"] is True
    assert body["comparisons"][0]["between"] == ["a", "b"]


def test_a_drawn_equality_needs_no_target(check: Callable[..., Any]) -> None:
    sign = {"kind": "sep", "label": "=", "targets": []}
    drawn = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [
            [[{"kind": "gate", "label": "X", "targets": [1]}]],
            [[sign], [{"kind": "gate", "label": "X", "targets": [1]}]],
        ],
    }
    body = check(circuit_json=text(drawn)).json()
    assert body["status"] == "ok" and body["equal_to_target"] is True
    assert body["comparisons"][0]["between"] == ["part 1", "part 2"]


def test_a_circuit_alone_is_refused_by_the_simulator(check: Callable[..., Any]) -> None:
    body = check(circuit_json=text(BELL)).json()
    assert body["status"] == "refused"


def test_a_check_takes_values_for_symbols(check: Callable[..., Any]) -> None:
    rz = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [[[{"kind": "gate", "label": r"R_z(\theta)", "targets": [1]}]]],
    }
    z = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [[[{"kind": "gate", "label": "Z", "targets": [1]}]]],
    }
    body = check(
        circuit_json=text(rz), target={"circuit_json": text(z)}, values={"theta": r"\pi"}
    ).json()
    assert body["equal_to_target"] is True


@pytest.mark.parametrize(
    "target",
    [{}, {"state": "0", "unitary": [[1]]}, {"state": "0", "circuit_json": "{}"}, {"extra": 1}],
)
def test_a_target_has_exactly_one_thing_in_it(check: Callable[..., Any], target: dict) -> None:
    response = check(circuit_json=text(BELL), target=target)
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM


@pytest.mark.parametrize(
    "target",
    [
        {"state": None, "unitary": [[1, 0], [0, 1]]},
        {"state": None},
        {"state": "00", "circuit_json": None},
    ],
)
def test_a_null_in_a_target_is_not_taken_for_absent(
    check: Callable[..., Any], target: dict
) -> None:
    response = check(circuit_json=text(BELL), target=target)
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["errors"][0]["path"] == "/target"


def test_a_null_target_is_refused_not_taken_for_absent(check: Callable[..., Any]) -> None:
    response = check(circuit_json=text(BELL), target=None)
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    error = response.json()["errors"][0]
    assert error["path"] == "/target"
    assert "target must be" in error["message"]


@pytest.mark.parametrize("field", ["input", "values"])
def test_a_null_input_or_values_is_the_same_as_absent(
    check: Callable[..., Any], field: str
) -> None:
    response = check(circuit_json=text(BELL), target={"state": "00"}, **{field: None})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# --- caps --------------------------------------------------------------------------------------


def test_thirteen_wires_are_too_many_for_a_simulation(run: Callable[..., Any]) -> None:
    response = run(circuit_json=text(wide(13)))
    assert response.status_code == 422
    body = response.json()
    assert "13 wires; this check takes at most 12" in body["detail"]
    assert body["errors"][0]["path"] == "/circuit_json"


def test_twelve_wires_are_fine_for_a_simulation(run: Callable[..., Any]) -> None:
    assert run(circuit_json=text(wide(12))).json()["status"] == "ok"


def test_the_unitary_stops_at_eight_wires(run: Callable[..., Any]) -> None:
    assert run(circuit_json=text(wide(8)), unitary=True).status_code == 200
    response = run(circuit_json=text(wide(9)), unitary=True)
    assert response.status_code == 422 and "9 wires" in response.json()["detail"]
    assert run(circuit_json=text(wide(9))).status_code == 200


def test_every_comparison_that_builds_a_unitary_stops_at_eight_wires(
    check: Callable[..., Any],
) -> None:
    nine = text(wide(9))
    assert check(circuit_json=nine, target={"unitary": [[1]]}).status_code == 422
    assert check(circuit_json=nine, target={"circuit_json": nine}).status_code == 422
    assert check(circuit_json=nine).status_code == 422


def test_a_state_comparison_goes_to_twelve_wires(check: Callable[..., Any]) -> None:
    response = check(circuit_json=text(wide(12, 0)), target={"state": "0" * 12})
    assert response.status_code == 200 and response.json()["equal_to_target"] is True
    assert check(circuit_json=text(wide(13, 0)), target={"state": "0"}).status_code == 422


def test_the_second_circuit_of_a_check_is_capped_and_pointed_at(
    check: Callable[..., Any],
) -> None:
    response = check(circuit_json=text(BELL), target={"circuit_json": text(wide(30))})
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "/target/circuit_json"


def test_the_operation_cap_is_a_setting(client_for: ClientFor) -> None:
    client = client_with(client_for, max_ops=3)
    url = "/api/v1/circuits/run"
    assert client.post(url, json={"circuit_json": text(wide(1, 3))}).status_code == 200
    response = client.post(url, json={"circuit_json": text(wide(1, 4))})
    assert response.status_code == 422
    assert "4 operations; this endpoint takes at most 3" in response.json()["detail"]


def test_the_default_operation_cap_is_two_thousand(run: Callable[..., Any]) -> None:
    assert run(circuit_json=text(wide(1, 2000))).status_code == 200
    assert run(circuit_json=text(wide(1, 2001))).status_code == 422


def test_a_body_over_the_cap_is_a_413_by_its_declared_size(client_for: ClientFor) -> None:
    client = client_with(client_for, max_body_bytes=200)
    response = client.post("/api/v1/circuits/run", content=b"x" * 201, headers=JSON)
    assert response.status_code == 413
    assert response.headers["content-type"] == PROBLEM
    assert response.json()["detail"] == (
        "The request body is over 200 bytes. "
        "Use `quantum-sim` on the command line for a circuit this size."
    )


def test_a_body_over_the_cap_is_a_413_when_it_arrives_in_chunks(client_for: ClientFor) -> None:
    client = client_with(client_for, max_body_bytes=200)

    def chunks() -> Iterator[bytes]:
        for _ in range(5):
            yield b"x" * 50

    response = client.post("/api/v1/circuits/run", content=chunks(), headers=JSON)
    assert response.status_code == 413


def test_a_body_at_the_cap_is_read(client_for: ClientFor) -> None:
    body = json.dumps({"circuit_json": "{}"})
    client = client_with(client_for, max_body_bytes=len(body))
    assert client.post("/api/v1/circuits/run", content=body, headers=JSON).status_code == 200


def test_the_default_body_cap_is_one_mebibyte(client: TestClient) -> None:
    response = client.post("/api/v1/circuits/parse", json={"latex": "x" * (1024 * 1024)})
    assert response.status_code == 413
    assert client.post("/api/v1/circuits/parse", json={"latex": "x" * 1000}).status_code == 200


def test_a_slow_job_is_a_422_and_then_the_lane_says_busy(
    client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    monkeypatch.setattr(
        "research_api.adapters.circuits.run", lambda *a, **k: release.wait(10) and {}
    )
    client = client_with(client_for, timeout_s=0.2)
    url = "/api/v1/circuits/run"
    body = {"circuit_json": text(BELL)}
    try:
        first = client.post(url, json=body)
        assert first.status_code == 422
        assert first.json()["detail"] == (
            "The work took longer than 0.2 s, so this request was released. "
            "Use `quantum-sim` on the command line for a circuit this size."
        )
        assert client.post(url, json=body).status_code == 422
        busy = client.post(url, json=body)
        assert busy.status_code == 503
        assert busy.headers["retry-after"] == "5"
    finally:
        release.set()


# --- conventions, the contract -----------------------------------------------------------------


def test_the_conventions_are_the_simulators(client: TestClient) -> None:
    body = client.get("/api/v1/circuits/conventions").json()
    assert {"version", "conventions", "gates", "limits"} <= set(body)
    assert body["limits"]["statevector_wires"] == 20
    assert any(gate["name"] == "h" for gate in body["gates"])


def test_the_contract_lists_the_circuit_operations() -> None:
    spec = json.loads(openapi_document())
    operations = {
        (method, path): item["operationId"]
        for path, methods in spec["paths"].items()
        for method, item in methods.items()
        if path.startswith("/api/v1/circuits") or "circuits" in item.get("tags", [])
    }
    assert operations == {
        ("post", "/api/v1/circuits/parse"): "parseCircuit",
        ("post", "/api/v1/circuits/run"): "runCircuit",
        ("post", "/api/v1/circuits/check"): "checkCircuit",
        ("get", "/api/v1/circuits/conventions"): "getCircuitConventions",
        ("post", "/api/v1/circuits/qasm"): "exportQasm",
        ("get", "/api/v1/documents/{doc_id}/circuits"): "listDocumentCircuits",
        ("post", "/api/v1/documents/{doc_id}/qasm"): "exportDocumentQasm",
    }


def test_the_contract_sends_the_circuit_as_text_and_errors_as_problems() -> None:
    spec = json.loads(openapi_document())
    schemas = spec["components"]["schemas"]
    assert schemas["RunRequest"]["properties"]["circuit_json"]["type"] == "string"
    assert schemas["CheckRequest"]["properties"]["circuit_json"]["type"] == "string"
    run = spec["paths"]["/api/v1/circuits/run"]["post"]["responses"]
    for status in ("400", "413", "422", "503"):
        assert PROBLEM in run[status]["content"]
    assert run["200"]["content"]["application/json"]["schema"]["type"] == "object"


def test_the_contract_does_not_offer_a_null_target() -> None:
    schemas = json.loads(openapi_document())["components"]["schemas"]
    target = schemas["CheckRequest"]["properties"]["target"]
    assert "anyOf" not in target
    assert target["$ref"] == "#/components/schemas/CheckTargetBody"
    assert "target" not in schemas["CheckRequest"].get("required", [])


def test_the_values_description_names_both_spellings_of_pi() -> None:
    schemas = json.loads(openapi_document())["components"]["schemas"]
    for name in ("RunRequest", "CheckRequest", "QasmRequest", "PaperQasmRequest"):
        description = schemas[name]["properties"]["values"]["description"]
        assert "`\\pi/2`" in description and "`pi/2`" in description, name


def test_the_contract_bounds_the_values_by_count_and_by_key_length() -> None:
    schemas = json.loads(openapi_document())["components"]["schemas"]
    for name in ("RunRequest", "CheckRequest", "QasmRequest", "PaperQasmRequest"):
        values = next(
            branch
            for branch in schemas[name]["properties"]["values"]["anyOf"]
            if branch.get("type") == "object"
        )
        assert values["maxProperties"] == MAX_VALUES, name
        assert values["propertyNames"] == {"maxLength": MAX_VALUE_CHARS}, name
        assert "maxLength" not in values, name


# --- OpenQASM 3 export of a pasted circuit -----------------------------------------------------

QASM = "/api/v1/circuits/qasm"


def ry(label: str = r"R_y(\theta)") -> dict[str, Any]:
    return {
        "format": "circuit/1",
        "wires": 1,
        "parts": [[[{"kind": "gate", "label": label, "targets": [1]}]]],
    }


@pytest.fixture
def export(client: TestClient) -> Callable[..., Any]:
    def post(document: object, **body: Any) -> Any:
        return client.post(QASM, json={"circuit_json": text(document), **body})

    return post


def program_lines(program: str) -> list[str]:
    return [line for line in program.splitlines() if not line.startswith("//")]


def test_a_pasted_circuit_comes_back_as_typed_programs(export: Callable[..., Any]) -> None:
    response = export(BELL)
    assert response.status_code == 200
    assert "no-store" not in response.headers.get("cache-control", "")
    body = response.json()
    assert body == {
        "status": "ok",
        "programs": [
            {
                "filename": "circuit-p01.qasm",
                "part": 1,
                "sign": None,
                "text": to_qasm(BELL, tool="research-api").programs[0].text,
                "n_qubits": 2,
                "n_clbits": 0,
            }
        ],
        "reasons": [],
        "notes": [],
        "symbols": [],
        "unmatched": [],
        "unbound": [],
    }
    program = body["programs"][0]["text"]
    assert program.startswith("// Generated by research-api (qasm format 1)\n")
    assert "// source:" not in program


def test_a_drawn_equality_gives_one_program_per_side(export: Callable[..., Any]) -> None:
    x = [[{"kind": "gate", "label": "X", "targets": [1]}]]
    sign = [[{"kind": "sep", "label": "=", "targets": []}]]
    body = export({"format": "circuit/1", "wires": 1, "parts": [x, sign + x]}).json()
    assert [(p["filename"], p["part"], p["sign"]) for p in body["programs"]] == [
        ("circuit-p01.qasm", 1, None),
        ("circuit-p02.qasm", 2, "="),
    ]
    assert "// sign: =" in body["programs"][1]["text"]


def test_a_symbol_without_a_value_is_refused_with_a_200(export: Callable[..., Any]) -> None:
    response = export(ry())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refused" and body["programs"] == []
    assert body["reasons"] == ["part 1: unbound parameter(s): theta; bind them with a value"]
    assert body["symbols"] == ["theta"]
    assert body["unbound"] == ["theta"]


@pytest.mark.parametrize("value", ["pi/4", r"\pi/4", "0.7853981633974483"])
def test_a_bound_symbol_is_exported_with_its_value(export: Callable[..., Any], value: str) -> None:
    body = export(ry(), values={"theta": value}).json()
    assert body["status"] == "ok", body["reasons"]
    program = body["programs"][0]["text"]
    assert f"// bound: theta = {value}\n" in program
    expected = to_qasm(ry(), values={"theta": value}, tool="research-api").programs[0].text
    assert program == expected


def test_pi_and_backslash_pi_give_the_same_program_body(export: Callable[..., Any]) -> None:
    plain = export(ry(), values={"theta": "pi/4"}).json()["programs"][0]["text"]
    latex = export(ry(), values={r"\theta": r"\pi/4"}).json()["programs"][0]["text"]
    assert program_lines(plain) == program_lines(latex)


def test_a_key_no_symbol_uses_is_listed_as_unmatched(export: Callable[..., Any]) -> None:
    body = export(BELL, values={"phi": "1"}).json()
    assert body["status"] == "ok" and body["unmatched"] == ["phi"]


def test_a_circuit_the_emitter_refuses_has_its_reasons_word_for_word(
    export: Callable[..., Any],
) -> None:
    body = export(ry("R_{xx}(1)")).json()
    expected = to_qasm(ry("R_{xx}(1)"), tool="research-api")
    assert body["status"] == "refused" and body["reasons"] == list(expected.reasons)


def test_a_subscript_of_two_characters_is_named_as_a_key_the_values_box_takes(
    export: Callable[..., Any],
) -> None:
    body = export(ry(r"R_y(\theta_{12})")).json()
    assert body["status"] == "refused" and body["symbols"] == ["theta_12"]
    assert body["unbound"] == ["theta_{12}"]


def test_a_circuit_refused_for_another_reason_too_names_no_unbound_symbol(
    export: Callable[..., Any],
) -> None:
    both = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [
            [
                [{"kind": "text", "label": r"\ket{1}", "targets": [1]}],
                [{"kind": "gate", "label": r"R_y(\theta)", "targets": [1]}],
            ]
        ],
    }
    body = export(both).json()
    assert body["status"] == "refused" and body["symbols"] == ["theta"]
    assert any("|1>" in reason for reason in body["reasons"])
    assert body["unbound"] == []


@pytest.mark.parametrize(
    ("values", "path", "words"),
    [
        ({"thetta": "1"}, "/values/thetta", "'thetta' is not a parameter name"),
        ({"pi": "1"}, "/values/pi", "'pi' is not a parameter name"),
        ({"theta": "pi pi ?"}, "/values/theta", "'?' at position 6"),
        ({"theta": "1", "\\theta": "2"}, "/values/\\theta", "parameter 'theta' is bound twice"),
        ({"theta": "\\phi"}, "/values/theta", "free symbol"),
    ],
)
def test_a_key_or_value_the_export_cannot_use_is_a_422_that_points_to_it(
    export: Callable[..., Any], values: dict[str, Any], path: str, words: str
) -> None:
    response = export(ry(), values=values)
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert words in body["detail"]
    assert body["errors"] == [{"path": path, "message": body["detail"]}]


@pytest.mark.parametrize("value", [True, None, [1], {"a": 1}])
def test_a_value_that_is_not_a_number_or_text_is_a_422(
    export: Callable[..., Any], value: object
) -> None:
    response = export(ry(), values={"theta": value})
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"].startswith("/values/theta")


def test_an_export_has_no_wire_cap(export: Callable[..., Any]) -> None:
    body = export(wide(16)).json()
    assert body["status"] == "ok" and body["programs"][0]["n_qubits"] == 16


def test_an_export_over_the_operation_cap_is_a_422(client_for: ClientFor) -> None:
    client = client_with(client_for, max_ops=3)
    assert client.post(QASM, json={"circuit_json": text(wide(1, 3))}).status_code == 200
    response = client.post(QASM, json={"circuit_json": text(wide(1, 4))})
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == (
        "The request asks for 4 operations; this endpoint takes at most 3. "
        "Use the Python function `quantum_sim.to_qasm` for a circuit this size."
    )
    assert body["errors"][0]["path"] == "/circuit_json"


def test_an_export_body_over_the_cap_is_a_413(client_for: ClientFor) -> None:
    client = client_with(client_for, max_body_bytes=200)
    response = client.post(QASM, json={"circuit_json": text(wide(1, 20))})
    assert response.status_code == 413
    assert response.json()["detail"] == (
        "The request body is over 200 bytes. "
        "Use the Python function `quantum_sim.to_qasm` for a circuit this size."
    )
    # the other circuit routes keep pointing to quantum-sim's command
    response = client.post("/api/v1/circuits/check", json={"circuit_json": text(wide(1, 20))})
    assert response.status_code == 413 and "`quantum-sim` on the command line" in response.text


def test_a_key_written_twice_in_an_export_is_a_400(client: TestClient) -> None:
    twice = '{"format": "circuit/1", "wires": 1, "wires": 2, "parts": []}'
    response = client.post(QASM, json={"circuit_json": twice})
    assert response.status_code == 400
    assert "circuit_json gives the key 'wires' twice" in response.json()["detail"]
    body = '{"circuit_json": "{}", "values": {"theta": 1, "theta": 2}}'
    response = client.post(QASM, content=body, headers=JSON)
    assert response.status_code == 400 and "'theta' twice" in response.json()["detail"]


def test_an_export_refuses_a_field_it_does_not_know(export: Callable[..., Any]) -> None:
    response = export(BELL, tool="mine")
    assert response.status_code == 422 and response.json()["errors"][0]["path"] == "/tool"


def test_a_slow_export_is_a_422_and_then_the_lane_says_busy(
    client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    monkeypatch.setattr(
        "research_api.adapters.circuits.export_document", lambda *a, **k: release.wait(10) and {}
    )
    client = client_with(client_for, timeout_s=0.2)
    body = {"circuit_json": text(BELL)}
    try:
        first = client.post(QASM, json=body)
        assert first.status_code == 422
        assert first.json()["detail"] == (
            "The work took longer than 0.2 s, so this request was released. "
            "Use the Python function `quantum_sim.to_qasm` for a circuit this size."
        )
        assert client.post(QASM, json=body).status_code == 422
        busy = client.post(QASM, json=body)
        assert busy.status_code == 503 and busy.headers["retry-after"] == "5"
    finally:
        release.set()


def test_a_bad_key_is_a_422_even_when_the_lane_is_busy(
    client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    monkeypatch.setattr(
        "research_api.adapters.circuits.export_document", lambda *a, **k: release.wait(10) and {}
    )
    client = client_with(client_for, timeout_s=0.2)
    body = {"circuit_json": text(BELL)}
    try:
        for _ in range(2):  # two slow exports keep both slots
            assert client.post(QASM, json=body).status_code == 422
        assert client.post(QASM, json=body).status_code == 503
        bad = client.post(QASM, json={**body, "values": {"thetta": "1"}})
        assert bad.status_code == 422 and bad.json()["errors"][0]["path"] == "/values/thetta"
        paper = client.post(paper_url(TELEPORT, "qasm"), json={"values": {"pi": "1"}})
        assert paper.status_code == 422 and paper.json()["errors"][0]["path"] == "/values/pi"
    finally:
        release.set()


# --- OpenQASM 3 export of a paper --------------------------------------------------------------

BELL_BLOCK = (
    "```latex\n"
    "\\begin{quantikz}\n"
    "\\lstick{$\\ket{0}$} & \\gate{H} & \\ctrl{1} & \\qw \\\\\n"
    "\\lstick{$\\ket{0}$} & \\qw & \\targ{} & \\qw\n"
    "\\end{quantikz}\n"
    "```"
)
RY_BLOCK = "```latex\n\\begin{quantikz} & \\gate{R_y(\\theta)} & \\qw \\end{quantikz}\n```"
CIRCUIT_PAPER = FakePaper(
    key="qasm-test",
    title="Two circuits (synthetic)",
    source_name="synthetic-two-circuits.tex",
    source_kind="latex",
    authors=("A. Example",),
    year=2026,
    pages=(
        FakePage(f"Intro.\n\n{BELL_BLOCK}", title="One"),
        FakePage(f"```latex\nno circuit\n```\n\n{RY_BLOCK}", title="Two"),
    ),
)


def client_over(client_for: ClientFor, *papers: FakePaper, **caps: Any) -> TestClient:
    settings = Settings.model_validate({"circuit_caps": caps})
    container = build_container(
        settings,
        fake=True,
        seed=load_seed_users(SEED_FILE),
        cheap_hashing=True,
        document_reader=FakeDocumentReader(papers or PAPERS),
    )
    return sign_in_as_member(client_for(build_app(container)))


def paper_url(paper: FakePaper, what: str) -> str:
    return f"/api/v1/documents/{paper.short_id}/{what}"


def test_the_circuit_blocks_of_a_paper_are_listed(client_for: ClientFor) -> None:
    client = client_over(client_for, CIRCUIT_PAPER)
    response = client.get(paper_url(CIRCUIT_PAPER, "circuits"))
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["doc_id"], body["source_name"]) == (
        CIRCUIT_PAPER.doc_id,
        "synthetic-two-circuits.tex",
    )
    assert [(b["block"], b["status"], len(b["circuits"])) for b in body["blocks"]] == [
        (1, "parsed", 1),
        (2, "unparsed", 0),
        (3, "parsed", 1),
    ]
    assert body["blocks"][1]["latex"] == "no circuit"
    assert body["blocks"][0]["circuits"][0]["format"] == "circuit/1"


def test_a_paper_is_exported_with_docingests_names_and_header(client_for: ClientFor) -> None:
    client = client_over(client_for, CIRCUIT_PAPER)
    response = client.post(paper_url(CIRCUIT_PAPER, "qasm"), json={})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["doc_id"] == CIRCUIT_PAPER.doc_id
    bell, rotation = body["circuits"]
    assert (bell["block"], bell["circuit"], bell["status"]) == (1, 1, "ok")
    (program,) = bell["programs"]
    assert program["filename"] == f"{CIRCUIT_PAPER.short_id}-b01-c01-p01.qasm"
    assert program["text"].startswith(
        "// Generated by docingest (qasm format 1)\n"
        "// source: synthetic-two-circuits.tex, block 1, circuit 1, part 1/1\n"
    )
    assert (rotation["block"], rotation["status"], rotation["programs"]) == (3, "refused", [])
    assert rotation["unbound"] == ["theta"]
    assert body["warnings"] == []


def test_a_papers_values_bind_its_circuits_and_a_stray_key_is_a_warning(
    client_for: ClientFor,
) -> None:
    client = client_over(client_for, CIRCUIT_PAPER)
    body = client.post(
        paper_url(CIRCUIT_PAPER, "qasm"), json={"values": {"theta": "pi/2", "phi": "1"}}
    ).json()
    assert [c["status"] for c in body["circuits"]] == ["ok", "ok"]
    assert body["circuits"][1]["programs"][0]["filename"].endswith("-b03-c01-p01.qasm")
    assert "// bound: theta = pi/2" in body["circuits"][1]["programs"][0]["text"]
    assert body["warnings"] == ["binding 'phi' matches no symbol of any exported circuit"]


def test_a_paper_with_no_circuits_says_so(client_for: ClientFor) -> None:
    client = client_over(client_for)
    listed = client.get(paper_url(TRANSMON, "circuits")).json()
    assert listed["blocks"] == []
    body = client.post(paper_url(TRANSMON, "qasm"), json={"values": None}).json()
    assert body["circuits"] == []
    assert body["warnings"] == [f"{TRANSMON.source_name}: no circuits found"]


def test_the_fake_teleportation_paper_exports_its_bell_pair_and_refuses_its_teleportation(
    client: TestClient,
) -> None:
    listed = client.get(paper_url(TELEPORT, "circuits")).json()
    assert [(b["block"], b["status"], len(b["circuits"])) for b in listed["blocks"]] == [
        (1, "partial", 1),
        (2, "parsed", 1),
    ]
    body = client.post(paper_url(TELEPORT, "qasm"), json={}).json()
    teleport, bell = body["circuits"]
    assert teleport["status"] == "refused" and teleport["programs"] == []
    assert teleport["reasons"] == ["status: the circuit was read as 'partial', not 'parsed'"]
    assert bell["status"] == "ok" and (bell["block"], bell["circuit"]) == (2, 1)
    (program,) = bell["programs"]
    assert program["filename"] == f"{TELEPORT.short_id}-b02-c01-p01.qasm"
    assert program["text"].startswith(
        "// Generated by docingest (qasm format 1)\n"
        "// source: synthetic-teleportation-circuit.tex, block 2, circuit 1, part 1/1\n"
    )
    assert program_lines(program["text"])[-2:] == ["h q[0];", "cx q[0], q[1];"]
    assert body["warnings"] == []


@pytest.mark.parametrize(
    ("doc_id", "status"),
    [("f" * 64, 404), ("f" * 16, 404), ("short", 422), ("A" * 16, 422), ("a" * 65, 422)],
)
@pytest.mark.parametrize("what", ["circuits", "qasm"])
def test_a_missing_or_malformed_paper_is_refused(
    client: TestClient, doc_id: str, status: int, what: str
) -> None:
    url = f"/api/v1/documents/{doc_id}/{what}"
    response = client.post(url, json={}) if what == "qasm" else client.get(url)
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM


@pytest.mark.parametrize("what", ["circuits", "qasm"])
def test_two_papers_that_share_a_prefix_are_a_409(client_for: ClientFor, what: str) -> None:
    reader = FakeDocumentReader(())
    twin_a, twin_b = "ab" + "0" * 14 + "0" * 48, "ab" + "0" * 14 + "1" * 48
    reader._papers = {twin_a: TRANSMON, twin_b: CIRCUIT_PAPER}
    container = build_container(
        Settings.defaults(),
        fake=True,
        seed=load_seed_users(SEED_FILE),
        cheap_hashing=True,
        document_reader=reader,
    )
    client = sign_in_as_member(client_for(build_app(container)))
    url = f"/api/v1/documents/{twin_a[:16]}/{what}"
    response = client.post(url, json={}) if what == "qasm" else client.get(url)
    assert response.status_code == 409 and "more characters" in response.json()["detail"]


def test_bad_values_for_a_paper_are_a_422_that_points_to_the_key(client: TestClient) -> None:
    response = client.post(paper_url(TELEPORT, "qasm"), json={"values": {"thetta": "1"}})
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == "/values/thetta"


def test_a_paper_export_needs_a_body_and_refuses_an_unknown_field(client: TestClient) -> None:
    url = paper_url(TELEPORT, "qasm")
    assert client.post(url, content=b"", headers=JSON).status_code == 422
    response = client.post(url, json={"set": {"theta": 1}})
    assert response.status_code == 422 and response.json()["errors"][0]["path"] == "/set"


def test_a_paper_over_the_operation_cap_points_to_the_docingest_command(
    client_for: ClientFor,
) -> None:
    client = client_over(client_for, CIRCUIT_PAPER, max_ops=2)
    response = client.post(paper_url(CIRCUIT_PAPER, "qasm"), json={"values": {"theta": 1}})
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == (
        "The paper's circuits ask for 3 operations; this endpoint takes at most 2. "
        "Use `docingest circuits export --doc` on the command line for a paper this size."
    )
    assert "errors" not in body
    assert client.get(paper_url(CIRCUIT_PAPER, "circuits")).status_code == 200


def test_a_paper_export_body_over_the_cap_is_a_413(client_for: ClientFor) -> None:
    client = client_over(client_for, CIRCUIT_PAPER, max_body_bytes=50)
    response = client.post(paper_url(CIRCUIT_PAPER, "qasm"), json={"values": {"t" * 60: 1}})
    assert response.status_code == 413
    assert response.json()["detail"] == (
        "The request body is over 50 bytes. "
        "Use `docingest circuits export --doc` on the command line for a paper this size."
    )


def test_a_slow_reading_of_a_papers_blocks_points_to_the_docingest_command(
    client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    monkeypatch.setattr(
        "research_api.adapters.circuits.circuit_code_blocks", lambda *a: release.wait(10) and ()
    )
    client = client_over(client_for, CIRCUIT_PAPER, timeout_s=0.2)
    try:
        response = client.get(paper_url(CIRCUIT_PAPER, "circuits"))
        assert response.status_code == 422
        assert response.json()["detail"] == (
            "The work took longer than 0.2 s, so this request was released. "
            "Use `docingest circuits export --doc` on the command line for a paper this size."
        )
    finally:
        release.set()


def test_a_paper_export_takes_exactly_one_lane_slot(
    client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken: list[str] = []
    run = ComputeLane.run

    async def counted(self: ComputeLane, work: Any, **kwargs: Any) -> Any:
        taken.append("slot")
        return await run(self, work, **kwargs)

    monkeypatch.setattr(ComputeLane, "run", counted)
    client = client_over(client_for, CIRCUIT_PAPER)
    response = client.post(paper_url(CIRCUIT_PAPER, "qasm"), json={"values": {"theta": 1}})
    assert response.status_code == 200
    assert [c["status"] for c in response.json()["circuits"]] == ["ok", "ok"]
    assert taken == ["slot"]


def test_a_slow_paper_times_out_as_a_whole_and_points_to_the_docingest_command(
    client_for: ClientFor, monkeypatch: pytest.MonkeyPatch
) -> None:
    # each circuit alone is within the time limit; the two together are not
    def slow(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.3)
        return export_document(*args, **kwargs)

    monkeypatch.setattr("research_api.adapters.circuits.export_document", slow)
    client = client_over(client_for, CIRCUIT_PAPER, timeout_s=0.5)
    response = client.post(paper_url(CIRCUIT_PAPER, "qasm"), json={"values": {"theta": 1}})
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "The work took longer than 0.5 s, so this request was released. "
        "Use `docingest circuits export --doc` on the command line for a paper this size."
    )
    time.sleep(0.3)  # the job's thread ends on its own and gives its slot back


def test_the_contract_types_the_export_answers() -> None:
    spec = json.loads(openapi_document())
    schemas = spec["components"]["schemas"]
    assert set(schemas["QasmResponse"]["properties"]) == {
        "status",
        "programs",
        "reasons",
        "notes",
        "symbols",
        "unmatched",
        "unbound",
    }
    assert set(schemas["QasmProgramOut"]["properties"]) == {
        "filename",
        "part",
        "sign",
        "text",
        "n_qubits",
        "n_clbits",
    }
    assert set(schemas["PaperCircuitQasmOut"]["properties"]) == {
        "block",
        "circuit",
        "status",
        "programs",
        "reasons",
        "notes",
        "unbound",
    }
    assert set(schemas["PaperQasmResponse"]["properties"]) == {
        "doc_id",
        "source_name",
        "circuits",
        "warnings",
    }
    assert set(schemas["PaperCircuitsResponse"]["properties"]) == {
        "doc_id",
        "source_name",
        "blocks",
    }
    paths = spec["paths"]
    exported = paths["/api/v1/circuits/qasm"]["post"]["responses"]
    assert exported["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/QasmResponse"
    }
    for status in ("400", "413", "422", "503"):
        assert PROBLEM in exported[status]["content"]
    paper = paths["/api/v1/documents/{doc_id}/qasm"]["post"]["responses"]
    for status in ("404", "409", "413", "422", "503"):
        assert PROBLEM in paper[status]["content"]
    listed = paths["/api/v1/documents/{doc_id}/circuits"]["get"]["responses"]
    for responses, hint in (
        (exported, "`quantum_sim.to_qasm`"),
        (paper, "`docingest circuits"),
        (listed, "`docingest circuits"),
    ):
        for status in ("413", "422"):
            assert hint in responses[status]["description"], status
            assert "`quantum-sim` command" not in responses[status]["description"], status
    assert "/values/<key>" in exported["422"]["description"]
    assert "/values/<key>" in paper["422"]["description"]
    assert "together" in paper["422"]["description"]
    run = paths["/api/v1/circuits/run"]["post"]["responses"]
    assert "`quantum-sim` command" in run["422"]["description"]


def test_values_with_more_than_64_keys_are_a_422_on_every_route_that_takes_them(
    client: TestClient,
) -> None:
    many = {f"theta_{{{n}}}": 1 for n in range(65)}
    fits = {f"theta_{{{n}}}": 1 for n in range(64)}
    for url, body in (
        (QASM, {"circuit_json": text(ry())}),
        (paper_url(CIRCUIT_PAPER, "qasm"), {}),
        ("/api/v1/circuits/run", {"circuit_json": text(ry())}),
        ("/api/v1/circuits/check", {"circuit_json": text(ry())}),
    ):
        response = client.post(url, json={**body, "values": many})
        assert response.status_code == 422, url
        assert response.headers["content-type"] == PROBLEM
        assert response.json()["errors"][0]["path"] == "/values"
        assert client.post(url, json={**body, "values": fits}).status_code != 422, url


def test_a_key_or_a_value_of_more_than_500_characters_is_a_422(client: TestClient) -> None:
    long_key = client.post(QASM, json={"circuit_json": text(ry()), "values": {"t" * 501: 1}})
    long_value = client.post(
        QASM, json={"circuit_json": text(ry()), "values": {"theta": "1" * 501}}
    )
    assert long_key.status_code == long_value.status_code == 422
    assert (
        long_key.json()["errors"][0]["path"] == long_value.json()["errors"][0]["path"] == "/values"
    )
