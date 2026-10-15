"""Small, explicit circuits used by the interactive quantum solver."""

from __future__ import annotations

import numpy as np

from .domain import (
    Circuit,
    ConditionalOperation,
    MeasurementOperation,
    Operation,
    StateVector,
    gates,
)


def teleportation_circuit(input_state: StateVector) -> Circuit:
    """Build the three-qubit teleportation circuit for ``input_state`` on qubit 0.

    Qubits are ordered ``input, entangled-left, receiver``. The two classical
    measurement keys are ``m0`` and ``m1`` and drive the receiver corrections.
    """
    if input_state.num_qubits != 1:
        raise ValueError("teleportation requires a single-qubit input state")
    initial = StateVector(
        np.kron(input_state.amplitudes, np.array([1, 0, 0, 0], dtype=np.complex128)),
        3,
    )
    operations = (
        Operation(gates["H"], (1,)),
        Operation(gates["CNOT"], (1, 2)),
        Operation(gates["CNOT"], (0, 1)),
        Operation(gates["H"], (0,)),
        MeasurementOperation((0,), "m0"),
        MeasurementOperation((1,), "m1"),
        ConditionalOperation(Operation(gates["X"], (2,)), "m1", "1"),
        ConditionalOperation(Operation(gates["Z"], (2,)), "m0", "1"),
    )
    return Circuit(3, operations, initial)
