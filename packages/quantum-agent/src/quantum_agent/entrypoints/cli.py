from __future__ import annotations

import argparse
import json

from quantum_agent.engine import StatevectorSimulator
from quantum_agent.parsing import parse_circuit, parse_dirac
from quantum_agent.visualizer import state_markdown


def main() -> None:
    parser = argparse.ArgumentParser(description="Solve small quantum problems with statevectors")
    parser.add_argument("input", help="Dirac state, for example '|00> + |11>'")
    parser.add_argument("--shots", type=int, default=0)
    args = parser.parse_args()
    state = parse_dirac(args.input)
    result = StatevectorSimulator().measure(state, shots=args.shots)
    print(state_markdown(state))
    if result.samples:
        print(json.dumps({"samples": result.samples}, sort_keys=True))
