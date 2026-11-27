"""Adapter: docingest gate lists and the quantum-sim engine behind the ``CircuitEngine`` port.

The OpenQASM 3 export calls ``quantum_sim.qasm.to_qasm`` on the ``circuit/1`` document, as
docingest's exporter does, and finds a paper's circuits as docingest's export finds them: the
fenced ``latex`` blocks of the whole text (``circuit_code_blocks``), each read with
``gate_list``. So the programs of a paper are the bytes ``docingest circuits export`` writes.
A paper is one lane job (``qasm_blocks``): its blocks are read, its operations checked and
every circuit exported in one slot and under one time limit, so a busy lane refuses it before
any work and never in the middle.

``unbound`` follows docingest's exporter (``docingest.adapters.qasm``) rule for rule: it names
the symbols of a refused circuit that have no value, as binding keys (``theta_{12}`` for the
reader's ``theta_12``), and only when a second export, with a distinct placeholder value for
each of them, is ok. A circuit refused for another reason too gets ``()``. The placeholders are
the ones docingest uses (``PLACEHOLDER`` plus a tenth of the square root of the k-th prime, in
sorted symbol order); ``tests/test_circuits_lane.py`` checks the two agree.

``check_values`` refuses what ``quantum_sim.qasm.validate_bindings`` refuses, as a 422 that
points to the key: each key is checked alone, then against the earlier key with the same name
(``theta`` and ``\\theta`` bind one symbol twice). It takes no lane slot: the check is cheap
(a value is at most ``quantum_sim.angles.MAX_CHARS`` long and the body cap bounds the keys,
under half a second for a full body), so a bad key is a 422 even when the lane is busy. It
runs in a worker thread, off the event loop.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from docingest.domain.circuits import circuit_code_blocks, gate_list
from quantum_sim import check, run
from quantum_sim.angles import symbol_name
from quantum_sim.entrypoints import conventions
from quantum_sim.qasm import to_qasm, validate_bindings

from ...domain.circuits import USE_DOCINGEST, QasmExport, QasmProgram, values_pointer
from ...domain.errors import FieldError, InvalidInputError
from ...ports.circuits import CircuitQasm, GateListReading, LatexBlock
from .lane import ComputeLane

__all__ = ["CircuitsAdapter", "ComputeLane"]

# The placeholders of the second export, as in docingest.adapters.qasm.
PLACEHOLDER = 0.37


class CircuitsAdapter:
    """Reads LaTeX with docingest's ``gate_list``, simulates with quantum-sim and exports with
    ``quantum_sim.qasm``.

    All of them are pure functions of their input, so any number of calls may run in parallel;
    the lane decides how many do.
    """

    def __init__(self, *, timeout_s: float, max_busy: int = 2) -> None:
        self._lane = ComputeLane(timeout_s=timeout_s, max_busy=max_busy)

    async def parse(self, latex: str) -> GateListReading:
        return await self._lane.run(lambda: _parse(latex))

    async def run(
        self,
        document: object,
        *,
        input_state: object = None,
        values: Mapping[str, Any] | None = None,
        unitary: bool = False,
    ) -> Mapping[str, Any]:
        # Wrapped, so a document is always read as a circuit/1 document and never as a request.
        return await self._lane.run(
            lambda: run(
                {"circuit": document}, input_state=input_state, values=values, unitary=unitary
            )
        )

    async def check(
        self, request: Mapping[str, Any], *, values: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        return await self._lane.run(lambda: check(dict(request), values=values))

    def conventions(self) -> Mapping[str, Any]:
        return conventions()

    async def blocks(self, markdown: str) -> tuple[LatexBlock, ...]:
        return await self._lane.run(lambda: _blocks(markdown))

    async def check_values(self, values: Mapping[str, Any] | None) -> None:
        await asyncio.to_thread(_check_values, values)  # no lane slot: see the module docstring

    async def qasm(
        self,
        document: object,
        *,
        values: Mapping[str, Any] | None = None,
        origin: str = "",
        tool: str,
    ) -> QasmExport:
        return await self._lane.run(lambda: _qasm(document, values, origin=origin, tool=tool))

    async def qasm_blocks(
        self,
        markdown: str,
        *,
        values: Mapping[str, Any] | None = None,
        origin: Callable[[int, int], str],
        tool: str,
        admit: Callable[[Sequence[object]], None],
    ) -> tuple[CircuitQasm, ...]:
        return await self._lane.run(
            lambda: _qasm_blocks(markdown, values, origin=origin, tool=tool, admit=admit),
            hint=USE_DOCINGEST,
        )


def _parse(latex: str) -> GateListReading:
    found = gate_list(latex)
    return GateListReading(
        status=found.status,
        text=found.text,
        circuits=tuple(circuit.to_dict() for circuit in found.circuits),
    )


def _blocks(markdown: str) -> tuple[LatexBlock, ...]:
    return tuple(
        LatexBlock(source=block.source, reading=_parse(block.source))
        for block in circuit_code_blocks(markdown)
    )


def _qasm_blocks(
    markdown: str,
    values: Mapping[str, Any] | None,
    *,
    origin: Callable[[int, int], str],
    tool: str,
    admit: Callable[[Sequence[object]], None],
) -> tuple[CircuitQasm, ...]:
    found = [
        (block_no, circuit_no, document)
        for block_no, block in enumerate(_blocks(markdown), 1)
        for circuit_no, document in enumerate(block.reading.circuits, 1)
    ]
    admit([document for _, _, document in found])
    return tuple(
        CircuitQasm(b, c, _qasm(document, values, origin=origin(b, c), tool=tool))
        for b, c, document in found
    )


def _check_values(values: Mapping[str, Any] | None) -> None:
    named: dict[str, str] = {}  # symbol name -> the first key that binds it
    for key, value in (values or {}).items():
        try:
            validate_bindings({key: value})
            name = symbol_name(key) or key  # a key that passed is one symbol
            if name in named:
                validate_bindings({named[name]: (values or {})[named[name]], key: value})
        except ValueError as error:
            message = str(error)
            raise InvalidInputError(message, (FieldError(values_pointer(key), message),)) from None
        named[name] = key


def _qasm(
    document: object, values: Mapping[str, Any] | None, *, origin: str, tool: str
) -> QasmExport:
    export = to_qasm(document, values=values, origin=origin, tool=tool)
    unbound: tuple[str, ...] = ()
    if not export.ok:
        unbound = _unbound(document, values, export.symbols, origin=origin, tool=tool)
    return QasmExport(
        status=export.status,
        programs=tuple(
            QasmProgram(
                part=program.part,
                sign=program.sign,
                text=program.text,
                n_qubits=program.n_qubits,
                n_clbits=program.n_clbits,
            )
            for program in export.programs
        ),
        reasons=export.reasons,
        notes=export.notes,
        symbols=export.symbols,
        unmatched=export.unmatched,
        unbound=unbound,
    )


def _unbound(
    document: object,
    values: Mapping[str, Any] | None,
    symbols: tuple[str, ...],
    *,
    origin: str,
    tool: str,
) -> tuple[str, ...]:
    """The binding keys of the symbols without a value of a refused circuit, if values for them
    alone would export it; else ``()``. No caller key names an unbound symbol, so a placeholder
    key never collides with one."""
    named = {symbol_name(key) for key in values or {}}
    missing = tuple(symbol for symbol in symbols if symbol not in named)
    if not missing:
        return ()
    keys = {symbol: key for symbol in missing if (key := _key(symbol))}
    if len(keys) < len(missing):
        return ()
    placeholders = {keys[symbol]: _placeholder(k) for k, symbol in enumerate(sorted(missing))}
    retry = to_qasm(document, values={**(values or {}), **placeholders}, origin=origin, tool=tool)
    return tuple(keys[symbol] for symbol in missing) if retry.ok else ()


def _key(symbol: str) -> str | None:
    """The binding key of a reader's symbol name (``theta_{12}`` for ``theta_12``); ``None`` if
    the binding rule reads the key as another name."""
    word, _, sub = symbol.partition("_")
    key = f"{word}_{{{sub}}}" if sub else word
    return key if symbol_name(key) == symbol else None


def _placeholder(k: int) -> float:
    """The placeholder of the ``k``-th unbound symbol (from 0, in sorted symbol order)."""
    return PLACEHOLDER + math.sqrt(_prime(k)) / 10


def _prime(k: int) -> int:
    """The ``k``-th prime, from 0 (2, 3, 5, ...); a circuit has few symbols."""
    found: list[int] = []
    candidate = 2
    while len(found) <= k:
        if all(candidate % p for p in found if p * p <= candidate):
            found.append(candidate)
        candidate += 1
    return found[k]
