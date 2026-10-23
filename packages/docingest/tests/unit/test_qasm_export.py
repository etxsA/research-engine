"""``QasmExportService``: select stored documents, emit their circuits, write the files."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes import InMemoryStore, add_doc

from docingest.application.qasm import QasmExportService


def quantikz(*rows: str) -> str:
    return "\\begin{quantikz}\n" + " \\\\\n".join(rows) + "\n\\end{quantikz}"


H = quantikz("& \\gate{H} & \\qw")
CNOT = quantikz("& \\ctrl{1} & \\qw", "& \\targ{} & \\qw")
THETA = quantikz("& \\gate{R_y(\\theta)} & \\qw")


def document(store, doc_id: str, *circuits: str, name: str = "a.md"):
    body = "\n\n".join(f"``` latex\n{circuit}\n```" for circuit in circuits)
    add_doc(store, doc_id, [(body, "")], name=name)


def export(store, out: Path, selectors=(), values=None):
    return QasmExportService(store, out).export_corpus(selectors, values=values)


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


def test_set_binds_a_free_parameter(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, THETA)

    refused = export(store, tmp_path / "refused")
    assert refused.files == () and "theta" in refused.refusals[0]

    bound = export(store, tmp_path / "bound", values={"theta": "pi/4"})
    assert bound.ok
    assert "ry(pi/4) q[0];" in bound.files[0].path.read_text()


def test_out_overrides_the_service_default(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    default, custom = tmp_path / "default", tmp_path / "custom"

    report = QasmExportService(store, default).export_corpus(out=custom)

    assert report.out == custom
    assert report.files[0].path.parent == custom
    assert not default.exists()


def test_export_documents_is_the_post_ingest_path(tmp_path):
    store = InMemoryStore()
    document(store, "a" * 64, H)
    docs, _ = store.corpus()

    report = QasmExportService(store, tmp_path / "qasm").export_documents(docs)

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
