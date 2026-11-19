"""A small NumPy simulator of quantum circuits with stated conventions.

Conventions (Nielsen and Chuang): wire 1 is the top wire and the most significant bit, wires
are numbered from 1, and results are compared up to global phase. See ``docs/conventions.md``.
"""

from importlib.metadata import version

from quantum_sim.angles import Angle, parse_angle, parse_value, symbol_name
from quantum_sim.api import RESULT_FORMAT, check, refusal, run
from quantum_sim.domain import (
    ATOL,
    CONVENTIONS,
    GATES,
    MAX_OPS,
    MAX_REASONS,
    MAX_STATEVECTOR_WIRES,
    MAX_UNITARY_WIRES,
    MEASURE,
    RANDOM_STATES,
    AngleError,
    Circuit,
    Equality,
    GateSpec,
    InvalidCircuitError,
    InvalidStateError,
    LabelError,
    LimitExceededError,
    Op,
    QuantumSimError,
    RefusedError,
    UnknownGateError,
    UnsupportedOperationError,
    validate,
)
from quantum_sim.engine import equal, probabilities, statevector, unitary
from quantum_sim.formats import (
    CIRCUIT_FORMAT,
    ParametricCircuit,
    ParametricOp,
    Reading,
    from_gate_list,
    to_gate_list,
)
from quantum_sim.labels import ResolvedLabel, label_of, resolve_label
from quantum_sim.qasm import QASM_FORMAT_VERSION, QasmExport, QasmProgram, to_qasm

__version__ = version("quantum-sim")

__all__ = [
    "ATOL",
    "CIRCUIT_FORMAT",
    "CONVENTIONS",
    "GATES",
    "MAX_OPS",
    "MAX_REASONS",
    "MAX_STATEVECTOR_WIRES",
    "MAX_UNITARY_WIRES",
    "MEASURE",
    "QASM_FORMAT_VERSION",
    "RANDOM_STATES",
    "RESULT_FORMAT",
    "Angle",
    "AngleError",
    "Circuit",
    "Equality",
    "GateSpec",
    "InvalidCircuitError",
    "InvalidStateError",
    "LabelError",
    "LimitExceededError",
    "Op",
    "ParametricCircuit",
    "ParametricOp",
    "QasmExport",
    "QasmProgram",
    "QuantumSimError",
    "Reading",
    "RefusedError",
    "ResolvedLabel",
    "UnknownGateError",
    "UnsupportedOperationError",
    "__version__",
    "check",
    "equal",
    "from_gate_list",
    "label_of",
    "parse_angle",
    "parse_value",
    "probabilities",
    "refusal",
    "resolve_label",
    "run",
    "statevector",
    "symbol_name",
    "to_gate_list",
    "to_qasm",
    "unitary",
    "validate",
]
