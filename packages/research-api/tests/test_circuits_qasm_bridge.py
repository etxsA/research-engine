"""The web export gives the files the command line writes.

A paper is stored with docingest's own filesystem store, exported to files by docingest's own
``QasmExportService`` (what ``docingest circuits export --doc`` runs), and exported again through
``POST /documents/{doc_id}/qasm``: the same file names, the same bytes, the same refusals and the
same warnings. The circuits are every LaTeX fixture of quantum-sim's bridge tests, so a new
fixture is checked here too. A pasted circuit gives quantum-sim's golden programs, with the
service's name in the first line and no source line.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from docingest.adapters.qasm import QuantumSimExporter
from docingest.adapters.store.filesystem import FilesystemStore
from docingest.application.qasm import QasmExportReport, QasmExportService
from docingest.domain.models import DocumentManifest, PageMethod, PageRecord, SourceKind
from fastapi.testclient import TestClient
from helpers import SEED_FILE, sign_in_as_member

from research_api.adapters.docingest_ask import DocingestDocumentReader
from research_api.bootstrap import Settings, build_container
from research_api.bootstrap.seed import load_seed_users
from research_api.entrypoints.http import build_app

FIXTURES = Path(__file__).resolve().parents[2] / "quantum-sim" / "tests" / "fixtures"
BRIDGE = sorted((FIXTURES / "bridge").glob("*.tex"))
GOLDENS = sorted((FIXTURES / "qasm").glob("*.json"))

# Blocks the bridge fixtures do not have: a symbol, two symbols singular together, a symbol with
# a long subscript, an input in |1>, and a block with no circuit in it.
EXTRA = [
    r"\begin{quantikz} & \gate{R_y(\theta)} & \qw \end{quantikz}",
    r"\begin{quantikz} & \gate{R_z(\frac{1}{\theta - \phi})} & \qw \end{quantikz}",
    r"\begin{quantikz} & \gate{R_x(\theta_{12})} & \qw \end{quantikz}",
    r"\begin{quantikz} \lstick{$\ket{1}$} & \gate{R_y(\theta)} & \qw \end{quantikz}",
    r"E = mc^2",
]


def test_the_fixtures_are_there() -> None:
    assert len(BRIDGE) == 37 and len(GOLDENS) >= 4


def fenced(source: str) -> str:
    return f"```latex\n{source.strip()}\n```"


def stored(store: FilesystemStore, name: str, pages: list[list[str]]) -> str:
    """Save a LaTeX paper whose page ``i`` holds the blocks ``pages[i]``; its doc_id."""
    doc_id = hashlib.sha256(name.encode()).hexdigest()
    markdown = "".join(
        f"<!-- page {n} | method=latex -->\nSection {n}.\n\n"
        + "\n\nSome text between the blocks.\n\n".join(blocks)
        + "\n\n"
        for n, blocks in enumerate(pages, 1)
    )
    manifest = DocumentManifest(
        doc_id=doc_id,
        source_path=f"/in/{name}",
        source_name=name,
        source_kind=SourceKind.LATEX,
        mime="application/x-tex",
        size_bytes=len(markdown),
        n_pages=len(pages),
        source_pages=len(pages),
        pages=[
            PageRecord(index=i, method=PageMethod.LATEX, n_chars=1, seconds=0.0, title=None)
            for i in range(len(pages))
        ],
        pipeline_version="test",
        config_hash="h",
    )
    store.save(manifest, markdown)
    return doc_id


@pytest.fixture
def store(tmp_path: Path) -> FilesystemStore:
    return FilesystemStore(tmp_path / "normalized")


@pytest.fixture
def api(store: FilesystemStore, client_for: Callable[..., TestClient]) -> TestClient:
    container = build_container(
        Settings.model_validate({"circuit_caps": {"max_ops": 10_000}}),
        fake=True,
        seed=load_seed_users(SEED_FILE),
        cheap_hashing=True,
        document_reader=DocingestDocumentReader(lambda: store),
    )
    return sign_in_as_member(client_for(build_app(container)))


def command_line(
    store: FilesystemStore, out: Path, doc_id: str, values: dict[str, str] | None
) -> QasmExportReport:
    return QasmExportService(store, out, QuantumSimExporter()).export_corpus(
        [doc_id], values=values
    )


def web(api: TestClient, doc_id: str, values: dict[str, str] | None) -> dict[str, Any]:
    response = api.post(f"/api/v1/documents/{doc_id[:16]}/qasm", json={"values": values})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def assert_same_export(report: QasmExportReport, body: dict[str, Any], out: Path) -> None:
    written = {file.path.name: file.path.read_bytes() for file in report.files}
    served = {
        program["filename"]: program["text"].encode("utf-8")
        for circuit in body["circuits"]
        for program in circuit["programs"]
    }
    assert sorted(served) == sorted(written) == sorted(p.name for p in out.glob("*.qasm"))
    for name, data in written.items():
        assert served[name] == data, name
    refusals = [
        f"{body['source_name']}, block {c['block']}, circuit {c['circuit']}: {reason}"
        for c in body["circuits"]
        for reason in c["reasons"]
    ]
    assert refusals == list(report.refusals)
    assert body["warnings"] == list(report.warnings)
    assert len(body["circuits"]) == report.circuits


PAPER_PAGES = [
    [fenced(path.read_text(encoding="utf-8")) for path in BRIDGE[:20]],
    [fenced(source) for source in EXTRA],
    [fenced(path.read_text(encoding="utf-8")) for path in BRIDGE[20:]],
]


@pytest.mark.parametrize(
    "values",
    [None, {"theta": "pi/4"}, {"\\theta": "\\pi/3", "phi": "0.25", "theta_{12}": "1"}, {"x": "1"}],
    ids=["no values", "theta", "three", "unmatched"],
)
def test_a_paper_exported_on_the_web_is_the_files_the_command_writes(
    api: TestClient, store: FilesystemStore, tmp_path: Path, values: dict[str, str] | None
) -> None:
    doc_id = stored(store, "bridge-paper.tex", PAPER_PAGES)
    out = tmp_path / "qasm"
    report = command_line(store, out, doc_id, values)
    assert report.written > 10 and report.refusals and report.errors == ()
    body = web(api, doc_id, values)
    assert body["doc_id"] == doc_id and body["source_name"] == "bridge-paper.tex"
    assert_same_export(report, body, out)


def test_the_unbound_names_are_those_the_command_names(
    api: TestClient, store: FilesystemStore
) -> None:
    doc_id = stored(store, "symbols.tex", [[fenced(source) for source in EXTRA]])
    body = web(api, doc_id, None)
    assert [(c["block"], c["unbound"]) for c in body["circuits"]] == [
        (1, ["theta"]),
        (2, ["phi", "theta"]),
        (3, ["theta_{12}"]),
        (4, []),  # refused for its |1> input too: a value would not export it
    ]


def test_a_paper_with_no_circuits_warns_as_the_command_warns(
    api: TestClient, store: FilesystemStore, tmp_path: Path
) -> None:
    doc_id = stored(store, "prose.tex", [["Only prose here, and $x^2$."]])
    out = tmp_path / "qasm"
    report = command_line(store, out, doc_id, {"theta": "1"})
    body = web(api, doc_id, {"theta": "1"})
    assert body["circuits"] == []
    assert (
        body["warnings"]
        == list(report.warnings)
        == [
            "prose.tex: no circuits found",
            "binding 'theta' matches no symbol of any exported circuit",
        ]
    )


def test_the_listed_blocks_are_the_blocks_the_command_numbers(
    api: TestClient, store: FilesystemStore, tmp_path: Path
) -> None:
    doc_id = stored(store, "bridge-paper.tex", PAPER_PAGES)
    out = tmp_path / "qasm"
    report = command_line(store, out, doc_id, {"theta": "1", "phi": "2", "theta_{12}": "3"})
    listed = api.get(f"/api/v1/documents/{doc_id}/circuits").json()
    assert [b["block"] for b in listed["blocks"]] == list(range(1, len(BRIDGE) + len(EXTRA) + 1))
    named = {(file.block, file.circuit) for file in report.files}
    with_circuits = {
        (b["block"], n) for b in listed["blocks"] for n in range(1, len(b["circuits"]) + 1)
    }
    assert named <= with_circuits
    assert listed["blocks"][len(BRIDGE[:20]) + 4]["status"] == "unparsed"


def test_nothing_is_written_next_to_the_paper(
    api: TestClient, store: FilesystemStore, tmp_path: Path
) -> None:
    doc_id = stored(store, "bridge-paper.tex", PAPER_PAGES)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    web(api, doc_id, {"theta": "1"})
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("golden", GOLDENS, ids=lambda p: p.stem)
def test_a_pasted_circuit_gives_the_golden_programs_under_the_services_name(
    api: TestClient, golden: Path
) -> None:
    expected = sorted(golden.parent.glob(f"{golden.stem}.qasm")) + sorted(
        golden.parent.glob(f"{golden.stem}.*.qasm")
    )
    response = api.post("/api/v1/circuits/qasm", json={"circuit_json": golden.read_text()})
    body = response.json()
    assert body["status"] == "ok", body["reasons"]
    assert [p["filename"] for p in body["programs"]] == [
        f"circuit-p{n:02d}.qasm" for n in range(1, len(expected) + 1)
    ]
    for program, path in zip(body["programs"], expected, strict=True):
        lines = path.read_text(encoding="utf-8").split("\n")
        assert lines[0] == "// Generated by quantum-sim (qasm format 1)"
        assert lines[1].startswith("// source: ")
        assert program["text"] == "\n".join(
            ["// Generated by research-api (qasm format 1)", *lines[2:]]
        )


def test_the_golden_fixtures_are_circuit_documents() -> None:
    for golden in GOLDENS:
        assert json.loads(golden.read_text())["format"] == "circuit/1"
