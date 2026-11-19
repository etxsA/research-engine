"""``docingest circuits export`` and the optional post-ingest ``--qasm`` hook, end to end."""

from __future__ import annotations

import os
import stat
from hashlib import sha256
from pathlib import Path

import pytest
from builders import one_line
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
    text = path.read_text()
    assert "// bound: theta = pi/4" in text
    assert "ry(pi/4.0) q[0];" in text


def test_a_malformed_set_is_a_usage_error(setup):
    runner, cfg, raw, _out = setup
    (raw / "circuit.md").write_text(markdown(H))
    assert ingest(runner, cfg, raw).exit_code == 0

    missing = runner.invoke(app, ["circuits", "export", "--config", str(cfg), "--set", "theta"])
    assert missing.exit_code == 2
    assert "--set expects NAME=VALUE" in one_line(missing.output)

    repeated = runner.invoke(
        app, ["circuits", "export", "--config", str(cfg), "--set", "theta=1", "--set", "theta=2"]
    )
    assert repeated.exit_code == 2
    assert "--set names 'theta' twice" in one_line(repeated.output)


def test_a_name_bound_twice_in_two_spellings_is_a_usage_error(setup):
    runner, cfg, raw, out = setup
    (raw / "theta.md").write_text(markdown(THETA))
    assert ingest(runner, cfg, raw).exit_code == 0

    result = runner.invoke(
        app,
        ["circuits", "export", "--config", str(cfg), "--set", "theta=1", "--set", "\\theta=2"],
    )

    assert result.exit_code == 2
    assert "parameter 'theta' is bound twice" in one_line(result.output)
    assert exported(out) == []


@pytest.mark.parametrize("name", ["a\nb", "thetta", "pi"])
def test_a_set_name_that_is_not_one_symbol_is_a_usage_error(setup, name):
    runner, cfg, raw, out = setup
    (raw / "theta.md").write_text(markdown(THETA))
    assert ingest(runner, cfg, raw).exit_code == 0

    result = runner.invoke(
        app,
        ["circuits", "export", "--config", str(cfg), "--set", f"{name}=1", "--set", "theta=pi/4"],
    )

    assert result.exit_code == 2
    assert f"{name!r} is not a parameter name" in one_line(result.output)
    assert exported(out) == []


def test_a_bad_set_value_fails_once_before_writing_any_circuit(setup):
    runner, cfg, raw, out = setup
    (raw / "mixed.md").write_text(markdown(H, THETA))
    assert ingest(runner, cfg, raw).exit_code == 0

    result = runner.invoke(
        app, ["circuits", "export", "--config", str(cfg), "--set", "theta=\\cos(1)"]
    )

    assert result.exit_code == 2
    assert "value for 'theta'" in one_line(result.output)
    assert exported(out) == []


def test_a_bad_set_value_names_the_position_in_the_value_as_typed(setup):
    runner, cfg, raw, out = setup
    (raw / "theta.md").write_text(markdown(THETA))
    assert ingest(runner, cfg, raw).exit_code == 0

    result = runner.invoke(
        app, ["circuits", "export", "--config", str(cfg), "--set", "theta=   pi ?"]
    )

    assert result.exit_code == 2
    assert "'?' at position 6" in one_line(result.output)
    assert exported(out) == []


def test_an_unwritable_out_is_a_clear_error(setup, tmp_path):
    runner, cfg, raw, _out = setup
    (raw / "circuit.md").write_text(markdown(H))
    assert ingest(runner, cfg, raw).exit_code == 0
    not_a_directory = tmp_path / "file"
    not_a_directory.write_text("")

    result = runner.invoke(
        app, ["circuits", "export", "--config", str(cfg), "--out", str(not_a_directory)]
    )

    assert result.exit_code == 1
    assert "cannot finish the export" in result.output


def test_a_run_stopped_by_an_unusable_out_reports_only_the_papers_it_reached(setup, tmp_path):
    runner, cfg, raw, out = setup
    first, second = raw / "a.md", raw / "b.md"
    first.write_bytes(f"{markdown(H)}\nPaper A.\n".encode())  # contents chosen for the order
    second.write_bytes(markdown(THETA).encode())
    assert ingest(runner, cfg, raw).exit_code == 0
    folders = sorted(path.parent.name for path in out.glob("*/manifest.json"))
    assert folders == [
        sha256(first.read_bytes()).hexdigest()[:16],
        sha256(second.read_bytes()).hexdigest()[:16],
    ]
    not_a_directory = tmp_path / "notadir"
    not_a_directory.write_text("")

    command = ["circuits", "export", "--config", str(cfg), "--set", "theta=pi/4"]
    result = runner.invoke(app, [*command, "--out", str(not_a_directory)])

    assert result.exit_code == 1
    text = one_line(result.output)
    assert f"the output folder {not_a_directory} cannot be used" in text
    assert "matches no symbol" not in text
    assert "wrote 0 program(s) from 1 circuit(s) in 1 document(s)" in text


