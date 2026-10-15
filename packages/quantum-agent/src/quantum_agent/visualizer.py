"""Stable JSON-friendly and Markdown-friendly state summaries."""

from __future__ import annotations

from .domain import StateVector


def state_summary(state: StateVector, *, threshold: float = 1e-12, top_k: int | None = None) -> dict[str, object]:
    entries = [
        {
            "basis": format(index, f"0{state.num_qubits}b"),
            "amplitude_real": float(amplitude.real),
            "amplitude_imag": float(amplitude.imag),
            "probability": state.probability(index),
        }
        for index, amplitude in enumerate(state.amplitudes)
        if abs(amplitude) >= threshold
    ]
    entries.sort(key=lambda item: float(item["probability"]), reverse=True)
    truncated = top_k is not None and len(entries) > top_k
    return {
        "num_qubits": state.num_qubits,
        "entries": entries[:top_k] if top_k is not None else entries,
        "truncated": truncated,
        "omitted_count": max(0, len(entries) - (top_k or len(entries))),
    }


def state_markdown(state: StateVector, *, threshold: float = 1e-12, top_k: int | None = None) -> str:
    summary = state_summary(state, threshold=threshold, top_k=top_k)
    lines = ["| Basis | Amplitude | Probability |", "|---|---:|---:|"]
    for entry in summary["entries"]:
        real = entry["amplitude_real"]
        imag = entry["amplitude_imag"]
        amplitude = f"{real:g}" if not imag else f"{real:g}{imag:+g}i"
        lines.append(f"| `|{entry['basis']}>` | `{amplitude}` | `{entry['probability']:.6g}` |")
    if summary["truncated"]:
        lines.append(f"| … | {summary['omitted_count']} entries omitted | … |")
    return "\n".join(lines)
