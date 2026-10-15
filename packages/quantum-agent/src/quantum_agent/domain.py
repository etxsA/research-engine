"""Pure quantum-domain values and validation rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import log2
from typing import Final, Union

import numpy as np
from numpy.typing import NDArray

ComplexArray = NDArray[np.complex128]
DEFAULT_TOLERANCE: Final[float] = 1e-10


class QuantumError(ValueError):
    """Base class for invalid quantum inputs."""


class DimensionMismatchError(QuantumError):
    """A vector, matrix, or operation has incompatible dimensions."""


class NonUnitaryGateError(QuantumError):
    """A matrix does not preserve inner products."""


class InvalidQubitError(QuantumError):
    """A qubit index or operand list is invalid."""


class InvalidStateError(QuantumError):
    """A state vector is not finite or normalized."""


class ParseError(QuantumError):
    """Input text cannot be converted into a canonical quantum object."""


def _qubits_for_dimension(dimension: int) -> int:
    if dimension < 1 or dimension & (dimension - 1):
        raise DimensionMismatchError("state dimension must be a positive power of two")
    return int(log2(dimension))


def _as_complex_array(values: object, *, ndim: int) -> ComplexArray:
    array = np.asarray(values, dtype=np.complex128)
    if array.ndim != ndim:
        raise DimensionMismatchError(f"expected {ndim} dimensions, got {array.ndim}")
    if not np.all(np.isfinite(array)):
        raise QuantumError("matrix or vector contains non-finite values")
    return array


@dataclass(frozen=True)
class StateVector:
    amplitudes: ComplexArray
    num_qubits: int
    tolerance: float = DEFAULT_TOLERANCE

    def __post_init__(self) -> None:
        amplitudes = _as_complex_array(self.amplitudes, ndim=1)
        expected_qubits = _qubits_for_dimension(amplitudes.size)
        if expected_qubits != self.num_qubits:
            raise DimensionMismatchError(
                f"vector dimension {amplitudes.size} does not match {self.num_qubits} qubits"
            )
        norm = float(np.vdot(amplitudes, amplitudes).real)
        if not np.isclose(norm, 1.0, atol=self.tolerance, rtol=0):
            raise InvalidStateError(f"state vector must be normalized; squared norm is {norm:g}")
        object.__setattr__(self, "amplitudes", amplitudes.copy())

    @classmethod
    def zero(cls, num_qubits: int) -> StateVector:
        if num_qubits < 0:
            raise InvalidStateError("number of qubits cannot be negative")
        amplitudes = np.zeros(2**num_qubits, dtype=np.complex128)
        amplitudes[0] = 1
        return cls(amplitudes, num_qubits)

    def probability(self, basis_index: int) -> float:
        if not 0 <= basis_index < self.amplitudes.size:
            raise IndexError("basis index is outside the state vector")
        return float(abs(self.amplitudes[basis_index]) ** 2)

    def probabilities(self) -> NDArray[np.float64]:
        return np.abs(self.amplitudes) ** 2

    def inner_product(self, other: StateVector) -> complex:
        if self.num_qubits != other.num_qubits:
            raise DimensionMismatchError("inner-product states must have equal dimensions")
        return complex(np.vdot(self.amplitudes, other.amplitudes))


@dataclass(frozen=True)
class Gate:
    name: str
    matrix: ComplexArray
    num_qubits: int
    tolerance: float = DEFAULT_TOLERANCE

    def __post_init__(self) -> None:
        matrix = _as_complex_array(self.matrix, ndim=2)
        expected_dimension = 2**self.num_qubits
        if self.num_qubits < 1 or matrix.shape != (expected_dimension, expected_dimension):
            raise DimensionMismatchError(
                f"{self.name} must be a {expected_dimension}x{expected_dimension} matrix"
            )
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            unitary = matrix.conj().T @ matrix
        if not np.allclose(unitary, np.eye(expected_dimension), atol=self.tolerance):
            raise NonUnitaryGateError(f"{self.name} is not unitary")
        object.__setattr__(self, "matrix", matrix.copy())


@dataclass(frozen=True)
class Operation:
    gate: Gate
    qubits: tuple[int, ...]
    controls: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if len(self.qubits) != self.gate.num_qubits:
            raise InvalidQubitError(f"{self.gate.name} requires {self.gate.num_qubits} target qubits")
        if len(set(self.qubits)) != len(self.qubits) or len(set(self.controls)) != len(self.controls):
            raise InvalidQubitError("operation qubits must be unique")
        if set(self.qubits) & set(self.controls):
            raise InvalidQubitError("control and target qubits must be disjoint")


@dataclass(frozen=True)
class MeasurementOperation:
    qubits: tuple[int, ...]
    key: str

    def __post_init__(self) -> None:
        if not self.qubits or len(set(self.qubits)) != len(self.qubits):
            raise InvalidQubitError("measurement qubits must be non-empty and unique")
        if not self.key.strip():
            raise QuantumError("measurement key cannot be empty")


@dataclass(frozen=True)
class ConditionalOperation:
    operation: Operation
    key: str
    value: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise QuantumError("conditional measurement key cannot be empty")
        if not self.value or any(bit not in "01" for bit in self.value):
            raise QuantumError("conditional measurement value must be a bit string")


CircuitOperation = Union[Operation, MeasurementOperation, ConditionalOperation]


@dataclass(frozen=True)
class Circuit:
    num_qubits: int
    operations: tuple[CircuitOperation, ...] = ()
    initial_state: StateVector | None = None

    def __post_init__(self) -> None:
        if self.num_qubits < 1:
            raise InvalidQubitError("a circuit must contain at least one qubit")
        initial = self.initial_state or StateVector.zero(self.num_qubits)
        if initial.num_qubits != self.num_qubits:
            raise DimensionMismatchError("initial state and circuit have different qubit counts")
        for operation in self.operations:
            if isinstance(operation, MeasurementOperation):
                operands = operation.qubits
            elif isinstance(operation, ConditionalOperation):
                operands = (*operation.operation.qubits, *operation.operation.controls)
            else:
                operands = (*operation.qubits, *operation.controls)
            if any(qubit < 0 or qubit >= self.num_qubits for qubit in operands):
                raise InvalidQubitError("operation references a qubit outside the circuit")


class MeasurementBasis(str, Enum):
    COMPUTATIONAL = "computational"


@dataclass(frozen=True)
class MeasurementResult:
    probabilities: dict[str, float]
    samples: dict[str, int] = field(default_factory=dict)
    collapsed_state: StateVector | None = None
    seed: int | None = None


def _gate(name: str, matrix: object, num_qubits: int = 1) -> Gate:
    return Gate(name, np.asarray(matrix, dtype=np.complex128), num_qubits)


def _builtin_gates() -> dict[str, Gate]:
    return {
        "I": _gate("I", np.eye(2)),
        "H": _gate("H", np.array([[1, 1], [1, -1]]) / np.sqrt(2)),
        "X": _gate("X", [[0, 1], [1, 0]]),
        "Y": _gate("Y", [[0, -1j], [1j, 0]]),
        "Z": _gate("Z", [[1, 0], [0, -1]]),
        "S": _gate("S", [[1, 0], [0, 1j]]),
        "CNOT": _gate("CNOT", [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]], 2),
        "TOFFOLI": _gate(
            "TOFFOLI",
            np.array(
                [
                    [1, 0, 0, 0, 0, 0, 0, 0],
                    [0, 1, 0, 0, 0, 0, 0, 0],
                    [0, 0, 1, 0, 0, 0, 0, 0],
                    [0, 0, 0, 1, 0, 0, 0, 0],
                    [0, 0, 0, 0, 1, 0, 0, 0],
                    [0, 0, 0, 0, 0, 1, 0, 0],
                    [0, 0, 0, 0, 0, 0, 0, 1],
                    [0, 0, 0, 0, 0, 0, 1, 0],
                ]
            ),
            3,
        ),
    }


gates = _builtin_gates()


def phase(theta: float) -> Gate:
    return _gate(f"Phase({theta:g})", [[1, 0], [0, np.exp(1j * theta)]])
