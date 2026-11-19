"""Port: export one ``CircuitSteps`` as OpenQASM 3 programs.

The application owns the corpus walk and the files; an adapter owns the emitter and the
simulator reader behind it. The port carries plain data only, so the application never imports
``quantum_sim`` or the parser.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..domain.circuits import CircuitSteps


@dataclass(frozen=True)
class QasmProgram:
    """One OpenQASM 3 program: a circuit part, its drawn sign, and the text."""

    part: int
    sign: str | None
    text: str
    n_qubits: int
    n_clbits: int


@dataclass(frozen=True)
class QasmExport:
    """The result of exporting one circuit. ``status`` is ``"ok"`` or ``"refused"``.

    A refused circuit has no programs. ``notes`` are the reader's notes and ``symbols`` are the
    free parameters the circuit mentions, whether or not the caller bound them.

    ``unmatched`` holds the caller's binding keys, as given, that name none of those symbols
    (all of them when the circuit could not be read). ``None`` means the exporter did not report
    it; the service then counts every key as unmatched for this circuit, so a binding is never
    taken as used because nobody checked. ``unbound`` names the symbols of a refused circuit
    that the caller gave no value, and only when values for them alone would export the circuit
    (a circuit refused for another reason too has none); it is empty for an ok export. Each name
    is a binding key that ``check_values`` accepts (``theta_{12}``, not the reader's
    ``theta_12``), so the service can print it in a ``--set NAME=VALUE`` hint.
    """

    status: str
    programs: tuple[QasmProgram, ...]
    reasons: tuple[str, ...]
    notes: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    unmatched: tuple[str, ...] | None = None
    unbound: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.reasons


@runtime_checkable
class CircuitExporter(Protocol):
    """The emitter behind ``QasmExportService``. It has no fingerprint: the export is derived
    output and is in no cache key."""

    def check_values(self, values: Mapping[str, str | float | int] | None = None) -> None:
        """Refuse a bad binding once, before any circuit is exported.

        Raises ``ValueError`` for a value or name the emitter cannot use, such as a key that is
        not one symbol of the angle grammar (``thetta``, ``pi``).
        """
        ...

    def export(
        self,
        circuit: CircuitSteps,
        *,
        values: Mapping[str, str | float | int] | None = None,
        origin: str = "",
        tool: str = "docingest",
    ) -> QasmExport:
        """Read ``circuit`` with the simulator and return its programs or refusal."""
        ...