def test_a_store_that_cannot_be_read_is_a_clear_error(setup, monkeypatch):
    runner, cfg, raw, _out = setup
    (raw / "circuit.md").write_text(markdown(H))
    assert ingest(runner, cfg, raw).exit_code == 0

    def unreadable(self):
        raise OSError("the disk went away")

    monkeypatch.setattr("docingest.adapters.store.filesystem.FilesystemStore.corpus", unreadable)
    result = runner.invoke(app, ["circuits", "export", "--config", str(cfg)])

    assert result.exit_code == 1
    assert "error: cannot finish the export: the disk went away" in one_line(result.output)


def test_an_empty_corpus_exits_one(setup):
    runner, cfg, _raw, out = setup

    result = runner.invoke(app, ["circuits", "export", "--config", str(cfg)])

    assert result.exit_code == 1
    assert "warning: no stored documents: ingest a paper first" in one_line(result.output)
    assert "wrote 0 program(s)" in result.output
    assert not (out / "qasm").exists()


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


def test_ingest_qasm_exports_identical_files_once(setup):
    runner, cfg, raw, out = setup
    (raw / "one.md").write_text(markdown(H))
    (raw / "two.md").write_text(markdown(H))

    result = ingest(runner, cfg, raw, "--qasm")

    assert result.exit_code == 0, result.output
    assert len(exported(out)) == 1
    assert "qasm: wrote 1 program(s) from 1 circuit(s)" in one_line(result.output)


def test_programs_are_readable_like_the_stored_documents(setup):
    runner, cfg, raw, out = setup
    (raw / "circuit.md").write_text(markdown(H))
    assert ingest(runner, cfg, raw, "--qasm").exit_code == 0

    [path] = exported(out)
    [document] = out.glob("*/document.md")
    assert stat.S_IMODE(path.stat().st_mode) == stat.S_IMODE(document.stat().st_mode)


def test_ingest_qasm_is_best_effort(setup):
    runner, cfg, raw, out = setup
    (raw / "theta.md").write_text(markdown(THETA))

    result = ingest(runner, cfg, raw, "--qasm")

    assert result.exit_code == 0, result.output
    assert "refused" in result.output and "unbound parameter" in result.output
    assert exported(out) == []
    assert list(out.glob("*/manifest.json"))


class MarkdownCrawler:
    def __init__(self, circuit: str = CNOT):
        self.records = [record("2401.00001")]
        self.circuit = circuit

    def search(self, query, limit):
        return self.records[:limit]

    def fetch(self, rec, dest_dir: Path):
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{rec.key}.md"
        path.write_text(markdown(self.circuit))
        return FetchedSource(path=path, record=rec, format="text")


class UnusableCrawler:
    """A crawler that must not be reached: a usage error comes before any network call."""

    def __init__(self):
        self.calls = 0

    def search(self, query, limit):
        self.calls += 1
        raise AssertionError("search was called")

    def fetch(self, rec, dest_dir: Path):
        self.calls += 1
        raise AssertionError("fetch was called")


class EP:
    def __init__(self, obj):
        self.name, self.value, self._obj = "fake", "tests:fake", obj

    def load(self):
        return lambda cfg: self._obj


def use_crawler(cfg: Path, out: Path, monkeypatch, crawler) -> None:
    cfg.write_text(
        f'output_dir = "{out}"\nraw_dir = "{cfg.parent / "raw-crawl"}"\n'
        '[adapters]\ncrawler = "fake"\n'
    )
    eps = {"docingest.crawler": EP(crawler)}
    monkeypatch.setattr(
        "docingest.bootstrap.entry_points", lambda group: [eps[group]] if group in eps else []
    )


def test_crawl_qasm_writes_the_circuits(setup, monkeypatch):
    runner, cfg, _raw, out = setup
    use_crawler(cfg, out, monkeypatch, MarkdownCrawler())

    result = runner.invoke(app, ["crawl", "anything", "--config", str(cfg), "--qasm"])

    assert result.exit_code == 0, result.output
    [path] = exported(out)
    assert "cx q[0], q[1];" in path.read_text()


def test_crawl_qasm_without_ingest_is_rejected(setup, monkeypatch):
    runner, cfg, _raw, out = setup
    crawler = UnusableCrawler()
    use_crawler(cfg, out, monkeypatch, crawler)

    result = runner.invoke(
        app, ["crawl", "anything", "--config", str(cfg), "--qasm", "--no-ingest"]
    )

    assert result.exit_code == 2
    assert "--qasm has nothing to export with --no-ingest" in one_line(result.output)
    assert crawler.calls == 0
    assert not (cfg.parent / "raw-crawl").exists()


UNBOUND_HINT = (
    "removed 1 stale QASM file(s) from an earlier export; block 1, circuit 1 was refused because "
    "theta has no value: run docingest circuits export --set NAME=VALUE to write it again"
)


