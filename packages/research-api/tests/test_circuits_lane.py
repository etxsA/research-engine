from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import pytest
from docingest.adapters.qasm import QuantumSimExporter
from docingest.domain.circuits import circuit_code_blocks, gate_list
from quantum_sim.qasm import to_qasm, validate_bindings

from research_api.adapters.circuits import CircuitsAdapter, ComputeLane
from research_api.application.circuits import CheckCircuit
from research_api.domain.circuits import CheckTarget, CircuitCaps
from research_api.domain.errors import InvalidInputError, UnavailableError


def test_a_result_comes_back() -> None:
    assert asyncio.run(ComputeLane(timeout_s=5).run(lambda: 41 + 1)) == 42


def test_an_exception_of_the_work_reaches_the_caller() -> None:
    def fail() -> int:
        raise ValueError("inside")

    with pytest.raises(ValueError, match="inside"):
        asyncio.run(ComputeLane(timeout_s=5).run(fail))


def test_the_work_does_not_run_on_the_event_loop_thread() -> None:
    caller = threading.get_ident()
    assert asyncio.run(ComputeLane(timeout_s=5).run(threading.get_ident)) != caller


def test_work_that_takes_too_long_is_a_422_and_keeps_its_slot_until_it_ends() -> None:
    release = threading.Event()
    lane = ComputeLane(timeout_s=0.2, max_busy=1)

    async def scenario() -> None:
        with pytest.raises(InvalidInputError, match=r"longer than 0\.2 s"):
            await lane.run(lambda: release.wait(10))
        # the thread is still running, so the only slot is taken
        with pytest.raises(UnavailableError) as busy:
            await lane.run(lambda: 1)
        assert busy.value.retry_after_s == 5
        release.set()
        for _ in range(100):  # the slot comes back once the thread has ended
            try:
                assert await lane.run(lambda: 7) == 7
                return
            except UnavailableError:
                await asyncio.sleep(0.02)
        raise AssertionError("the slot never came back")

    asyncio.run(scenario())


def test_calls_up_to_the_limit_run_together() -> None:
    both_in = threading.Barrier(2, timeout=5)
    lane = ComputeLane(timeout_s=5, max_busy=2)

    async def scenario() -> list[int]:
        return await asyncio.gather(lane.run(both_in.wait), lane.run(both_in.wait))

    assert sorted(asyncio.run(scenario())) == [0, 1]


def test_a_finished_call_after_the_loop_closed_is_harmless() -> None:
    started = threading.Event()
    release = threading.Event()
    lane = ComputeLane(timeout_s=5)

    def work() -> int:
        started.set()
        release.wait(5)
        return 1

    async def scenario() -> None:
        task = asyncio.ensure_future(lane.run(work))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    release.set()
    time.sleep(0.1)  # the thread ends against a closed loop; nothing must blow up


def test_the_adapter_reads_latex_and_simulates() -> None:
    engine = CircuitsAdapter(timeout_s=30)
    latex = r"\begin{quantikz} \lstick{$\ket{0}$} & \gate{X} & \qw \end{quantikz}"
    reading = asyncio.run(engine.parse(latex))
    assert reading.status == "parsed"
    assert reading.circuits[0]["format"] == "circuit/1"
    result = asyncio.run(engine.run(reading.circuits[0]))
    assert result["status"] == "ok"
    assert result["circuits"][0]["probabilities"]["values"] == [0.0, 1.0]


def test_a_request_shaped_document_is_not_taken_for_a_request() -> None:
    engine = CircuitsAdapter(timeout_s=30)
    bell = {
        "format": "circuit/1",
        "wires": 1,
        "parts": [[[{"kind": "gate", "label": "X", "targets": [1]}]]],
    }
    result = asyncio.run(engine.run({"circuit": bell, "input": "1"}))
    assert result["status"] == "refused"


def test_conventions_come_from_quantum_sim() -> None:
    body = CircuitsAdapter(timeout_s=30).conventions()
    assert {"version", "conventions", "gates", "limits"} <= set(body)


