"""Quantum-sim adapter for the circuit exporter port.

This is the only docingest module that imports ``quantum_sim`` (an import-linter contract checks
it). ``bootstrap`` imports it lazily, so a command that never exports a circuit does not load
``quantum_sim``. Binding names follow ``quantum_sim.angles.symbol_name``, the one rule, and
values ``quantum_sim.angles.parse_value`` (inside ``to_qasm``).

``unbound`` names the symbols of a refused circuit that the caller gave no value, and only when
a missing value is what refuses it: the adapter exports the circuit a second time with a
placeholder value for each of those symbols, and reports them only if that export is ok. A
circuit refused for another reason too (a ``|1>`` input, a gate with no standard spelling, a
value of the caller's that the angle cannot take) has ``unbound == ()``, so the service never
says a value would export it. Each unbound symbol gets its own placeholder (``_placeholder``, in
sorted symbol order, so the second export is deterministic): ``\\frac{1}{\\theta - \\phi}``
with neither bound still gets the hint. A circuit singular at the placeholders only loses the
hint, never gains a false one: ``\\sqrt{\\theta - 1}`` is refused at every placeholder (they
are all below 1 for the first twelve symbols), so its circuit gets no hint.

The names in ``unbound`` are binding keys (``theta_{12}`` for the reader's ``theta_12``), so
the hint names a key that ``--set`` takes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from quantum_sim.angles import symbol_name
from quantum_sim.qasm import to_qasm, validate_bindings

from ..domain.circuits import CircuitSteps
from ..ports import QasmExport, QasmProgram

__all__ = ["QuantumSimExporter"]

# The placeholders of the second export: 0.37 plus a tenth of the square root of a prime, one
# prime per unbound symbol. Square roots of distinct primes are linearly independent over the
# rationals with 1, so no placeholder is an integer or a simple fraction, and no sum of whole
# multiples of them and a rational number is zero (1/(\theta - \phi), 1/(\theta + \phi - 2\psi)
# and 1/(\theta - 1) are defined there); a circuit singular at them only loses the hint.
PLACEHOLDER = 0.37


class QuantumSimExporter:
    """Emit OpenQASM 3 with ``quantum_sim.qasm``, after the simulator reads the circuit."""

    def check_values(self, values: Mapping[str, str | float | int] | None = None) -> None:
        validate_bindings(values)

    def export(
        self,
        circuit: CircuitSteps,
        *,
        values: Mapping[str, str | float | int] | None = None,
        origin: str = "",
        tool: str = "docingest",
    ) -> QasmExport:
        document = circuit.to_dict()
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
    document: Mapping[str, Any],
    values: Mapping[str, str | float | int] | None,
    symbols: tuple[str, ...],
    *,
    origin: str,
    tool: str,
) -> tuple[str, ...]:
    """The binding keys of the symbols without a value of a refused circuit, if values for them
    alone would export it; else ``()``. No caller key names an unbound symbol, so a placeholder
    key never collides with one. A key is the symbol spelled as ``--set`` takes it
    (``theta_{12}`` for the reader's ``theta_12``), both for the placeholder and in the hint, so
    a symbol with a subscript of several characters keeps a hint that names a usable key."""
    named = {symbol_name(key) for key in values or {}}
    missing = tuple(symbol for symbol in symbols if symbol not in named)
    if not missing:
        return ()
    keys = {symbol: key for symbol in missing if (key := _key(symbol))}
    if len(keys) < len(missing):
        return ()
    placeholders = {keys[symbol]: _placeholder(k) for k, symbol in enumerate(sorted(missing))}
    filled = {**(values or {}), **placeholders}
    retry = to_qasm(dict(document), values=filled, origin=origin, tool=tool)
    return tuple(keys[symbol] for symbol in missing) if retry.ok else ()


def _key(symbol: str) -> str | None:
    """The binding key of a reader's symbol name: ``theta_12`` (``\\theta_{12}``) is not a key,
    ``theta_{12}`` is; a symbol with no subscript is its own key. ``None`` if the binding rule
    reads the key as another name (then the circuit gets no hint)."""
    word, _, sub = symbol.partition("_")
    key = f"{word}_{{{sub}}}" if sub else word
    return key if symbol_name(key) == symbol else None


def _placeholder(k: int) -> float:
    """The placeholder value of the ``k``-th unbound symbol (from 0, in sorted symbol order):
    0.37 plus a tenth of the square root of the ``k``-th prime."""
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