def test_ingest_qasm_names_a_circuit_whose_bound_program_it_removed(setup):
    runner, cfg, raw, out = setup
    (raw / "theta.md").write_text(markdown(THETA))
    assert ingest(runner, cfg, raw).exit_code == 0
    bound = runner.invoke(app, ["circuits", "export", "--config", str(cfg), "--set", "theta=pi/4"])
    assert bound.exit_code == 0, bound.output
    assert len(exported(out)) == 1

    result = ingest(runner, cfg, raw / "theta.md", "--qasm")  # a cache hit: stored as before

    assert result.exit_code == 0, result.output
    assert f"warning: theta.md: {UNBOUND_HINT}" in one_line(result.output)
    assert exported(out) == []


def test_crawl_qasm_names_a_circuit_whose_bound_program_it_removed(setup, monkeypatch):
    runner, cfg, _raw, out = setup
    use_crawler(cfg, out, monkeypatch, MarkdownCrawler(THETA))
    assert runner.invoke(app, ["crawl", "anything", "--config", str(cfg)]).exit_code == 0
    bound = runner.invoke(app, ["circuits", "export", "--config", str(cfg), "--set", "theta=pi/4"])
    assert bound.exit_code == 0, bound.output
    assert len(exported(out)) == 1

    result = runner.invoke(app, ["crawl", "anything", "--config", str(cfg), "--qasm"])

    assert result.exit_code == 0, result.output
    assert f"warning: 2401.00001.md: {UNBOUND_HINT}" in one_line(result.output)
    assert exported(out) == []


def test_ingest_qasm_does_not_blame_a_missing_value_for_another_refusal(setup):
    # the stored Markdown changes under the same doc_id (a new reader, ingest --force): block 1
    # now starts in |1>, which no value of theta exports
    runner, cfg, raw, out = setup
    (raw / "theta.md").write_text(markdown(THETA))
    assert ingest(runner, cfg, raw).exit_code == 0
    bound = runner.invoke(app, ["circuits", "export", "--config", str(cfg), "--set", "theta=pi/4"])
    assert bound.exit_code == 0, bound.output
    assert len(exported(out)) == 1
    [stored] = out.glob("*/document.md")
    one_input = quantikz("\\lstick{\\ket{1}} & \\gate{R_y(\\theta)} & \\qw")
    stored.write_text(stored.read_text().replace(THETA, one_input))

    result = ingest(runner, cfg, raw / "theta.md", "--qasm")  # a cache hit: the changed Markdown

    assert result.exit_code == 0, result.output
    text = one_line(result.output)
    assert "the drawing starts an input in |1>" in text
    assert "warning: theta.md: removed 1 stale QASM file(s) from an earlier export" in text
    assert "has no value" not in text
    assert exported(out) == []


def test_a_write_error_in_the_hook_is_a_warning(setup):
    runner, cfg, raw, out = setup
    (raw / "circuit.md").write_text(markdown(H))
    out.mkdir()
    (out / "qasm").write_text("not a directory")

    result = ingest(runner, cfg, raw, "--qasm")

    assert result.exit_code == 0, result.output
    assert "warning: cannot finish the export: the output folder" in one_line(result.output)
    assert list(out.glob("*/manifest.json"))
    assert (out / "qasm").read_text() == "not a directory"


def test_an_unexpected_error_in_the_hook_is_a_warning(setup, monkeypatch):
    runner, cfg, raw, out = setup
    (raw / "circuit.md").write_text(markdown(H))

    def broken(self, documents, **kwargs):
        raise RuntimeError("the exporter broke")

    monkeypatch.setattr("docingest.application.qasm.QasmExportService.export_documents", broken)
    result = ingest(runner, cfg, raw, "--qasm")

    assert result.exit_code == 0, result.output
    assert (
        "warning: the export stopped: the exporter broke; run `docingest circuits export` to "
        "catch up"
    ) in one_line(result.output)
    assert list(out.glob("*/manifest.json"))


def test_a_write_error_exits_one_even_with_allow_partial(setup, monkeypatch):
    runner, cfg, raw, out = setup
    (raw / "one.md").write_text(markdown(H))
    (raw / "two.md").write_text(markdown(CNOT))
    assert ingest(runner, cfg, raw).exit_code == 0
    replace = os.replace

    def full_for_cx(source, destination):
        if "cx" in Path(source).read_text():
            raise OSError("disk full")
        return replace(source, destination)

    monkeypatch.setattr("docingest.application.qasm.os.replace", full_for_cx)
    for extra in ([], ["--allow-partial"]):
        result = runner.invoke(app, ["circuits", "export", "--config", str(cfg), *extra])

        assert result.exit_code == 1, extra
        assert "wrote 1 program(s)" in result.output
        assert "two.md: cannot finish the export: disk full" in one_line(result.output)
        [path] = exported(out)
        assert "h q[0];" in path.read_text()
