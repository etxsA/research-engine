"""The ``quantum-sim`` command. JSON in, JSON out.

    quantum-sim conventions                      the conventions, the gate table, the limits
    quantum-sim run [FILE] [--input BITS] [--set NAME=VALUE]... [--unitary]
    quantum-sim check [FILE [FILE]] [--set NAME=VALUE]...

``FILE`` is a JSON file, or ``-`` or nothing for standard input. ``run`` reads a circuit/1
document (or ``{"circuit": ..., "input": ..., "values": ...}``); ``check`` reads
``{"a": ..., "b": ...}``, ``{"circuit": ..., "target": {...}}``, a circuit/1 document with a
drawn equality, or two files that each hold a circuit/1 document. The result (format
``quantum-sim-result/1``) is JSON on standard output. Exit status: 0 when the work was done
(``ok``, or ``symbolic`` when a symbol has no value; a check that finds the circuits different
is still 0 and says ``"equal_to_target": false``), 1 when the input was refused (a JSON result
with the reasons on standard output), 2 for a command line that does not parse.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any

from quantum_sim.api import check, refusal, run
from quantum_sim.domain import (
    ATOL,
    CONVENTIONS,
    GATES,
    MAX_OPS,
    MAX_STATEVECTOR_WIRES,
    MAX_UNITARY_WIRES,
    RANDOM_STATES,
    short_text,
)

MAX_INPUT_BYTES = 32 * 1024 * 1024


class _BadInput(Exception):
    pass


def conventions() -> dict[str, object]:
    """The conventions, the gate table and the limits, as printed by ``quantum-sim conventions``."""
    return {
        "version": version("quantum-sim"),
        "conventions": dict(CONVENTIONS),
        "gates": [
            {
                "name": spec.name,
                "targets": spec.n_targets,
                "params": spec.n_params,
                "definition": spec.definition,
            }
            for spec in GATES.values()
        ],
        "limits": {
            "statevector_wires": MAX_STATEVECTOR_WIRES,
            "unitary_wires": MAX_UNITARY_WIRES,
            "ops": MAX_OPS,
            "random_states": RANDOM_STATES,
            "atol": ATOL,
        },
    }


def _read_json(source: str | None) -> object:
    """The JSON document in the file ``source`` (standard input for ``None`` or ``-``)."""
    try:
        if source in (None, "-"):
            text = sys.stdin.read(MAX_INPUT_BYTES + 1)
        else:
            with Path(str(source)).open(encoding="utf-8") as handle:
                text = handle.read(MAX_INPUT_BYTES + 1)
    except (OSError, UnicodeDecodeError) as error:
        raise _BadInput(f"cannot read {source or 'standard input'}: {error}") from None
    if len(text) > MAX_INPUT_BYTES:
        raise _BadInput(f"more than {MAX_INPUT_BYTES // (1024 * 1024)} MiB of input")

    def no_repeats(pairs: list[tuple[str, object]]) -> dict[str, object]:
        found: dict[str, object] = {}
        for key, value in pairs:
            if key in found:
                raise _BadInput(
                    f"{source or 'standard input'} gives the key {short_text(key)!r} twice; "
                    "the document does not say which value is meant"
                )
            found[key] = value
        return found

    try:
        return json.loads(text, object_pairs_hook=no_repeats)
    except (ValueError, RecursionError) as error:
        raise _BadInput(f"{source or 'standard input'} is not valid JSON: {error}") from None


def _assignments(items: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items:
        name, separator, value = item.partition("=")
        if not separator or not name.strip():
            raise _BadInput(f"--set expects NAME=VALUE, got {item!r}")
        if name.strip() in values:
            raise _BadInput(f"--set gives {short_text(name.strip())!r} twice; give it once")
        values[name.strip()] = value  # parse_value strips it; error positions count from here
    return values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quantum-sim", description="Simulate small quantum circuits (numpy only)."
    )
    parser.add_argument("--version", action="version", version=version("quantum-sim"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "conventions", help="print the conventions, the gate table and the limits as JSON"
    )
    run_parser = commands.add_parser(
        "run", help="simulate a circuit/1 document (a file or standard input)"
    )
    run_parser.add_argument("file", nargs="?", help="JSON file; '-' or nothing is standard input")
    run_parser.add_argument("--input", dest="input_state", help="input basis label, wire 1 first")
    run_parser.add_argument("--unitary", action="store_true", help="also list the unitary")
    check_parser = commands.add_parser(
        "check", help="are two circuits, or a circuit and a target, equal up to global phase?"
    )
    check_parser.add_argument(
        "files", nargs="*", help="one request file, or two circuit/1 files; none is standard input"
    )
    for sub in (run_parser, check_parser):
        sub.add_argument(
            "--set",
            dest="assignments",
            action="append",
            default=[],
            metavar="NAME=VALUE",
            help="a value for a symbol, a number or an angle such as '\\pi/2' or 'pi/2' "
            "(repeatable)",
        )
    return parser


def _execute(args: argparse.Namespace) -> dict[str, Any]:
    values = _assignments(args.assignments) or None
    if args.command == "run":
        request = _read_json(args.file)
        # the flag only asks for the unitary; without it the request file decides
        return run(
            request, input_state=args.input_state, values=values, unitary=args.unitary or None
        )
    if len(args.files) > 2:
        raise _BadInput("check takes at most two files")
    if len(args.files) == 2:
        request = {"a": _read_json(args.files[0]), "b": _read_json(args.files[1])}
    else:
        request = _read_json(args.files[0] if args.files else None)
    return check(request, values=values)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "conventions":
        result: dict[str, Any] = conventions()
    else:
        try:
            result = _execute(args)
        except _BadInput as error:
            result = refusal(args.command, [str(error)])
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 1 if result.get("status") == "refused" else 0
