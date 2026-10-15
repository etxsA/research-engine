# Quantum agent architecture

The package follows the repository's ports-and-adapters direction:

```text
agent/CLI -> parsing/use cases -> domain values -> NumPy simulator
                                      \-> visualizer JSON/Markdown
                                      \-> optional SDK adapters
```

The native path is intentionally limited to pure statevectors and
computational-basis measurement. Measurement operations collapse the
statevector and can drive later operations through explicit classical keys;
outcomes may be supplied for deterministic branch execution. This makes every
result inspectable and keeps the package local and deterministic. Optional Qiskit, Cirq, and PennyLane
adapters should implement import/export or oracle protocols without entering
the domain layer.

## Agent contract

The future tool surface should require an explicit quantum mode and accept one
of:

* `dirac`: constrained expressions such as
  `1/sqrt(2)|00> + 1/sqrt(2)|11>`;
* `circuit`: JSON with `num_qubits` and typed `operations`;
* `matrix`: JSON state and matrix arrays.

Responses should contain the interpreted input, assumptions and basis
ordering, derivation/operation trace, numeric state, probabilities or seeded
samples, and validation warnings. The parser must not execute arbitrary code.

## Framework decision

NumPy is required for dense complex arrays and matrix/vector operations.
SciPy may be added only after profiling identifies a decomposition or sparse
operation bottleneck. SymPy is a future symbolic-verification adapter. Qiskit
offers broad state/operator and simulator APIs, Cirq offers hardware-aware
circuit modeling, and PennyLane offers differentiable hybrid execution; all
remain optional because none is needed for the v1 statevector contract.

## Safety and errors

The simulator checks powers-of-two dimensions, normalization, finite values,
unitarity, distinct in-range qubits, and configured qubit/memory limits before
execution. Errors are typed so the agent can explain and correct malformed
requests rather than return a success-shaped fallback.

## Test vectors

The required baseline includes `H|0>`, Pauli/Hadamard identities, Bell-state
creation and measurement, Toffoli truth-table behavior, tensor ordering,
seeded sampling, invalid dimensions, non-unitary gates, measurement-conditioned
operations, and teleportation branch checks.
