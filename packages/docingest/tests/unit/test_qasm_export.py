"""``QasmExportService``: select stored documents, emit their circuits, write the files."""

from __future__ import annotations

import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from fakes import InMemoryStore, add_doc

from docingest.adapters.qasm import QuantumSimExporter
from docingest.application import qasm as qasm_module
from docingest.application.qasm import QasmExportService
from docingest.bootstrap import Container
from docingest.config import AppConfig
from docingest.ports import QasmExport, QasmProgram


def quantikz(*rows: str) -> str:
    return "\\begin{quantikz}\n" + " \\\\\n".join(rows) + "\n\\end{quantikz}"


H = quantikz("& \\gate{H} & \\qw")
CNOT = quantikz("& \\ctrl{1} & \\qw", "& \\targ{} & \\qw")
THETA = quantikz("& \\gate{R_y(\\theta)} & \\qw")
OPEN_CONTROL = quantikz("& \\octrl{1} & \\qw", "& \\gate{X} & \\qw")
AMBIGUOUS_U = quantikz("& \\gate{U} & \\qw")
RZZ = quantikz("\\gate[2]{R_{zz}(0.5)} & \\qw", "& \\qw")
RZZ_GAMMA = quantikz("\\gate[2]{R_{zz}(\\gamma)} & \\qw", "& \\qw")
ONE_INPUT = quantikz("\\lstick{\\ket{1}} & \\gate{H} & \\qw")


def document(store, doc_id: str, *circuits: str, name: str = "a.md"):
    body = "\n\n".join(f"``` latex\n{circuit}\n```" for circuit in circuits)
    add_doc(store, doc_id, [(body, "")], name=name)


def export(store, out: Path, selectors=(), values=None):
    return QasmExportService(store, out, QuantumSimExporter()).export_corpus(
        selectors, values=values
    )


