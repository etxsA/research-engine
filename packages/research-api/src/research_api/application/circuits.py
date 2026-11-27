"""Use cases of the circuit check: read LaTeX, simulate, compare, export as OpenQASM 3.

The OpenQASM export never writes or deletes a file: it returns the programs. A pasted circuit is
exported alone (``circuit-pNN.qasm``). A paper is exported as ``docingest circuits export --doc``
exports it: every circuit of every fenced ``latex`` block of its whole text, the same file names
and the same bytes, and the per-paper notes docingest gives for one document (each circuit's
notes, "no circuits found", a binding key that matches no symbol of any exported circuit);
notes about other papers of the store and about stale files are left out, and a text that cannot
be read is a 404. Bad binding keys and values are refused
(422) before any circuit is exported, as the command refuses a bad ``--set`` (exit 2); a refused
circuit is data, kept next to the exported ones.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from ..domain.circuits import (
    MAX_STATE_WIRES,
    MAX_UNITARY_WIRES,
    PAPER_TOOL,
    PASTED_ORIGIN,
    PASTED_TOOL,
    USE_THE_LIBRARY,
    CheckTarget,
    CircuitCaps,
    PaperBlock,
    PaperCircuitQasm,
    PaperCircuits,
    PaperQasm,
    PastedQasm,
    QasmExport,
    QasmFile,
    ensure_paper_within_caps,
    ensure_within_caps,
    paper_origin,
    pasted_qasm_filename,
    qasm_filename,
    read_json_strict,
)
from ..domain.documents import DocumentText, is_doc_id
from ..domain.errors import InvalidInputError, NotFoundError
from ..ports.circuits import CircuitEngine, GateListReading
from ..ports.documents import DocumentReader

CIRCUIT = "circuit_json"
TARGET_CIRCUIT = "target.circuit_json"


def _pointer(name: str) -> str:
    return "/" + name.replace(".", "/")


class ParseCircuit:
    def __init__(self, *, engine: CircuitEngine) -> None:
        self._engine = engine

    async def __call__(self, latex: str) -> GateListReading:
        return await self._engine.parse(latex)


class RunCircuit:
    """Simulate one ``circuit/1`` document given as raw JSON text."""

    def __init__(self, *, engine: CircuitEngine, caps: CircuitCaps) -> None:
        self._engine = engine
        self._caps = caps

    async def __call__(
        self,
        *,
        circuit_json: str,
        input_state: object = None,
        values: Mapping[str, Any] | None = None,
        unitary: bool = False,
    ) -> Mapping[str, Any]:
        document = read_json_strict(circuit_json, what=CIRCUIT)
        ensure_within_caps(
            [(_pointer(CIRCUIT), document)],
            self._caps,
            max_wires=MAX_UNITARY_WIRES if unitary else MAX_STATE_WIRES,
        )
        return await self._engine.run(
            document, input_state=input_state, values=values, unitary=unitary
        )


class CheckCircuit:
    """Compare a circuit with a state, a unitary, a second circuit or, with no target, the two
    sides of an equality drawn in the document."""

    def __init__(self, *, engine: CircuitEngine, caps: CircuitCaps) -> None:
        self._engine = engine
        self._caps = caps

    async def __call__(
        self,
        *,
        circuit_json: str,
        target: CheckTarget | None = None,
        input_state: object = None,
        values: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        document = read_json_strict(circuit_json, what=CIRCUIT)
        documents = [(_pointer(CIRCUIT), document)]
        request: dict[str, Any] = {}
        max_wires = MAX_UNITARY_WIRES
        if target is None:
            request["circuit"] = document
        elif target.state is not None:
            request.update(circuit=document, target={"state": target.state})
            max_wires = MAX_STATE_WIRES
        elif target.unitary is not None:
            request.update(circuit=document, target={"unitary": target.unitary})
        elif target.circuit_json is not None:
            other = read_json_strict(target.circuit_json, what=TARGET_CIRCUIT)
            documents.append((_pointer(TARGET_CIRCUIT), other))
            request.update(a=document, b=other)
        else:
            raise InvalidInputError("A target needs one of state, unitary or circuit_json.")
        ensure_within_caps(documents, self._caps, max_wires=max_wires)
        if input_state is not None:
            request["input"] = input_state
        return await self._engine.check(request, values=values)


class ShowCircuitConventions:
    def __init__(self, *, engine: CircuitEngine) -> None:
        self._engine = engine

    def __call__(self) -> Mapping[str, Any]:
        return self._engine.conventions()


# --- OpenQASM 3 export -------------------------------------------------------------------------


class ExportQasm:
    """Export one pasted ``circuit/1`` document, given as raw JSON text."""

    def __init__(self, *, engine: CircuitEngine, caps: CircuitCaps) -> None:
        self._engine = engine
        self._caps = caps

    async def __call__(
        self, *, circuit_json: str, values: Mapping[str, Any] | None = None
    ) -> PastedQasm:
        document = read_json_strict(circuit_json, what=CIRCUIT)
        ensure_within_caps(
            [(_pointer(CIRCUIT), document)], self._caps, max_wires=None, hint=USE_THE_LIBRARY
        )
        await self._engine.check_values(values)
        export = await self._engine.qasm(
            document, values=values, origin=PASTED_ORIGIN, tool=PASTED_TOOL
        )
        files = tuple(QasmFile(pasted_qasm_filename(p.part), p) for p in export.programs)
        return PastedQasm(export=export, files=files)


async def _paper_text(reader: DocumentReader, doc_id: str) -> DocumentText:
    if not is_doc_id(doc_id):
        raise NotFoundError("No such document.")
    return await asyncio.to_thread(reader.text, doc_id)  # a blocking port


class ListPaperCircuits:
    """The fenced ``latex`` blocks of a paper, numbered as docingest's export numbers them."""

    def __init__(self, *, engine: CircuitEngine, reader: DocumentReader) -> None:
        self._engine = engine
        self._reader = reader

    async def __call__(self, doc_id: str) -> PaperCircuits:
        paper = await _paper_text(self._reader, doc_id)
        blocks = await self._engine.blocks(paper.markdown)
        return PaperCircuits(
            doc_id=paper.doc_id,
            source_name=paper.source_name,
            blocks=tuple(
                PaperBlock(n, block.source, block.reading.status, block.reading.circuits)
                for n, block in enumerate(blocks, 1)
            ),
        )


