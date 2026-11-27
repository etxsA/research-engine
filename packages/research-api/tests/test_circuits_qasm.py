"""The OpenQASM 3 export use cases, over a scripted engine and a scripted paper reader."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from helpers import arun

from research_api.application.circuits import (
    ExportPaperQasm,
    ExportQasm,
    ListPaperCircuits,
)
from research_api.domain.circuits import CircuitCaps, QasmExport, QasmProgram
from research_api.domain.documents import DocumentText
from research_api.domain.errors import (
    BadRequestError,
    ConflictError,
    FieldError,
    InvalidInputError,
    NotFoundError,
)
from research_api.ports.circuits import CircuitQasm, GateListReading, LatexBlock

DOC_ID = "0123456789abcdef" + "a" * 48
NAME = "paper.tex"


def circuit(label: str, ops: int = 1, wires: int = 1) -> dict[str, Any]:
    step = [{"kind": "gate", "label": label, "targets": [1]}]
    return {"format": "circuit/1", "wires": wires, "parts": [[step] * ops]}


def ok(label: str, parts: int = 1, **seen: Any) -> QasmExport:
    programs = tuple(
        QasmProgram(n, "=" if n > 1 else None, f"// {label} part {n}\n", 1, 0)
        for n in range(1, parts + 1)
    )
    return QasmExport("ok", programs, (), **seen)


def refused(reason: str, **seen: Any) -> QasmExport:
    return QasmExport("refused", (), (reason,), **seen)


@dataclass
class ScriptedEngine:
    """Answers ``qasm`` by the label of the circuit's first gate; records every call."""

    answers: dict[str, QasmExport] = field(default_factory=dict)
    found: tuple[LatexBlock, ...] = ()
    bad_values: InvalidInputError | None = None
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    async def blocks(self, markdown: str) -> tuple[LatexBlock, ...]:
        self.calls.append(("blocks", markdown))
        return self.found

    async def check_values(self, values: Mapping[str, Any] | None) -> None:
        self.calls.append(("check_values", values))
        if self.bad_values is not None:
            raise self.bad_values

    async def qasm(
        self,
        document: object,
        *,
        values: Mapping[str, Any] | None = None,
        origin: str = "",
        tool: str,
    ) -> QasmExport:
        self.calls.append(("qasm", document, values, origin, tool))
        return self._answer(document)

    async def qasm_blocks(
        self,
        markdown: str,
        *,
        values: Mapping[str, Any] | None = None,
        origin: Callable[[int, int], str],
        tool: str,
        admit: Callable[[Sequence[object]], None],
    ) -> tuple[CircuitQasm, ...]:
        """One call, as the adapter's one lane job: the circuits of ``found``, admitted before
        any is exported; each export is recorded as a ``qasm`` call would be."""
        self.calls.append(("qasm_blocks", markdown))
        found = [
            (b, c, document)
            for b, block in enumerate(self.found, 1)
            for c, document in enumerate(block.reading.circuits, 1)
        ]
        admit([document for _, _, document in found])
        done = []
        for b, c, document in found:
            self.calls.append(("qasm", document, values, origin(b, c), tool))
            done.append(CircuitQasm(b, c, self._answer(document)))
        return tuple(done)

    def _answer(self, document: object) -> QasmExport:
        assert isinstance(document, dict)
        return self.answers[document["parts"][0][0][0]["label"]]

    def named(self, name: str) -> list[tuple[Any, ...]]:
        return [call for call in self.calls if call[0] == name]


class ScriptedReader:
    """One paper; a lookup by a prefix of its id finds it. Records the thread of each call."""

    def __init__(self, markdown: str = "the text", error: Exception | None = None) -> None:
        self.markdown, self.error = markdown, error
        self.threads: list[int] = []
        self.asked: list[str] = []

    def text(self, doc_id: str) -> DocumentText:
        self.threads.append(threading.get_ident())
        self.asked.append(doc_id)
        if self.error is not None:
            raise self.error
        if not DOC_ID.startswith(doc_id):
            raise NotFoundError("No such document.")
        return DocumentText(DOC_ID, NAME, self.markdown)

    def search(self, query: str, limit: int) -> Any:
        raise AssertionError("not used")

    def get(self, doc_id: str) -> Any:
        raise AssertionError("not used")

    def page(self, doc_id: str, page: int) -> Any:
        raise AssertionError("not used")


def block(latex: str, *circuits: dict[str, Any], status: str = "parsed") -> LatexBlock:
    return LatexBlock(latex, GateListReading(status, "gate list", tuple(circuits)))


CAPS = CircuitCaps(max_ops=10)


def exporter(engine: ScriptedEngine, reader: ScriptedReader | None = None) -> ExportPaperQasm:
    return ExportPaperQasm(engine=engine, reader=reader or ScriptedReader(), caps=CAPS)


# --- a pasted circuit ----------------------------------------------------------------------