def test_one_circuit_becomes_one_named_program(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    out = tmp_path / "qasm"
    report = export(store, out)

    assert report.ok and not report.warnings
    assert (report.documents, report.circuits, report.written) == (1, 1, 1)
    [file] = report.files
    assert file.path == out / "aaaaaaaaaaaaaaaa-b01-c01-p01.qasm"
    assert file.path.exists()
    assert file.source_name == "a.md" and (file.block, file.circuit, file.part) == (1, 1, 1)
    text = file.path.read_text()
    assert "// source: a.md, block 1, circuit 1, part 1/1" in text
    assert "h q[0];" in text


def test_every_block_and_circuit_gets_its_own_file(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H, CNOT)
    report = export(store, tmp_path / "qasm")

    assert [f.path.name for f in report.files] == [
        "aaaaaaaaaaaaaaaa-b01-c01-p01.qasm",
        "aaaaaaaaaaaaaaaa-b02-c01-p01.qasm",
    ]
    assert report.circuits == 2
    assert "cx q[0], q[1];" in report.files[1].path.read_text()


def test_the_source_name_selects_a_document(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H, name="a.md")
    document(store, "b" * 64, CNOT, name="b.md")

    report = export(store, tmp_path / "qasm", ["b.md"])

    assert [f.doc_id for f in report.files] == ["b" * 64]
    assert report.documents == 1


def test_a_doc_id_prefix_of_eight_characters_selects_a_document(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H, name="a.md")
    document(store, "b" * 64, H, name="b.md")

    report = export(store, tmp_path / "qasm", ["b" * 8])

    assert [f.doc_id for f in report.files] == ["b" * 64]


def test_selectors_are_a_set(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    report = export(store, tmp_path / "qasm", ["a" * 8, "a.md", "a" * 64])
    assert report.documents == 1 and report.written == 1


def test_an_ambiguous_prefix_is_an_error(tmp_path):
    store = InMemoryStore()
    document(store, "c" * 64, H, name="one.md")
    document(store, "c" * 32 + "d" * 32, H, name="two.md")
    with pytest.raises(ValueError, match="longer doc_id prefix"):
        export(store, tmp_path / "qasm", ["c" * 8])


def test_an_unknown_or_short_selector_is_an_error(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    with pytest.raises(ValueError, match="no stored document"):
        export(store, tmp_path / "qasm", ["z" * 8])
    with pytest.raises(ValueError, match="no stored document"):
        export(store, tmp_path / "qasm", ["abcd"])


def test_a_document_without_circuits_is_a_warning(tmp_path):
    store = InMemoryStore()
    add_doc(store, "e" * 64, [("# Nothing here\n", "")], name="e.md")
    report = export(store, tmp_path / "qasm")
    assert report.files == () and report.circuits == 0
    assert report.warnings == ("e.md: no circuits found",)


def test_an_empty_corpus_writes_nothing(tmp_path):
    report = export(InMemoryStore(), tmp_path / "qasm")
    assert (report.documents, report.circuits, report.written) == (0, 0, 0)


def test_a_refused_circuit_does_not_stop_the_others(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H, THETA)
    report = export(store, tmp_path / "qasm")

    assert not report.ok
    assert report.written == 1 and report.circuits == 2
    assert len(report.refusals) == 1
    assert "block 2, circuit 1" in report.refusals[0]
    assert "unbound parameter" in report.refusals[0]


def test_an_open_control_is_conjugated_with_x(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, OPEN_CONTROL)
    report = export(store, tmp_path / "qasm")

    assert report.ok
    text = report.files[0].path.read_text()
    assert "x q[0];\ncx q[0], q[1];\nx q[0];" in text


def test_an_ambiguous_gate_the_simulator_refuses_is_not_written(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, AMBIGUOUS_U)
    out = tmp_path / "qasm"
    report = export(store, out)

    assert not report.ok
    assert report.files == () and report.written == 0
    assert "unknown gate label 'U'" in report.refusals[0]
    assert not out.exists()


def test_a_gate_without_a_standard_spelling_is_refused(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, RZZ)
    out = tmp_path / "qasm"
    report = export(store, out)

    assert not report.ok
    assert report.files == () and report.written == 0
    assert "stdgates.inc has no rzz" in report.refusals[0]
    assert not out.exists()


def test_set_binds_a_free_parameter(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, THETA)

    refused = export(store, tmp_path / "refused")
    assert refused.files == () and "theta" in refused.refusals[0]

    bound = export(store, tmp_path / "bound", values={"theta": "pi/4"})
    assert bound.ok and not bound.warnings
    text = bound.files[0].path.read_text()
    assert "// bound: theta = pi/4" in text
    assert "ry(pi/4.0) q[0];" in text


def test_out_overrides_the_service_default(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    default, custom = tmp_path / "default", tmp_path / "custom"

    report = QasmExportService(store, default, QuantumSimExporter()).export_corpus(out=custom)

    assert report.out == custom
    assert report.files[0].path.parent == custom
    assert not default.exists()


def test_export_documents_is_the_post_ingest_path(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    docs, _ = store.corpus()

    report = QasmExportService(store, tmp_path / "qasm", QuantumSimExporter()).export_documents(
        docs
    )

    assert report.written == 1 and report.documents == 1


def test_an_unreadable_markdown_is_a_warning(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)

    def broken(_doc):
        raise OSError("gone")

    store.markdown = broken
    report = export(store, tmp_path / "qasm")

    assert report.files == ()
    assert report.warnings == ("a.md: cannot read the stored Markdown (gone)",)


def test_a_refusal_removes_the_programs_of_an_earlier_run(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, THETA)
    out = tmp_path / "qasm"

    first = export(store, out, values={"theta": "pi/4"})
    [file] = first.files
    assert file.path.exists()

    second = export(store, out)

    assert not second.ok
    assert not file.path.exists()
    assert any("removed 1 stale QASM file(s)" in warning for warning in second.warnings)


def test_the_hook_names_the_circuit_whose_bound_program_it_removed(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H, THETA)
    out = tmp_path / "qasm"
    service = QasmExportService(store, out, QuantumSimExporter())
    bound = service.export_corpus(values={"theta": "pi/4"})
    assert bound.ok and bound.written == 2

    report = service.export_documents(store.corpus()[0])  # the --qasm hook passes no values

    assert [file.path.name for file in report.files] == ["aaaaaaaaaaaaaaaa-b01-c01-p01.qasm"]
    assert not (out / "aaaaaaaaaaaaaaaa-b02-c01-p01.qasm").exists()
    assert report.removed == 1
    assert (
        "a.md: removed 1 stale QASM file(s) from an earlier export; block 2, circuit 1 was "
        "refused because theta has no value: run docingest circuits export --set NAME=VALUE to "
        "write it again"
    ) in report.warnings


def test_the_hook_names_only_the_circuits_whose_programs_it_removed(tmp_path):
    # block 2 is a parametric R_zz: refused with or without a value, it never had a program
    store = InMemoryStore()
    document(store, "a" * 64, THETA, RZZ_GAMMA)
    out = tmp_path / "qasm"
    service = QasmExportService(store, out, QuantumSimExporter())
    bound = service.export_corpus(values={"theta": "pi/4", "gamma": "1"})
    assert [file.path.name for file in bound.files] == ["aaaaaaaaaaaaaaaa-b01-c01-p01.qasm"]

    report = service.export_documents(store.corpus()[0])

    [note] = [warning for warning in report.warnings if "stale" in warning]
    assert note == (
        "a.md: removed 1 stale QASM file(s) from an earlier export; block 1, circuit 1 was "
        "refused because theta has no value: run docingest circuits export --set NAME=VALUE to "
        "write it again"
    )


THETA_AFTER_ONE = quantikz("\\lstick{\\ket{1}} & \\gate{R_y(\\theta)} & \\qw")
THETA_AND_RZZ = quantikz(
    "& \\gate{R_y(\\theta)} & \\gate[2]{R_{zz}(0.5)} & \\qw", "& \\qw & & \\qw"
)
THETA_AND_ISWAP = quantikz("& \\gate{R_y(\\theta)} & \\gate[2]{iSWAP} & \\qw", "& \\qw & & \\qw")


@pytest.mark.parametrize("changed", [THETA_AFTER_ONE, THETA_AND_RZZ, THETA_AND_ISWAP])
def test_the_hook_does_not_blame_a_missing_value_for_another_refusal(tmp_path, changed):
    # the paper is ingested again (--force) and its circuit now has a |1> input, an R_zz or an
    # iSWAP: a value for theta would not export it, so the note must not say it would
    store = InMemoryStore()
    document(store, "a" * 64, THETA)
    out = tmp_path / "qasm"
    service = QasmExportService(store, out, QuantumSimExporter())
    assert service.export_corpus(values={"theta": "pi/4"}).written == 1
    document(store, "a" * 64, changed)

    report = service.export_documents(store.corpus()[0])  # the --qasm hook passes no values

    assert not (out / "aaaaaaaaaaaaaaaa-b01-c01-p01.qasm").exists() and report.removed == 1
    [note] = [warning for warning in report.warnings if "stale" in warning]
    assert note == "a.md: removed 1 stale QASM file(s) from an earlier export"
    assert not any("has no value" in line for line in (*report.warnings, *report.refusals))
    assert service.export_corpus(values={"theta": "pi/4"}).written == 0


def test_a_reexport_removes_a_part_that_the_document_lost(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    out = tmp_path / "qasm"
    out.mkdir()
    stale = out / "aaaaaaaaaaaaaaaa-b01-c01-p02.qasm"
    stale.write_text("stale")

    report = export(store, out)

    assert report.ok
    assert not stale.exists()
    assert "p02" not in {file.path.name for file in report.files}


def test_a_bad_binding_is_a_usage_error_not_a_refused_circuit(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H, THETA)

    with pytest.raises(ValueError, match="value for 'theta'"):
        export(store, tmp_path / "qasm", values={"theta": "\\cos(1)"})


def test_reader_notes_are_in_the_report(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, ONE_INPUT)
    report = export(store, tmp_path / "qasm")

    assert not report.ok
    assert any("starts an input in |1>" in refusal for refusal in report.refusals)
    assert any("is not read as an input state" in warning for warning in report.warnings)


def test_a_binding_that_matches_no_symbol_is_reported(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    document(store, "b" * 64, THETA)

    report = export(store, tmp_path / "qasm", values={"theta": "pi/4", "\\phi": "1"})

    assert report.ok and report.written == 2
    assert "binding '\\\\phi' matches no symbol of any exported circuit" in report.warnings
    assert not any("binding 'theta'" in warning for warning in report.warnings)


@pytest.mark.parametrize("key", ["thetta", "pi", "a\nb"])
def test_a_binding_key_that_is_not_one_symbol_is_a_usage_error(tmp_path, key):
    store = InMemoryStore()
    document(store, "a" * 64, THETA)
    out = tmp_path / "qasm"

    with pytest.raises(ValueError, match=re.escape(f"{key!r} is not a parameter name")):
        export(store, out, values={"theta": "pi/4", key: "1"})
    assert not out.exists()


def test_a_failed_write_leaves_neither_a_partial_file_nor_a_temporary_file(tmp_path, monkeypatch):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    out = tmp_path / "qasm"

    def fail(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr("docingest.application.qasm.os.replace", fail)
    report = export(store, out)

    assert not report.ok and report.files == ()
    assert report.errors == (
        "a.md: cannot finish the export: disk full; its earlier programs were kept",
    )
    assert not (out / "aaaaaaaaaaaaaaaa-b01-c01-p01.qasm").exists()
    assert list(out.iterdir()) == []


def test_a_write_error_stops_one_document_and_the_next_is_still_exported(tmp_path, monkeypatch):
    # the error hits the first document, so the documents after it show that the run goes on
    store = InMemoryStore()
    document(store, "a" * 64, H, H, name="a.md")
    document(store, "b" * 64, H, H, name="b.md")
    document(store, "c" * 64, H, H, name="c.md")
    out = tmp_path / "qasm"
    out.mkdir()
    for doc in ("a", "b", "c"):  # a stale program of each document from an earlier export
        (out / f"{doc * 16}-b09-c01-p01.qasm").write_text("stale")
    replace = qasm_module.os.replace

    def full_for_a(source, destination):
        if Path(destination).name.startswith("a" * 16 + "-b02"):
            raise OSError("disk full")
        return replace(source, destination)

    monkeypatch.setattr("docingest.application.qasm.os.replace", full_for_a)
    report = export(store, out)

    assert [file.path.name for file in report.files] == [
        "aaaaaaaaaaaaaaaa-b01-c01-p01.qasm",  # written before the error, and kept
        "bbbbbbbbbbbbbbbb-b01-c01-p01.qasm",
        "bbbbbbbbbbbbbbbb-b02-c01-p01.qasm",
        "cccccccccccccccc-b01-c01-p01.qasm",
        "cccccccccccccccc-b02-c01-p01.qasm",
    ]
    assert report.errors == (
        "a.md: cannot finish the export: disk full; its earlier programs were kept",
    )
    assert not report.ok and report.removed == 2 and report.documents == 3
    assert names(out) == {
        "aaaaaaaaaaaaaaaa-b01-c01-p01.qasm",
        "aaaaaaaaaaaaaaaa-b09-c01-p01.qasm",  # a's cleanup did not run
        "bbbbbbbbbbbbbbbb-b01-c01-p01.qasm",
        "bbbbbbbbbbbbbbbb-b02-c01-p01.qasm",
        "cccccccccccccccc-b01-c01-p01.qasm",
        "cccccccccccccccc-b02-c01-p01.qasm",
    }


# ------------------------------------------------------------------ stale programs


def names(out: Path) -> set[str]:
    return {path.name for path in out.iterdir()}


def test_a_reexport_removes_a_circuit_a_block_and_a_document_gone_from_the_markdown(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H, quantikz("& \\gate{H} & \\qw") + "\n\n" + CNOT, CNOT)
    out = tmp_path / "qasm"
    first = export(store, out)
    assert {f.path.name for f in first.files} == {
        "aaaaaaaaaaaaaaaa-b01-c01-p01.qasm",
        "aaaaaaaaaaaaaaaa-b02-c01-p01.qasm",
        "aaaaaaaaaaaaaaaa-b02-c02-p01.qasm",
        "aaaaaaaaaaaaaaaa-b03-c01-p01.qasm",
    }

    document(store, "a" * 64, H, H)  # block 2 lost its second circuit, block 3 is gone
    second = export(store, out)

    assert second.removed == 2
    assert names(out) == {"aaaaaaaaaaaaaaaa-b01-c01-p01.qasm", "aaaaaaaaaaaaaaaa-b02-c01-p01.qasm"}
    assert "a.md: removed 2 stale QASM file(s) from an earlier export" in second.warnings

    add_doc(store, "a" * 64, [("# No circuit any more\n", "")], name="a.md")
    third = export(store, out)

    assert third.removed == 2 and names(out) == set()
    assert "a.md: no circuits found" in third.warnings


def test_the_cleanup_spares_other_documents_other_files_and_an_unreadable_document(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H, name="a.md")
    document(store, "b" * 64, H, name="b.md")
    out = tmp_path / "qasm"
    export(store, out)
    keep = {
        "aaaaaaaaaaaaaaaa-b07-c01-p01.qasm.bak",  # not a .qasm file
        "aaaaaaaaaaaaaaaa-notes.qasm",  # not a program name
        "cccccccccccccccc-b01-c01-p01.qasm",  # another doc_id
        ".aaaaaaaaaaaaaaaa-b09-c01-p01.qasm.x.tmp",  # a temporary file
    }
    for name in keep:
        (out / name).write_text("mine")
    (out / "aaaaaaaaaaaaaaaa-b08-c01-p01.qasm").mkdir()  # a directory with a program's name
    keep.add("aaaaaaaaaaaaaaaa-b08-c01-p01.qasm")
    (out / "bbbbbbbbbbbbbbbb-b05-c01-p01.qasm").write_text("stale of b")
    (out / "aaaaaaaaaaaaaaaa-b05-c01-p01.qasm").write_text("stale of a")

    report = export(store, out, ["a.md"])

    assert report.errors == () and report.ok
    assert report.removed == 1
    assert names(out) == keep | {
        "aaaaaaaaaaaaaaaa-b01-c01-p01.qasm",
        "bbbbbbbbbbbbbbbb-b01-c01-p01.qasm",
        "bbbbbbbbbbbbbbbb-b05-c01-p01.qasm",  # b was not exported in this run
    }

    original = store.markdown

    def unreadable(doc):
        if doc.manifest.doc_id == "a" * 64:
            raise OSError("gone")
        return original(doc)

    store.markdown = unreadable
    report = export(store, out)

    assert report.removed == 1  # b's stale program; a's programs stay, its Markdown was not read
    assert "aaaaaaaaaaaaaaaa-b01-c01-p01.qasm" in names(out)
    assert "bbbbbbbbbbbbbbbb-b05-c01-p01.qasm" not in names(out)


def test_a_stale_program_removed_by_someone_else_first_is_not_an_error(tmp_path, monkeypatch):
    store = InMemoryStore()
    document(store, "a" * 64, THETA)
    out = tmp_path / "qasm"
    [file] = export(store, out, values={"theta": "pi/4"}).files
    unlink = Path.unlink

    def removed_meanwhile(path, missing_ok=False):
        unlink(path)  # another export removed it between the listing and this call
        raise FileNotFoundError(2, "No such file or directory", str(path))

    monkeypatch.setattr(Path, "unlink", removed_meanwhile)
    report = export(store, out)

    assert not file.path.exists()
    assert report.removed == 0 and report.errors == ()
    assert not any("stale" in warning for warning in report.warnings)
    assert len(report.refusals) == 1


def test_a_cleanup_error_keeps_the_count_of_what_it_removed(tmp_path, monkeypatch):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    out = tmp_path / "qasm"
    out.mkdir()
    for block in ("b05", "b06"):
        (out / f"aaaaaaaaaaaaaaaa-{block}-c01-p01.qasm").write_text("stale")
    unlink = Path.unlink
    calls = []

    def second_is_locked(path, missing_ok=False):
        calls.append(path.name)
        if len(calls) == 2:
            raise PermissionError(13, "Permission denied", str(path))
        unlink(path)

    monkeypatch.setattr(Path, "unlink", second_is_locked)
    report = export(store, out)

    assert report.removed == 1 and report.written == 1
    [error] = report.errors
    assert error.startswith("a.md: cannot remove a stale QASM file: [Errno 13] Permission denied")
    assert error.endswith("; 1 removed, the others were kept")
    assert "a.md: removed 1 stale QASM file(s) from an earlier export" in report.warnings
    assert names(out) == {"aaaaaaaaaaaaaaaa-b01-c01-p01.qasm", calls[1]}


def test_an_output_folder_that_cannot_be_made_is_one_error(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H, name="a.md")
    document(store, "b" * 64, CNOT, name="b.md")
    out = tmp_path / "file"
    out.write_text("not a folder")

    report = export(store, out)

    assert report.files == () and not report.ok
    [error] = report.errors
    assert error.startswith(f"cannot finish the export: the output folder {out} cannot be used")
    assert error.endswith("; the export stopped")


def test_a_run_stopped_by_the_output_folder_reports_only_what_it_reached(tmp_path):
    # b, the only document with theta, comes after the folder error: the run cannot know
    # whether theta matches a symbol, and it did not reach b
    store = InMemoryStore()
    document(store, "a" * 64, H, name="a.md")
    document(store, "b" * 64, THETA, name="b.md")
    out = tmp_path / "file"
    out.write_text("not a folder")

    report = export(store, out, values={"theta": "pi/4"})

    [error] = report.errors
    assert error.startswith(f"cannot finish the export: the output folder {out} cannot be used")
    assert not any("matches no symbol" in warning for warning in report.warnings)
    assert (report.documents, report.circuits, report.written) == (1, 1, 0)


def test_an_unreadable_document_counts_as_reached(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H, name="a.md")
    document(store, "b" * 64, H, name="b.md")
    original = store.markdown

    def unreadable(doc):
        if doc.manifest.doc_id == "a" * 64:
            raise OSError("gone")
        return original(doc)

    store.markdown = unreadable
    report = export(store, tmp_path / "qasm", values={"\\phi": "1"})

    assert (report.documents, report.circuits, report.written) == (2, 1, 1)
    assert report.errors == ()
    assert "binding '\\\\phi' matches no symbol of any exported circuit" in report.warnings


def test_a_document_error_suppresses_the_matches_no_symbol_warning(tmp_path, monkeypatch):
    store = InMemoryStore()
    document(store, "a" * 64, H, name="a.md")
    document(store, "b" * 64, THETA, name="b.md")
    replace = qasm_module.os.replace

    def full_for_b(source, destination):
        if Path(destination).name.startswith("b" * 16):
            raise OSError("disk full")
        return replace(source, destination)

    monkeypatch.setattr("docingest.application.qasm.os.replace", full_for_b)
    report = export(store, tmp_path / "qasm", values={"theta": "pi/4", "\\phi": "1"})

    assert report.errors == (
        "b.md: cannot finish the export: disk full; its earlier programs were kept",
    )
    assert report.documents == 2 and report.written == 1
    assert not any("matches no symbol" in warning for warning in report.warnings)


def test_programs_get_the_permissions_of_a_normal_write(tmp_path, monkeypatch):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    monkeypatch.setattr(qasm_module, "_UMASK", 0o022)

    report = export(store, tmp_path / "qasm")

    assert stat.S_IMODE(report.files[0].path.stat().st_mode) == 0o644


# ------------------------------------------------------------------ the post-ingest path


def test_export_documents_exports_a_document_given_twice_once(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    docs, _ = store.corpus()

    report = QasmExportService(store, tmp_path / "qasm", QuantumSimExporter()).export_documents(
        [docs[0], docs[0]]
    )

    assert (report.documents, report.circuits, report.written) == (1, 1, 1)


def ocr_all_variant(store, doc_id: str, circuit: str):
    """Store an ``--ocr-all`` run of the canonical document ``doc_id`` with another circuit."""
    manifest = store.canonical[doc_id][0]
    variant = manifest.model_copy(update={"ocr_all": True})
    return store.save(variant, f"# Circuits\n\n``` latex\n{circuit}\n```\n")


def test_an_ocr_all_variant_does_not_overwrite_the_canonical_programs(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    variant = ocr_all_variant(store, "a" * 64, CNOT)
    assert not variant.canonical

    report = QasmExportService(store, tmp_path / "qasm", QuantumSimExporter()).export_documents(
        [variant]
    )

    [file] = report.files
    assert "h q[0];" in file.path.read_text()
    assert "cx" not in file.path.read_text()


def test_a_variant_without_a_canonical_document_is_exported(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    variant = ocr_all_variant(store, "a" * 64, CNOT)
    del store.canonical["a" * 64]

    report = QasmExportService(store, tmp_path / "qasm", QuantumSimExporter()).export_documents(
        [variant]
    )

    [file] = report.files
    assert "cx q[0], q[1];" in file.path.read_text()


def test_a_variant_given_by_the_hook_is_exported_when_the_corpus_holds_another_variant(tmp_path):
    # two --ocr-all runs of equal length and no canonical document: the corpus keeps the first;
    # the hook was given the second and must export the Markdown it was given
    store = InMemoryStore()
    document(store, "a" * 64, H)
    first = ocr_all_variant(store, "a" * 64, H)
    manifest = first.manifest.model_copy(update={"config_hash": "h2"})
    second = store.save(manifest, f"# Circuits\n\n``` latex\n{CNOT}\n```\n")
    del store.canonical["a" * 64]
    [held], _ = store.corpus()
    assert held.manifest.config_hash == first.manifest.config_hash != second.manifest.config_hash

    report = QasmExportService(store, tmp_path / "qasm", QuantumSimExporter()).export_documents(
        [second]
    )

    [file] = report.files
    assert "cx q[0], q[1];" in file.path.read_text()


def test_a_longer_variant_in_the_corpus_is_exported_instead_of_the_one_given(tmp_path):
    # no canonical document; the corpus serves the 3-page variant, and the hook was given a
    # 1-page run: exporting the short one would delete the long one's programs
    store = InMemoryStore()
    document(store, "a" * 64, H)
    short = ocr_all_variant(store, "a" * 64, H)
    long_manifest = short.manifest.model_copy(update={"config_hash": "h2", "n_pages": 3})
    store.save(long_manifest, f"# Circuits\n\n``` latex\n{H}\n```\n\n``` latex\n{CNOT}\n```\n")
    del store.canonical["a" * 64]
    [held], _ = store.corpus()
    assert held.manifest.n_pages == 3 > short.manifest.n_pages

    report = QasmExportService(store, tmp_path / "qasm", QuantumSimExporter()).export_documents(
        [short]
    )

    assert [file.path.name for file in report.files] == [
        "aaaaaaaaaaaaaaaa-b01-c01-p01.qasm",
        "aaaaaaaaaaaaaaaa-b02-c01-p01.qasm",
    ]


def test_an_empty_corpus_is_a_warning(tmp_path):
    report = export(InMemoryStore(), tmp_path / "qasm")
    assert report.warnings == ("no stored documents: ingest a paper first",)


# ------------------------------------------------------------------ bindings and notes


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ({"theta": "0.1", "\\theta": "0.2"}, "parameter 'theta' is bound twice"),
        ({"theta": True}, "a parameter value cannot be true or false"),
        ({"theta": "\\phi"}, "has free symbol(s): phi"),
    ],
)
def test_bindings_the_emitter_cannot_use_are_usage_errors(tmp_path, values, reason):
    store = InMemoryStore()
    document(store, "a" * 64, THETA)
    out = tmp_path / "qasm"

    with pytest.raises(ValueError, match=re.escape(reason)):
        export(store, out, values=values)
    assert not out.exists()


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("R_y(\\frac{\\pi}{\\theta})", "0"),
        ("R_y(\\sqrt{\\theta})", "-1"),
        ("R_y(\\theta^{-1})", "0"),
        ("R_y(\\theta^{64})", "10000000000"),
    ],
)
def test_an_angle_undefined_after_binding_is_refused(tmp_path, label, value):
    store = InMemoryStore()
    document(store, "a" * 64, quantikz(f"& \\gate{{{label}}} & \\qw"))

    report = export(store, tmp_path / "qasm", values={"theta": value})

    assert not report.ok and report.files == ()
    assert "at the bound values" in report.refusals[0]


def test_a_braced_subscript_binding_matches_its_symbol(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, quantikz("& \\gate{R_y(\\theta_{1})} & \\qw"))

    report = export(store, tmp_path / "qasm", values={"\\theta_{1}": "0.5"})

    assert report.ok and not report.warnings
    assert "// bound: theta_1 = 0.5" in report.files[0].path.read_text()


def test_a_program_lists_only_the_bindings_of_its_circuit(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, THETA, quantikz("& \\gate{R_z(\\phi)} & \\qw"))

    report = export(store, tmp_path / "qasm", values={"theta": "0.25", "phi": "0.5"})

    assert report.ok
    first, second = (file.path.read_text() for file in report.files)
    assert "// bound: theta = 0.25" in first and "phi" not in first
    assert "// bound: phi = 0.5" in second and "theta" not in second


class NotingExporter:
    """An exporter whose circuit is fine but whose reader had something to say."""

    def check_values(self, values=None):
        return None

    def export(self, circuit, *, values=None, origin="", tool="docingest"):
        program = QasmProgram(1, None, "OPENQASM 3.0;\n", 1, 0)
        return QasmExport("ok", (program,), (), notes=("the label is not read",))


def test_reader_notes_of_an_ok_export_are_warnings(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)

    report = QasmExportService(store, tmp_path / "qasm", NotingExporter()).export_corpus()

    assert report.ok and report.written == 1
    assert report.warnings == ("a.md, block 1, circuit 1: the label is not read",)


def test_a_binding_counts_as_unmatched_when_the_exporter_does_not_report_it(tmp_path):
    # NotingExporter leaves ``unmatched`` at None: "not reported" must not mean "all matched"
    store = InMemoryStore()
    document(store, "a" * 64, H)

    report = QasmExportService(store, tmp_path / "qasm", NotingExporter()).export_corpus(
        values={"phi": "1"}
    )

    assert report.ok and report.written == 1
    assert "binding 'phi' matches no symbol of any exported circuit" in report.warnings


# ------------------------------------------------------------------ the composition root


def test_the_container_uses_an_exporter_override(tmp_path):
    cfg = AppConfig.model_validate({"output_dir": str(tmp_path / "out")})
    exporter = NotingExporter()
    container = Container(
        cfg, log=lambda _: None, overrides={"store": InMemoryStore(), "exporter": exporter}
    )
    assert container.qasm_export.exporter is exporter


def run_python(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    return result.stdout.strip()


def test_the_cli_does_not_import_quantum_sim():
    out = run_python(
        "import sys\nimport docingest.entrypoints.cli\nprint('quantum_sim' in sys.modules)"
    )
    assert out == "False"


def test_the_container_imports_quantum_sim_for_an_export(tmp_path):
    out = run_python(
        "import sys\n"
        "from docingest.bootstrap import Container\n"
        "from docingest.config import AppConfig\n"
        f"cfg = AppConfig.model_validate({{'output_dir': {str(tmp_path)!r}}})\n"
        "container = Container(cfg)\n"
        "before = 'quantum_sim' in sys.modules\n"
        "container.qasm_export\n"
        "print(before, 'quantum_sim' in sys.modules)"
    )
    assert out == "False True"
