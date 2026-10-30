"""``docingest circuits export`` and the optional post-ingest ``--qasm`` hook, end to end."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import record
from typer.testing import CliRunner

from docingest.entrypoints.cli import app
from docingest.ports import FetchedSource

WIDE = {"COLUMNS": "200"}


def quantikz(*rows: str) -> str:
    return "\\begin{quantikz}\n" + " \\\\\n".join(rows) + "\n\\end{quantikz}"


H = quantikz("& \\gate{H} & \\qw")
CNOT = quantikz("& \\ctrl{1} & \\qw", "& \\targ{} & \\qw")
THETA = quantikz("& \\gate{R_y(\\theta)} & \\qw")


def markdown(*circuits: str) -> str:
    body = "\n\n".join(f"``` latex\n{circuit}\n```" for circuit in circuits)
    return f"# Circuits\n\n{body}\n"


@pytest.fixture
def setup(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    out = tmp_path / "out"
    cfg = tmp_path / "cfg.toml"
    cfg.write_text(f'output_dir = "{out}"\nraw_dir = "{tmp_path / "raw-crawl"}"\n')
    return CliRunner(env=WIDE), cfg, raw, out


def ingest(runner, cfg, path: Path, *extra: str):
    return runner.invoke(app, ["ingest", str(path), "--config", str(cfg), *extra])


def exported(out: Path) -> list[Path]:
    return sorted((out / "qasm").glob("*.qasm"))


def test_export_writes_the_program_of_an_ingested_circuit(setup):
    runner, cfg, raw, out = setup
    (raw / "circuit.md").write_text(markdown(H))
    assert ingest(runner, cfg, raw).exit_code == 0

    result = runner.invoke(app, ["circuits", "export", "--config", str(cfg)])

    assert result.exit_code == 0, result.output
    [path] = exported(out)
    text = path.read_text()
    assert "// source: circuit.md, block 1, circuit 1, part 1/1" in text
    assert "h q[0];" in text
    assert "wrote 1 program(s)" in result.output


def test_export_selects_a_document_by_name(setup):
    runner, cfg, raw, out = setup
    (raw / "one.md").write_text(markdown(H))
    (raw / "two.md").write_text(markdown(CNOT))
    assert ingest(runner, cfg, raw).exit_code == 0

    result = runner.invoke(app, ["circuits", "export", "--config", str(cfg), "--doc", "two.md"])

    assert result.exit_code == 0, result.output
    [path] = exported(out)
    assert "cx q[0], q[1];" in path.read_text()


def test_export_out_override(setup, tmp_path):
    runner, cfg, raw, out = setup
    (raw / "circuit.md").write_text(markdown(H))
    assert ingest(runner, cfg, raw).exit_code == 0
    custom = tmp_path / "elsewhere"

    result = runner.invoke(app, ["circuits", "export", "--config", str(cfg), "--out", str(custom)])

    assert result.exit_code == 0, result.output
    [_path] = sorted(custom.glob("*.qasm"))
    assert not (out / "qasm").exists()


def test_set_binds_a_free_parameter(setup):
    runner, cfg, raw, out = setup
    (raw / "theta.md").write_text(markdown(THETA))
    assert ingest(runner, cfg, raw).exit_code == 0

    refused = runner.invoke(app, ["circuits", "export", "--config", str(cfg)])
    assert refused.exit_code == 1
    assert "unbound parameter" in refused.output
    assert exported(out) == []

    bound = runner.invoke(app, ["circuits", "export", "--config", str(cfg), "--set", "theta=pi/4"])
    assert bound.exit_code == 0, bound.output
    [path] = exported(out)
    assert "ry(pi/4) q[0];" in path.read_text()


def test_a_refusal_exits_one_and_allow_partial_exits_zero(setup):
    runner, cfg, raw, out = setup
    (raw / "mixed.md").write_text(markdown(H, THETA))
    assert ingest(runner, cfg, raw).exit_code == 0

    refused = runner.invoke(app, ["circuits", "export", "--config", str(cfg)])
    assert refused.exit_code == 1
    assert "refused" in refused.output
    assert len(exported(out)) == 1  # the circuit that could be made is still written

    allowed = runner.invoke(app, ["circuits", "export", "--config", str(cfg), "--allow-partial"])
    assert allowed.exit_code == 0, allowed.output
    assert len(exported(out)) == 1


def test_no_circuits_exits_one(setup):
    runner, cfg, raw, out = setup
    (raw / "plain.md").write_text("# Nothing\n\nNo circuits here.\n")
    assert ingest(runner, cfg, raw).exit_code == 0

    result = runner.invoke(app, ["circuits", "export", "--config", str(cfg)])

    assert result.exit_code == 1
    assert "no circuits found" in result.output
    assert exported(out) == []


def test_an_unknown_document_exits_one(setup):
    runner, cfg, raw, _out = setup
    (raw / "circuit.md").write_text(markdown(H))
    assert ingest(runner, cfg, raw).exit_code == 0

    result = runner.invoke(app, ["circuits", "export", "--config", str(cfg), "--doc", "missing.md"])

    assert result.exit_code == 1
    assert "no stored document matches" in result.output


def test_ingest_qasm_writes_the_circuits_after_the_store(setup):
    runner, cfg, raw, out = setup
    (raw / "circuit.md").write_text(markdown(H))

    result = ingest(runner, cfg, raw, "--qasm")

    assert result.exit_code == 0, result.output
    [path] = exported(out)
    assert "h q[0];" in path.read_text()
    assert "qasm: wrote 1 program(s)" in result.output


def test_ingest_qasm_is_best_effort(setup):
    runner, cfg, raw, out = setup
    (raw / "theta.md").write_text(markdown(THETA))

    result = ingest(runner, cfg, raw, "--qasm")

    assert result.exit_code == 0, result.output
    assert "refused" in result.output and "unbound parameter" in result.output
    assert exported(out) == []
    assert list(out.glob("*/manifest.json"))


class MarkdownCrawler:
    def __init__(self):
        self.records = [record("2401.00001")]

    def search(self, query, limit):
        return self.records[:limit]

    def fetch(self, rec, dest_dir: Path):
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{rec.key}.md"
        path.write_text(markdown(CNOT))
        return FetchedSource(path=path, record=rec, format="text")


class EP:
    def __init__(self, obj):
        self.name, self.value, self._obj = "fake", "tests:fake", obj

    def load(self):
        return lambda cfg: self._obj


def test_crawl_qasm_writes_the_circuits(setup, monkeypatch):
    runner, cfg, _raw, out = setup
    cfg.write_text(
        f'output_dir = "{out}"\nraw_dir = "{cfg.parent / "raw-crawl"}"\n'
        '[adapters]\ncrawler = "fake"\n'
    )
    eps = {"docingest.crawler": EP(MarkdownCrawler())}
    monkeypatch.setattr(
        "docingest.bootstrap.entry_points", lambda group: [eps[group]] if group in eps else []
    )

    result = runner.invoke(app, ["crawl", "anything", "--config", str(cfg), "--qasm"])

    assert result.exit_code == 0, result.output
    [path] = exported(out)
    assert "cx q[0], q[1];" in path.read_text()


def test_crawl_qasm_without_ingest_is_rejected(setup):
    runner, cfg, _raw, _out = setup
    result = runner.invoke(
        app, ["crawl", "anything", "--config", str(cfg), "--qasm", "--no-ingest"]
    )
    assert result.exit_code != 0
