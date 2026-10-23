"""Driving adapter: ``docingest circuits ...``, reading the circuits of stored documents."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ..application.qasm import QasmExportReport
from ..bootstrap import Container
from ..config import load_config
from ..domain.errors import DocingestError

circuits_app = typer.Typer(
    add_completion=False, help="Inspect and export the circuits of stored documents."
)
console = Console()

ConfigOpt = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="pipeline.toml", exists=True, dir_okay=False),
]


def _log(msg: str) -> None:
    console.print(msg, markup=False, highlight=False)


def _run[T](config: Path | None, action: Callable[[Container], T]) -> T:
    """Run ``action`` on a container; an expected failure is a message and exit code 1."""
    try:
        return action(Container(load_config(config), log=_log))
    except (DocingestError, RuntimeError, ValueError) as e:
        console.print(f"[red]error[/]: {escape(str(e))}")
        raise typer.Exit(1) from e


def _bindings(items: Sequence[str] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in items or ():
        name, separator, value = item.partition("=")
        name = name.strip()
        if not separator or not name:
            raise typer.BadParameter("--set expects NAME=VALUE")
        values[name] = value.strip()
    return values


def _report(report: QasmExportReport) -> None:
    if report.files:
        table = Table(title=f"OpenQASM 3 export — {report.out}")
        for column in ("document", "block", "circuit", "part", "file"):
            table.add_column(column)
        for file in report.files:
            table.add_row(
                escape(file.source_name),
                str(file.block),
                str(file.circuit),
                str(file.part),
                escape(file.path.name),
            )
        console.print(table)
    for warning in report.warnings:
        console.print(f"[yellow]warning[/]: {escape(warning)}")
    for refusal in report.refusals:
        console.print(f"[red]refused[/]: {escape(refusal)}")
    console.print(
        f"wrote {report.written} program(s) from {report.circuits} circuit(s) "
        f"in {report.documents} document(s) to {escape(str(report.out))}"
    )


@circuits_app.command("export")
def export(
    doc: Annotated[
        list[str] | None,
        typer.Option("--doc", help="doc_id, a unique prefix of 8+ characters, or a source name"),
    ] = None,
    config: ConfigOpt = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Directory for the .qasm files (default: <output_dir>/qasm)"),
    ] = None,
    set_values: Annotated[
        list[str] | None,
        typer.Option("--set", help="Bind a free parameter: NAME=VALUE (repeatable)"),
    ] = None,
    allow_partial: Annotated[
        bool,
        typer.Option(
            "--allow-partial",
            help="Exit 0 when some circuits are refused; the programs that could be made "
            "are written",
        ),
    ] = False,
) -> None:
    """Write one OpenQASM 3 program per circuit part of every stored document.

    The programs are derived from ``document.md``; the store, the manifest and the cache key
    are not changed. A circuit that cannot be exported is reported and does not stop the
    others. The command exits 1 when a circuit is refused (unless --allow-partial) or when
    nothing could be exported.
    """
    values = _bindings(set_values)
    report = _run(
        config,
        lambda container: container.qasm_export.export_corpus(doc or (), values=values, out=out),
    )
    _report(report)
    if not report.files:
        raise typer.Exit(1)
    if report.refusals and not allow_partial:
        raise typer.Exit(1)
