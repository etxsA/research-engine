"""Port: reading circuit sources and simulating circuits."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


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