def test_a_check_with_an_empty_target_is_refused_before_any_work() -> None:
    check = CheckCircuit(engine=CircuitsAdapter(timeout_s=1), caps=CircuitCaps())
    with pytest.raises(InvalidInputError, match="needs one of"):
        asyncio.run(check(circuit_json="{}", target=CheckTarget()))


# --- the OpenQASM 3 export ---------------------------------------------------------------------

BELL_LATEX = (
    r"\begin{quantikz} \lstick{$\ket{0}$} & \gate{H} & \ctrl{1} & \qw \\"
    r" \lstick{$\ket{0}$} & \qw & \targ{} & \qw \end{quantikz}"
)


def one_gate(label: str, legend: str = "") -> str:
    return rf"\begin{{quantikz}} {legend} & \gate{{{label}}} & \qw \end{{quantikz}}"


def drawn(latex: str) -> dict[str, Any]:
    """The first circuit of ``latex`` as a ``circuit/1`` document, as docingest reads it."""
    return gate_list(latex).circuits[0].to_dict()


def test_the_adapter_exports_what_quantum_sim_exports() -> None:
    document = drawn(BELL_LATEX)
    export = asyncio.run(
        CircuitsAdapter(timeout_s=30).qasm(document, origin="here", tool="research-api")
    )
    direct = to_qasm(document, origin="here", tool="research-api")
    assert export.status == "ok" and export.reasons == () and export.unbound == ()
    assert [(p.part, p.sign, p.text, p.n_qubits, p.n_clbits) for p in export.programs] == [
        (p.part, p.sign, p.text, p.n_qubits, p.n_clbits) for p in direct.programs
    ]
    assert export.programs[0].text.startswith("// Generated by research-api (qasm format 1)\n")
    assert "// source: here, part 1/1\n" in export.programs[0].text
    assert "cx q[0], q[1];" in export.programs[0].text


def test_no_origin_writes_no_source_line() -> None:
    export = asyncio.run(CircuitsAdapter(timeout_s=30).qasm(drawn(BELL_LATEX), tool="research-api"))
    assert "// source:" not in export.programs[0].text


def test_a_refusal_of_the_emitter_is_data() -> None:
    export = asyncio.run(
        CircuitsAdapter(timeout_s=30).qasm({"wires": 1, "parts": []}, tool="research-api")
    )
    assert export.status == "refused" and export.programs == () and export.reasons


@pytest.mark.parametrize(
    ("latex", "values"),
    [
        (one_gate(r"R_y(\theta)"), None),
        (one_gate(r"R_y(\theta_{12})"), None),
        (one_gate(r"R_y(\frac{1}{\theta - \phi})"), None),
        (one_gate(r"R_y(\frac{1}{\theta - \phi})"), {"theta": "pi/3"}),
        (one_gate(r"R_y(\sqrt{\theta - 1})"), None),
        (one_gate(r"R_y(\theta)", r"\lstick{$\ket{1}$}"), None),
        (one_gate(r"R_y(\theta)"), {"phi": "1"}),
        (one_gate(r"R_y(\theta)"), {"theta": "pi/4"}),
        (one_gate(r"R_y(\frac{1}{\theta})"), {"theta": "0"}),
        (BELL_LATEX, {"theta": "1"}),
    ],
)
def test_the_export_and_its_unbound_names_are_docingests(
    latex: str, values: dict[str, str] | None
) -> None:
    steps = gate_list(latex).circuits[0]
    ours = asyncio.run(
        CircuitsAdapter(timeout_s=30).qasm(
            steps.to_dict(), values=values, origin="p.tex, block 1, circuit 1", tool="docingest"
        )
    )
    theirs = QuantumSimExporter().export(
        steps, values=values, origin="p.tex, block 1, circuit 1", tool="docingest"
    )
    assert ours.unbound == theirs.unbound
    assert (ours.status, ours.reasons, ours.notes, ours.symbols, ours.unmatched) == (
        theirs.status,
        theirs.reasons,
        theirs.notes,
        theirs.symbols,
        theirs.unmatched,
    )
    assert [p.text for p in ours.programs] == [p.text for p in theirs.programs]


