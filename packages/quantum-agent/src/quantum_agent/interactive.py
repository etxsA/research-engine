"""Prompt-friendly helpers shared by interactive terminal adapters."""

from __future__ import annotations

import numpy as np

from .domain import DimensionMismatchError, Gate, StateVector


def parse_scalar(value: str) -> complex:
    token = value.strip().replace("i", "j")
    if not token:
        raise ValueError("empty numeric value")
    if token.startswith("1/sqrt(") and token.endswith(")"):
        return complex(1 / np.sqrt(float(token[7:-1])))
    if token.startswith("-1/sqrt(") and token.endswith(")"):
        return complex(-1 / np.sqrt(float(token[8:-1])))
    if "/" in token and "j" not in token:
        numerator, denominator = token.split("/", 1)
        return complex(float(numerator) / float(denominator))
    return complex(token)


def parse_vector(text: str) -> StateVector:
    compact = text.strip()
    if "|" in compact:
        from .parsing import parse_dirac

        return parse_dirac(compact)
    values = [parse_scalar(value) for value in compact.split(",") if value.strip()]
    if not values:
        raise ValueError("enter comma-separated coefficients")
    if len(values) & (len(values) - 1):
        raise DimensionMismatchError("state vector length must be a power of two")
    return StateVector(np.asarray(values, dtype=np.complex128), int(np.log2(len(values))))


def parse_matrix(text: str) -> np.ndarray:
    rows = [row.strip() for row in text.split(";") if row.strip()]
    if not rows:
        raise ValueError("enter rows separated by ';'")
    matrix = np.asarray([[parse_scalar(value) for value in row.split(",")] for row in rows])
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise DimensionMismatchError("matrix must be square with equal row lengths")
    return matrix


def gate_from_matrix(name: str, matrix: np.ndarray) -> Gate:
    dimension = matrix.shape[0]
    if dimension < 2 or dimension & (dimension - 1):
        raise DimensionMismatchError("gate dimension must be a power of two")
    return Gate(name, matrix, int(np.log2(dimension)))


def dirac_terms(state: StateVector, threshold: float = 1e-12) -> str:
    terms: list[str] = []
    for index, amplitude in enumerate(state.amplitudes):
        if abs(amplitude) < threshold:
            continue
        label = format(index, f"0{state.num_qubits}b")
        real, imaginary = amplitude.real, amplitude.imag
        if abs(imaginary) < threshold:
            coefficient = f"{real:.6g}"
        elif abs(real) < threshold:
            coefficient = f"{imaginary:.6g}i"
        else:
            coefficient = f"({real:.6g}{imaginary:+.6g}i)"
        terms.append(f"{coefficient}|{label}>")
    return " + ".join(terms) or "0"


def format_matrix(matrix: np.ndarray) -> str:
    return "\n".join(
        "[ " + "  ".join(f"{value.real:.6g}{value.imag:+.6g}i" for value in row) + " ]"
        for row in matrix
    )
