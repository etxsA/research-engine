"""Interactive terminal controller for document ingestion and quantum algebra."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from rich.console import Console

from quantum_agent import (
    Circuit,
    Operation,
    StateVector,
    StatevectorSimulator,
    gates,
    teleportation_circuit,
)
from quantum_agent.domain import QuantumError, phase
from quantum_agent.interactive import (
    dirac_terms,
    format_matrix,
    gate_from_matrix,
    parse_matrix,
    parse_scalar,
    parse_vector,
)

from ..application.ingest import IngestOptions
from ..config import load_config
from ..domain.errors import DocingestError


class TerminalIO(Protocol):
    def ask(self, prompt: str) -> str: ...

    def choose(self, prompt: str, choices: Sequence[str]) -> str: ...

    def write(self, message: str = "") -> None: ...


class RichTerminal:
    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def ask(self, prompt: str) -> str:
        return self.console.input(f"[bold]{prompt}[/] ")

    def choose(self, prompt: str, choices: Sequence[str]) -> str:
        self.write(prompt)
        self.write("  " + "  ".join(choices))
        return self.ask("Choice")

    def write(self, message: str = "") -> None:
        self.console.print(message, markup=False)


@dataclass
class InteractiveController:
    io: TerminalIO
    simulator: StatevectorSimulator
    ingest_launcher: Callable[[], None] | None = None

    def run(self) -> None:
        while True:
            self.io.write(
                "\nQuick guide: choose an option by entering its number. "
                "Document paths may be absolute or relative; use 0 to exit."
            )
            self.io.write(
                "1 Document Ingestion: enter a file path, or leave it blank to cancel. "
                "2 Quantum Engine: open the quantum operations submenu. "
                "0 Exit: close interactive mode."
            )
            choice = self.io.choose(
                "\nResearch Engine",
                ("1 Document Ingestion", "2 Quantum Linear Algebra Engine", "0 Exit"),
            )
            if choice == "0":
                self.io.write("Goodbye.")
                return
            if choice == "1":
                self._document_menu()
            elif choice == "2":
                self._quantum_menu()
            else:
                self.io.write("Invalid choice. Enter 1, 2, or 0.")

    def _document_menu(self) -> None:
        if self.ingest_launcher:
            try:
                self.ingest_launcher()
            except (DocingestError, OSError, ValueError) as exc:
                self.io.write(f"Error: {exc}")
        else:
            self.io.write("Use `docingest ingest PATH` for document ingestion.")

    def _quantum_menu(self) -> None:
        handlers = {
            "1": self._single_qubit,
            "2": self._tensor_products,
            "3": self._circuits,
            "4": self._measurement,
        }
        while True:
            self.io.write(
                "\nQuantum quick guide: enter vectors as `1,0` or `|0>`; "
                "enter matrices as rows separated by `;`, for example `1,0;0,1`."
            )
            self.io.write(
                "1 Single-qubit gate: state + H/X/Y/Z/S/Phase/custom gate. "
                "2 Tensor product: two vectors or matrices + `v` or `m`. "
                "3 Algorithms: `Bell`, or a teleportation state plus branch `00`, `01`, `10`, or `11`. "
                "4 Measurement: two same-size states. 0 Back: return to the main menu."
            )
            choice = self.io.choose(
                "\nQuantum Linear Algebra Engine",
                (
                    "1 Single-Qubit & Gate Operations",
                    "2 Multi-Qubit Systems & Tensor Products",
                    "3 Quantum Circuits & Algorithms",
                    "4 State Measurement & Probability Vectors",
                    "0 Back / Main Menu",
                ),
            )
            if choice == "0":
                return
            handler = handlers.get(choice)
            if handler is None:
                self.io.write("Invalid choice. Enter 1, 2, 3, 4, or 0.")
                continue
            try:
                handler()
            except (QuantumError, ValueError, IndexError) as exc:
                self.io.write(f"Error: {exc}")

    def _single_qubit(self) -> None:
        state = parse_vector(self.io.ask("Initial state vector (comma-separated or Dirac):"))
        name = self.io.ask("Gate [H, X, Y, Z, S, Phase, custom]:").strip().upper()
        if name == "PHASE":
            gate = phase(float(self.io.ask("Phase angle in radians:")))
        elif name == "CUSTOM":
            gate = gate_from_matrix(
                "custom", parse_matrix(self.io.ask("2x2 matrix rows separated by ';':"))
            )
        else:
            gate = gates.get(name)
            if gate is None or gate.num_qubits != 1:
                raise ValueError("choose H, X, Y, Z, S, Phase, or custom")
        result = self.simulator.apply_operation(state, Operation(gate, (0,)))
        self.io.write(f"{dirac_terms(state)} --{gate.name}--> {dirac_terms(result)}")
        self.io.write(f"Final vector: {result.amplitudes}")

    def _tensor_products(self) -> None:
        first = self.io.ask("First vector or matrix:")
        second = self.io.ask("Second vector or matrix:")
        if self.io.ask("Combine as [v]ectors or [m]atrices?").strip().lower().startswith("v"):
            left, right = parse_vector(first), parse_vector(second)
            result = StateVector(
                np.kron(left.amplitudes, right.amplitudes),
                left.num_qubits + right.num_qubits,
            )
            self.io.write(f"|psi> tensor |phi> = {result.amplitudes}")
            self.io.write(f"Dirac summary: {dirac_terms(result)}")
        else:
            result = np.kron(parse_matrix(first), parse_matrix(second))
            self.io.write(f"U1 tensor U2 =\n{format_matrix(result)}")

    def _circuits(self) -> None:
        choice = self.io.ask("Algorithm [Bell, Teleportation]:").strip().lower()
        if choice.startswith("tele"):
            input_state = parse_vector(self.io.ask("Input state to teleport:"))
            outcome = self.io.ask("Measurement branch m0m1 [00, 01, 10, 11]:").strip()
            if outcome not in {"00", "01", "10", "11"}:
                raise ValueError("measurement branch must be 00, 01, 10, or 11")
            circuit = teleportation_circuit(input_state)
            state = self.simulator.run(
                circuit,
                measurement_outcomes={"m0": outcome[0], "m1": outcome[1]},
            )
            self.io.write(f"Teleportation branch {outcome}: {dirac_terms(state)}")
            receiver_start = int(outcome, 2) * 2
            self.io.write(f"Receiver state: {state.amplitudes[receiver_start : receiver_start + 2]}")
            return
        if choice != "bell":
            raise ValueError("choose Bell or Teleportation")
        circuit = Circuit(2, (Operation(gates["H"], (0,)), Operation(gates["CNOT"], (0, 1))))
        state = StateVector.zero(2)
        self.io.write("Step 0: |00>")
        for index, operation in enumerate(circuit.operations, 1):
            state = self.simulator.apply_operation(state, operation)
            self.io.write(f"Step {index}: {operation.gate.name} -> {dirac_terms(state)}")
        self.io.write(f"Final vector: {state.amplitudes}")

    def _measurement(self) -> None:
        first = parse_vector(self.io.ask("State |psi>:"))
        second = parse_vector(self.io.ask("State |phi> (same dimension):"))
        overlap = abs(first.inner_product(second)) ** 2
        self.io.write(f"|<psi|phi>|^2 = {overlap:.6g}")
        self.io.write("Collapse probabilities:")
        for basis, probability in self.simulator.measure(first).probabilities.items():
            self.io.write(f"  |{basis}>: {probability:.6g}")


def _launch_ingest(io: TerminalIO) -> None:
    raw_path = io.ask("Document path (blank to cancel):").strip()
    if not raw_path:
        io.write("Cancelled.")
        return
    path = Path(raw_path).expanduser()
    from ..bootstrap import Container

    stored = Container(load_config(), log=io.write).ingest.ingest(path, IngestOptions())
    io.write(f"Stored: {stored.location}/document.md")


def interactive() -> None:
    """Open the interactive research-engine menu."""
    controller = InteractiveController(RichTerminal(), StatevectorSimulator())
    controller.ingest_launcher = lambda: _launch_ingest(controller.io)
    try:
        controller.run()
    except (EOFError, KeyboardInterrupt):
        controller.io.write("\nExiting interactive mode.")