class ExportPaperQasm:
    """Export every circuit of one paper, read only: the programs come back, nothing is written.

    The values are checked first, then the paper is read. Its circuits are exported in one
    engine job, with one time limit: the circuits share one operation budget, checked before any
    of them is exported. Each circuit is exported with the source line and the generated-by
    name docingest writes, so each program is the file ``docingest circuits export --doc`` would
    write.
    """

    def __init__(self, *, engine: CircuitEngine, reader: DocumentReader, caps: CircuitCaps) -> None:
        self._engine = engine
        self._reader = reader
        self._caps = caps

    async def __call__(self, doc_id: str, *, values: Mapping[str, Any] | None = None) -> PaperQasm:
        await self._engine.check_values(values)
        paper = await _paper_text(self._reader, doc_id)
        name = paper.source_name
        caps = self._caps
        found = await self._engine.qasm_blocks(
            paper.markdown,
            values=values,
            origin=lambda block, circuit: paper_origin(name, block, circuit),
            tool=PAPER_TOOL,
            admit=lambda documents: ensure_paper_within_caps(documents, caps),
        )
        keys = set(values or {})
        warnings: list[str] = []
        circuits: list[PaperCircuitQasm] = []
        for done in found:
            origin = paper_origin(name, done.block, done.circuit)
            keys &= set(done.export.unmatched)
            warnings.extend(f"{origin}: {note}" for note in done.export.notes)
            circuits.append(_paper_circuit(paper.doc_id, done.block, done.circuit, done.export))
        if not found:
            warnings.append(f"{name}: no circuits found")
        warnings.extend(
            f"binding {key!r} matches no symbol of any exported circuit" for key in sorted(keys)
        )
        return PaperQasm(
            doc_id=paper.doc_id,
            source_name=name,
            circuits=tuple(circuits),
            warnings=tuple(warnings),
        )


def _paper_circuit(doc_id: str, block: int, circuit: int, export: QasmExport) -> PaperCircuitQasm:
    files = tuple(
        QasmFile(qasm_filename(doc_id, block, circuit, program.part), program)
        for program in export.programs
    )
    return PaperCircuitQasm(block=block, circuit=circuit, export=export, files=files)