@arun
async def test_a_pasted_circuit_is_exported_with_the_services_name_and_no_source() -> None:
    engine = ScriptedEngine(answers={"H": ok("H", parts=2, symbols=("theta",))})
    done = await ExportQasm(engine=engine, caps=CAPS)(
        circuit_json=json.dumps(circuit("H")), values={"theta": "pi"}
    )
    assert [call[0] for call in engine.calls] == ["check_values", "qasm"]
    assert engine.named("qasm") == [("qasm", circuit("H"), {"theta": "pi"}, "", "research-api")]
    assert [f.filename for f in done.files] == ["circuit-p01.qasm", "circuit-p02.qasm"]
    assert [f.program for f in done.files] == list(done.export.programs)
    assert done.export.symbols == ("theta",)


@arun
async def test_a_refused_pasted_circuit_has_its_reasons_and_no_files() -> None:
    engine = ScriptedEngine(answers={"U": refused("part 1: gate 'u' has no name")})
    done = await ExportQasm(engine=engine, caps=CAPS)(circuit_json=json.dumps(circuit("U")))
    assert done.export.status == "refused" and done.files == ()
    assert done.export.reasons == ("part 1: gate 'u' has no name",)


@arun
async def test_a_pasted_circuit_has_no_wire_cap_but_the_operation_cap() -> None:
    engine = ScriptedEngine(answers={"H": ok("H")})
    export = ExportQasm(engine=engine, caps=CAPS)
    await export(circuit_json=json.dumps(circuit("H", ops=10, wires=40)))
    with pytest.raises(InvalidInputError, match="11 operations") as stop:
        await export(circuit_json=json.dumps(circuit("H", ops=11)))
    assert stop.value.errors[0].path == "/circuit_json"
    assert len(engine.named("qasm")) == 1


@arun
async def test_a_pasted_circuit_with_a_key_written_twice_is_a_400() -> None:
    engine = ScriptedEngine()
    with pytest.raises(BadRequestError, match="twice"):
        await ExportQasm(engine=engine, caps=CAPS)(circuit_json='{"wires": 1, "wires": 2}')
    assert engine.calls == []


@arun
async def test_bad_values_stop_a_pasted_export_before_the_emitter() -> None:
    bad = InvalidInputError("nope", (FieldError("/values/thetta", "nope"),))
    engine = ScriptedEngine(bad_values=bad)
    with pytest.raises(InvalidInputError) as stop:
        await ExportQasm(engine=engine, caps=CAPS)(
            circuit_json=json.dumps(circuit("H")), values={"thetta": "1"}
        )
    assert stop.value is bad and engine.named("qasm") == []


# --- a paper ---------------------------------------------------------------------------------


@arun
async def test_a_paper_is_exported_circuit_by_circuit_as_docingest_numbers_them() -> None:
    engine = ScriptedEngine(
        answers={"A": ok("A"), "B": ok("B", parts=2), "C": ok("C")},
        found=(
            block("first", circuit("A")),
            block("no circuit", status="unparsed"),
            block("third", circuit("B"), circuit("C")),
        ),
    )
    reader = ScriptedReader("the whole text")
    done = await exporter(engine, reader)(DOC_ID[:16], values={"theta": "1"})
    assert reader.asked == [DOC_ID[:16]]
    assert [call[0] for call in engine.calls if call[0] != "qasm"] == [
        "check_values",
        "qasm_blocks",
    ]
    assert engine.named("qasm_blocks") == [("qasm_blocks", "the whole text")]
    assert [(c[2], c[3], c[4]) for c in engine.named("qasm")] == [
        ({"theta": "1"}, "paper.tex, block 1, circuit 1", "docingest"),
        ({"theta": "1"}, "paper.tex, block 3, circuit 1", "docingest"),
        ({"theta": "1"}, "paper.tex, block 3, circuit 2", "docingest"),
    ]
    assert (done.doc_id, done.source_name) == (DOC_ID, NAME)
    assert [(c.block, c.circuit) for c in done.circuits] == [(1, 1), (3, 1), (3, 2)]
    assert [[f.filename for f in c.files] for c in done.circuits] == [
        ["0123456789abcdef-b01-c01-p01.qasm"],
        ["0123456789abcdef-b03-c01-p01.qasm", "0123456789abcdef-b03-c01-p02.qasm"],
        ["0123456789abcdef-b03-c02-p01.qasm"],
    ]


@arun
async def test_a_refused_circuit_is_kept_next_to_the_exported_ones() -> None:
    engine = ScriptedEngine(
        answers={
            "A": ok("A"),
            "R": refused("part 1: unbound parameter(s): theta", unbound=("theta",)),
        },
        found=(block("one", circuit("R"), circuit("A")),),
    )
    done = await exporter(engine)(DOC_ID)
    first, second = done.circuits
    assert (first.export.status, first.files, first.export.unbound) == ("refused", (), ("theta",))
    assert first.export.reasons == ("part 1: unbound parameter(s): theta",)
    assert second.export.status == "ok" and len(second.files) == 1
    assert done.warnings == ()