def test_unbound_names_a_key_that_the_binding_rule_takes() -> None:
    adapter = CircuitsAdapter(timeout_s=30)
    export = asyncio.run(adapter.qasm(drawn(one_gate(r"R_y(\theta_{12})")), tool="docingest"))
    assert export.unbound == ("theta_{12}",)
    singular = asyncio.run(adapter.qasm(drawn(one_gate(r"R_y(\sqrt{\theta - 1})")), tool="x"))
    assert singular.status == "refused" and singular.unbound == ()


def test_blocks_are_the_latex_fences_of_the_whole_text_in_order() -> None:
    markdown = (
        "<!-- page 1 | method=latex -->\n"
        "```python\n```latex\nnot a block\n```\n\n"
        f"```latex\n{BELL_LATEX}\n```\n\n"
        "<!-- page 2 | method=latex -->\n"
        "```latex\nno circuit here\n```\n\n"
        f"````latex\n{one_gate('X')}\n{one_gate('Z')}\n````\n\n"
        "```latex\nnever closed\n"
    )
    blocks = asyncio.run(CircuitsAdapter(timeout_s=30).blocks(markdown))
    expected = circuit_code_blocks(markdown)
    assert len(blocks) == len(expected) == 3
    for ours, theirs in zip(blocks, expected, strict=True):
        found = gate_list(theirs.source)
        assert ours.source == theirs.source
        assert ours.reading.status == found.status
        assert list(ours.reading.circuits) == [c.to_dict() for c in found.circuits]
    assert [len(b.reading.circuits) for b in blocks] == [1, 0, 2]
    assert blocks[1].reading.status == "unparsed"


def test_text_with_no_latex_fence_has_no_blocks() -> None:
    assert asyncio.run(CircuitsAdapter(timeout_s=30).blocks("plain text $x$")) == ()


RY_THETA = one_gate(r"R_y(\theta)")
TWO_BLOCKS = (
    f"```latex\n{BELL_LATEX}\n```\n\n```latex\nno circuit\n```\n\n"
    f"````latex\n{RY_THETA}\n{one_gate('X')}\n````\n"
)


def test_the_circuits_of_a_text_are_exported_in_one_job_as_blocks_and_qasm_export_them() -> None:
    adapter = CircuitsAdapter(timeout_s=30)
    admitted: list[list[object]] = []
    done = asyncio.run(
        adapter.qasm_blocks(
            TWO_BLOCKS,
            values={"theta": "pi/4"},
            origin=lambda b, c: f"p.tex, block {b}, circuit {c}",
            tool="docingest",
            admit=lambda documents: admitted.append(list(documents)),
        )
    )
    blocks = asyncio.run(adapter.blocks(TWO_BLOCKS))
    documents = [
        (b, c, d) for b, k in enumerate(blocks, 1) for c, d in enumerate(k.reading.circuits, 1)
    ]
    assert admitted == [[d for _, _, d in documents]]
    assert [(x.block, x.circuit) for x in done] == [(1, 1), (3, 1), (3, 2)]
    for (b, c, document), ours in zip(documents, done, strict=True):
        alone = asyncio.run(
            adapter.qasm(
                document,
                values={"theta": "pi/4"},
                origin=f"p.tex, block {b}, circuit {c}",
                tool="docingest",
            )
        )
        assert ours.export == alone


def test_a_job_its_admission_refuses_exports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    exported: list[object] = []
    monkeypatch.setattr(
        "research_api.adapters.circuits.to_qasm", lambda *a, **k: exported.append(a)
    )

    def refuse(documents: Any) -> None:
        raise InvalidInputError("too many")

    with pytest.raises(InvalidInputError, match="too many"):
        asyncio.run(
            CircuitsAdapter(timeout_s=30).qasm_blocks(
                TWO_BLOCKS, origin=lambda b, c: "", tool="x", admit=refuse
            )
        )
    assert exported == []


