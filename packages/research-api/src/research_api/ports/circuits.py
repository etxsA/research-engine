"""Port: reading circuit sources, simulating circuits and exporting them as OpenQASM 3."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..domain.circuits import QasmExport


@dataclass(frozen=True)
class GateListReading:
    """What docingest made of the LaTeX of one code block.

    ``status`` is ``parsed``, ``partial`` or ``unparsed``; ``text`` is the gate list as docingest
    writes it for the answer model; ``circuits`` holds each circuit as a ``circuit/1`` document.
    """

    status: str
    text: str
    circuits: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class LatexBlock:
    """One fenced ``latex`` block of a Markdown text: its body and what docingest read in it."""

    source: str
    reading: GateListReading


class CircuitEngine(Protocol):
    """The work is CPU-bound and bounded by the adapter: it takes plain Python data, as
    ``json.load`` gives it, and never keeps the key-order or duplicate-key question open."""

    async def parse(self, latex: str) -> GateListReading:
        """Read the circuits drawn in ``latex`` (quantikz, Qcircuit, yquant, tikz-cd)."""
        ...

    async def run(
        self,
        document: object,
        *,
        input_state: object = None,
        values: Mapping[str, Any] | None = None,
        unitary: bool = False,
    ) -> Mapping[str, Any]:
        """The ``quantum-sim-result/1`` document for a ``circuit/1`` document. Input the
        simulator refuses comes back as a result with status ``refused``, never as an error."""
        ...

    async def check(
        self, request: Mapping[str, Any], *, values: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        """The ``quantum-sim-result/1`` document of an equality check. ``request`` is one of
        quantum-sim's check requests: ``{"circuit", "target"}``, ``{"a", "b"}`` or
        ``{"circuit"}`` with a drawn equality; ``input`` may be added."""
        ...

    def conventions(self) -> Mapping[str, Any]:
        """The conventions, the gate table and the limits, as ``quantum-sim conventions``."""
        ...

    async def blocks(self, markdown: str) -> tuple[LatexBlock, ...]:
        """The fenced ``latex`` blocks of a Markdown text, in order, each read as ``parse``
        reads LaTeX: the blocks docingest's export numbers from 1."""
        ...

    async def check_values(self, values: Mapping[str, Any] | None) -> None:
        """Refuse binding keys and values the export cannot use, before any circuit is
        exported: an ``InvalidInputError`` with the emitter's message, pointing to the key in
        ``values`` when one key is the cause. Cheap, so it is never refused for a busy engine."""
        ...

    async def qasm(
        self,
        document: object,
        *,
        values: Mapping[str, Any] | None = None,
        origin: str = "",
        tool: str,
    ) -> QasmExport:
        """The OpenQASM 3 export of a ``circuit/1`` document. ``origin`` is the source line of
        the header (none when empty) and ``tool`` the generated-by name. A circuit the emitter
        refuses comes back with status ``refused`` and its reasons, never as an error."""
        ...
