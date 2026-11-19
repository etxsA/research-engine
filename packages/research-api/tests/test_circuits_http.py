from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import BELL, SEED_FILE, sign_in_as_member

from research_api.bootstrap import Settings, build_container
from research_api.bootstrap.seed import load_seed_users
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
    assert "200 bytes" in response.json()["detail"]


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
        assert first.status_code == 422 and "longer than 0.2 s" in first.json()["detail"]
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


def test_the_contract_lists_the_four_operations() -> None:
    spec = json.loads(openapi_document())
    operations = {
        (method, path): item["operationId"]
        for path, methods in spec["paths"].items()
        for method, item in methods.items()
        if path.startswith("/api/v1/circuits")
    }
    assert operations == {
        ("post", "/api/v1/circuits/parse"): "parseCircuit",
        ("post", "/api/v1/circuits/run"): "runCircuit",
        ("post", "/api/v1/circuits/check"): "checkCircuit",
        ("get", "/api/v1/circuits/conventions"): "getCircuitConventions",
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
