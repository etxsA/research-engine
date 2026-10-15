"""Agent-facing orchestration with an explicit quantum opt-in."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NoReturn

from .domain import StateVector
from .engine import StatevectorSimulator
from .parsing import parse_circuit, parse_dirac
from .visualizer import state_summary


@dataclass(frozen=True)
class QuantumSolution:
    interpretation: str
    state: StateVector
    summary: dict[str, object]


def solve_quantum_problem(
    request: str | Mapping[str, object],
    *,
    mode: str,
    simulator: StatevectorSimulator | None = None,
) -> QuantumSolution:
    """Solve one explicitly selected input mode; never executes input as code."""
    if mode == "dirac":
        state = parse_dirac(request) if isinstance(request, str) else _reject_mapping(mode)
        interpretation = f"Dirac state with {state.num_qubits} qubits"
    elif mode == "circuit":
        circuit = parse_circuit(request) if isinstance(request, Mapping) else _reject_text(mode)
        state = (simulator or StatevectorSimulator()).run(circuit)
        interpretation = f"Circuit with {circuit.num_qubits} qubits and {len(circuit.operations)} operations"
    else:
        raise ValueError("mode must be one of: dirac, circuit")
    return QuantumSolution(interpretation, state, state_summary(state))


def _reject_mapping(mode: str) -> NoReturn:
    raise TypeError(f"{mode} mode requires a string request")


def _reject_text(mode: str) -> NoReturn:
    raise TypeError(f"{mode} mode requires an object request")
