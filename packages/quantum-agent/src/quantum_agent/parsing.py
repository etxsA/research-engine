"""Constrained, deterministic input parsers for agent-facing quantum requests."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

import numpy as np

from .domain import (
    Circuit,
    ConditionalOperation,
    DimensionMismatchError,
    Gate,
    MeasurementOperation,
    Operation,
    ParseError,
    StateVector,
    gates,
    phase,
)


def parse_dirac(text: str) -> StateVector:
    """Parse a normalized sum such as ``1/sqrt(2)|00> + 1/sqrt(2)|11>``."""
    compact = text.replace(" ", "")
    terms = re.findall(r"([+-]?(?:\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?|1/sqrt\(\d+\))?)?\\?ket?\|?([01]+)>", compact)
    if not terms:
        terms = re.findall(r"([+-]?(?:\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?|1/sqrt\(\d+\))?)?\|([01]+)>", compact)
    if not terms:
        raise ParseError("expected a sum of computational-basis kets such as |00> + |11>")
    qubits = len(terms[0][1])
    amplitudes = np.zeros(2**qubits, dtype=np.complex128)
    for coefficient, ket in terms:
        if len(ket) != qubits:
            raise DimensionMismatchError("all Dirac basis kets must have equal width")
        value = _coefficient(coefficient or "1")
        index = int(ket, 2)
        amplitudes[index] += value
    return StateVector(amplitudes, qubits)


def _coefficient(value: str) -> complex:
    sign = -1 if value.startswith("-") else 1
    value = value.lstrip("+-")
    if value.startswith("1/sqrt(") and value.endswith(")"):
        return sign / np.sqrt(float(value[7:-1]))
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return sign * float(numerator) / float(denominator)
    return sign * float(value)


def parse_circuit(spec: Mapping[str, object]) -> Circuit:
    try:
        num_qubits = int(spec["num_qubits"])
        raw_operations = spec.get("operations", [])
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError("circuit input requires an integer num_qubits") from exc
    if not isinstance(raw_operations, Sequence) or isinstance(raw_operations, (str, bytes)):
        raise ParseError("operations must be a list")
    operations = []
    for raw in raw_operations:
        if not isinstance(raw, Mapping):
            raise ParseError("each operation must be an object")
        if "measure" in raw:
            qubits = tuple(int(q) for q in raw.get("measure", ()))
            key = str(raw.get("key", ""))
            operations.append(MeasurementOperation(qubits, key))
            continue
        name = str(raw.get("gate", "")).upper()
        if name not in gates and not name.startswith("PHASE("):
            raise ParseError(f"unsupported gate {name!r}")
        gate = phase(float(name[6:-1])) if name.startswith("PHASE(") else gates[name]
        qubits = tuple(int(q) for q in raw.get("qubits", ()))
        controls = tuple(int(q) for q in raw.get("controls", ()))
        operation = Operation(gate, qubits, controls)
        condition = raw.get("condition")
        if condition is not None:
            if not isinstance(condition, Mapping):
                raise ParseError("operation condition must be an object")
            operations.append(
                ConditionalOperation(
                    operation,
                    str(condition.get("key", "")),
                    str(condition.get("equals", "")),
                )
            )
        else:
            operations.append(operation)
    return Circuit(num_qubits, tuple(operations))


def parse_matrix(spec: Mapping[str, object]) -> tuple[StateVector, Gate]:
    try:
        state_values = spec["state"]
        matrix_values = spec["matrix"]
        num_qubits = int(spec["num_qubits"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ParseError("matrix input requires state, matrix, and num_qubits") from exc
    state = StateVector(np.asarray(state_values, dtype=np.complex128), num_qubits)
    matrix = np.asarray(matrix_values, dtype=np.complex128)
    return state, Gate("custom", matrix, int(round(np.log2(matrix.shape[0]))))