@pytest.mark.parametrize("values", [None, {}, {"theta": "pi/4", "phi": 0.5, "psi": 2}])
def test_values_the_export_can_use_pass(values: dict[str, Any] | None) -> None:
    asyncio.run(CircuitsAdapter(timeout_s=30).check_values(values))


@pytest.mark.parametrize(
    ("values", "path"),
    [
        ({"theta": "1", "thetta": "1"}, "/values/thetta"),
        ({"pi": "1"}, "/values/pi"),
        ({"theta": "pi pi ?"}, "/values/theta"),
        ({"theta": "\\phi"}, "/values/theta"),
        ({"theta": True}, "/values/theta"),
        ({"theta": 10**400}, "/values/theta"),
        ({"a/b~c": "1"}, "/values/a~1b~0c"),
        ({"theta": "1", "phi": "2", "\\theta": "3"}, "/values/\\theta"),
    ],
)
def test_a_value_the_export_cannot_use_is_a_422_that_points_to_its_key(
    values: dict[str, Any], path: str
) -> None:
    with pytest.raises(InvalidInputError) as refused:
        asyncio.run(CircuitsAdapter(timeout_s=30).check_values(values))
    with pytest.raises(ValueError, match=r"\w") as emitter:
        validate_bindings(values)
    assert [e.path for e in refused.value.errors] == [path]
    assert refused.value.detail == refused.value.errors[0].message == str(emitter.value)


def test_a_symbol_bound_twice_is_named_with_the_emitters_words() -> None:
    with pytest.raises(InvalidInputError) as refused:
        asyncio.run(CircuitsAdapter(timeout_s=30).check_values({"theta": 1, "{\\theta}": 2}))
    assert refused.value.detail == "parameter 'theta' is bound twice"
    assert refused.value.errors[0].path == "/values/{\\theta}"


def blocking(release: threading.Event) -> Any:
    def wait(*args: Any, **kwargs: Any) -> Any:
        release.wait(10)
        raise AssertionError("released")

    return wait


EXPORT_CALLS = {
    "qasm": ("to_qasm", lambda a: a.qasm({}, tool="x")),
    "blocks": ("circuit_code_blocks", lambda a: a.blocks("text")),
    "qasm_blocks": (
        "circuit_code_blocks",
        lambda a: a.qasm_blocks("text", origin=lambda b, c: "", tool="x", admit=lambda d: None),
    ),
}


@pytest.mark.parametrize("call", EXPORT_CALLS)
def test_the_lane_bounds_the_export_calls_and_their_time(
    call: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    name, start = EXPORT_CALLS[call]
    release = threading.Event()
    monkeypatch.setattr(f"research_api.adapters.circuits.{name}", blocking(release))
    adapter = CircuitsAdapter(timeout_s=0.2, max_busy=1)

    async def scenario() -> None:
        with pytest.raises(InvalidInputError, match=r"longer than 0\.2 s") as slow:
            await start(adapter)
        hint = "docingest circuits export --doc" if call == "qasm_blocks" else "quantum-sim"
        assert hint in slow.value.detail
        with pytest.raises(UnavailableError):  # the slow thread still holds the only slot
            await adapter.parse("x")

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_checking_values_takes_no_lane_slot_and_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    monkeypatch.setattr("research_api.adapters.circuits.gate_list", blocking(release))
    threads: list[int] = []
    checked = validate_bindings

    def recorded(values: Any) -> None:
        threads.append(threading.get_ident())
        checked(values)

    monkeypatch.setattr("research_api.adapters.circuits.validate_bindings", recorded)
    adapter = CircuitsAdapter(timeout_s=0.2, max_busy=1)

    async def scenario() -> None:
        with pytest.raises(InvalidInputError, match=r"longer than 0\.2 s"):
            await adapter.parse("x")  # the slow thread keeps the only slot
        with pytest.raises(UnavailableError):
            await adapter.parse("x")
        await adapter.check_values({"theta": "pi/4"})
        with pytest.raises(InvalidInputError) as refused:
            await adapter.check_values({"thetta": "1"})
        assert refused.value.errors[0].path == "/values/thetta"

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert threads and threading.get_ident() not in threads