@arun
async def test_each_circuits_notes_are_warnings_that_name_where_they_come_from() -> None:
    engine = ScriptedEngine(
        answers={"A": ok("A", notes=("a note", "another")), "B": ok("B", notes=("b note",))},
        found=(block("one", circuit("A")), block("two", circuit("B"))),
    )
    done = await exporter(engine)(DOC_ID)
    assert done.warnings == (
        "paper.tex, block 1, circuit 1: a note",
        "paper.tex, block 1, circuit 1: another",
        "paper.tex, block 2, circuit 1: b note",
    )
    assert done.circuits[0].export.notes == ("a note", "another")


@arun
async def test_a_key_no_circuit_uses_is_a_warning_and_one_some_circuit_uses_is_not() -> None:
    engine = ScriptedEngine(
        answers={
            "A": ok("A", unmatched=("zeta", "phi", "\\psi")),
            "B": ok("B", unmatched=("zeta", "theta", "\\psi")),
        },
        found=(block("one", circuit("A"), circuit("B")),),
    )
    values = {"theta": "1", "phi": "2", "zeta": "3", "\\psi": "4"}
    done = await exporter(engine)(DOC_ID, values=values)
    assert done.warnings == (
        "binding '\\\\psi' matches no symbol of any exported circuit",
        "binding 'zeta' matches no symbol of any exported circuit",
    )


@arun
async def test_a_paper_with_no_circuits_says_so_and_every_key_is_unmatched() -> None:
    engine = ScriptedEngine(found=(block("not a circuit", status="unparsed"),))
    done = await exporter(engine)(DOC_ID, values={"theta": "1"})
    assert done.circuits == ()
    assert done.warnings == (
        "paper.tex: no circuits found",
        "binding 'theta' matches no symbol of any exported circuit",
    )
    nothing = await exporter(ScriptedEngine())(DOC_ID)
    assert nothing.warnings == ("paper.tex: no circuits found",)


@arun
async def test_the_circuits_of_a_paper_share_one_operation_budget() -> None:
    engine = ScriptedEngine(
        answers={"A": ok("A")},
        found=(block("one", circuit("A", ops=6)), block("two", circuit("A", ops=5))),
    )
    with pytest.raises(InvalidInputError, match="docingest circuits export --doc") as stop:
        await exporter(engine)(DOC_ID)
    assert "11 operations" in stop.value.detail
    assert engine.named("qasm") == []


@arun
async def test_bad_values_stop_a_paper_export_before_the_paper_is_read() -> None:
    bad = InvalidInputError("nope", (FieldError("/values/pi", "nope"),))
    reader = ScriptedReader()
    with pytest.raises(InvalidInputError):
        await exporter(ScriptedEngine(bad_values=bad), reader)(DOC_ID, values={"pi": "1"})
    assert reader.asked == []


@pytest.mark.parametrize("doc_id", ["short", "A" * 16, "g" * 16, "../etc/passwd", ""])
@arun
async def test_an_id_that_is_not_a_document_id_is_not_found_without_a_read(doc_id: str) -> None:
    reader = ScriptedReader()
    with pytest.raises(NotFoundError):
        await exporter(ScriptedEngine(), reader)(doc_id)
    with pytest.raises(NotFoundError):
        await ListPaperCircuits(engine=ScriptedEngine(), reader=reader)(doc_id)
    assert reader.asked == []


@pytest.mark.parametrize("error", [NotFoundError("No such document."), ConflictError("two")])
@arun
async def test_the_readers_refusal_reaches_the_caller(error: Exception) -> None:
    reader = ScriptedReader(error=error)
    with pytest.raises(type(error)):
        await exporter(ScriptedEngine(), reader)(DOC_ID)
    with pytest.raises(type(error)):
        await ListPaperCircuits(engine=ScriptedEngine(), reader=reader)(DOC_ID)


@arun
async def test_the_paper_is_read_off_the_event_loop_thread() -> None:
    reader = ScriptedReader()
    await exporter(ScriptedEngine(), reader)(DOC_ID)
    await ListPaperCircuits(engine=ScriptedEngine(), reader=reader)(DOC_ID)
    assert threading.get_ident() not in reader.threads and len(reader.threads) == 2


# --- the circuits of a paper -----------------------------------------------------------------


@arun
async def test_the_blocks_of_a_paper_are_listed_from_one_with_their_circuits() -> None:
    engine = ScriptedEngine(
        found=(
            block("\\begin{quantikz}A", circuit("A")),
            block("no circuit", status="unparsed"),
            block("two", circuit("B"), circuit("C"), status="partial"),
        )
    )
    reader = ScriptedReader("the whole text")
    listed = await ListPaperCircuits(engine=engine, reader=reader)(DOC_ID[:20])
    assert (listed.doc_id, listed.source_name) == (DOC_ID, NAME)
    assert [(b.block, b.latex, b.status, len(b.circuits)) for b in listed.blocks] == [
        (1, "\\begin{quantikz}A", "parsed", 1),
        (2, "no circuit", "unparsed", 0),
        (3, "two", "partial", 2),
    ]
    assert listed.blocks[2].circuits == (circuit("B"), circuit("C"))
    assert engine.named("blocks") == [("blocks", "the whole text")]
