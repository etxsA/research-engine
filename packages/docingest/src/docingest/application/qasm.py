"""Export the circuits of stored documents as OpenQASM 3 programs.

The canonical store holds Markdown, and a circuit reaches it as a fenced ``latex`` block. This
use case reads those blocks through the same domain reader that ``ask`` uses, emits one program per
part of a drawn equality, and writes deterministic files. It never changes the store, the
manifest or the cache key: every file it writes is derived and can be made again from
``document.md``.

The output directory is a sibling of the document folders (``<output_dir>/qasm`` by default), so
``DocumentStore.corpus`` never mistakes it for a document: it has no ``manifest.json``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..domain.circuits import circuit_code_blocks, gate_list
from ..domain.qasm import to_qasm
from ..ports import DocumentStore, StoredDocument

__all__ = ["QasmExportReport", "QasmExportService", "QasmFile"]


@dataclass(frozen=True)
class QasmFile:
    """One program written to disk and where it came from."""

    doc_id: str
    source_name: str
    block: int
    circuit: int
    part: int
    sign: str | None
    path: Path
    n_qubits: int
    n_clbits: int


@dataclass(frozen=True)
class QasmExportReport:
    """What one export wrote, refused and warned about."""

    out: Path
    documents: int
    circuits: int
    files: tuple[QasmFile, ...]
    refusals: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def written(self) -> int:
        return len(self.files)


class QasmExportService:
    """Read stored documents, emit their circuits, write one file per program."""

    def __init__(self, store: DocumentStore, out: Path):
        self.store = store
        self.out = Path(out)

    def export_corpus(
        self,
        selectors: Sequence[str] = (),
        *,
        values: Mapping[str, str] | None = None,
        out: Path | None = None,
    ) -> QasmExportReport:
        """Export the selected documents (all of them when no selector is given)."""
        documents, warnings = self.store.corpus()
        selected = _select(documents, selectors)
        return self._export(selected, values=values, out=out, warnings=warnings)

    def export_documents(
        self,
        documents: Sequence[StoredDocument],
        *,
        values: Mapping[str, str] | None = None,
        out: Path | None = None,
    ) -> QasmExportReport:
        """Export documents a caller already holds, as the post-ingest hook does."""
        return self._export(list(documents), values=values, out=out)

    def _export(
        self,
        documents: Sequence[StoredDocument],
        *,
        values: Mapping[str, str] | None,
        out: Path | None,
        warnings: Sequence[str] = (),
    ) -> QasmExportReport:
        target = Path(out or self.out)
        files: list[QasmFile] = []
        refusals: list[str] = []
        notes = list(warnings)
        circuits = 0
        for document in documents:
            manifest = document.manifest
            name = manifest.source_name
            try:
                markdown = self.store.markdown(document)
            except OSError as e:
                notes.append(f"{name}: cannot read the stored Markdown ({e})")
                continue
            found = 0
            for block_no, block in enumerate(circuit_code_blocks(markdown), 1):
                listing = gate_list(block.source)
                for circuit_no, circuit in enumerate(listing.circuits, 1):
                    found += 1
                    circuits += 1
                    export = to_qasm(
                        circuit,
                        values=values,
                        origin=f"{name}, block {block_no}, circuit {circuit_no}",
                    )
                    for program in export.programs:
                        filename = (
                            f"{manifest.doc_id[:16]}-b{block_no:02d}-"
                            f"c{circuit_no:02d}-p{program.part:02d}.qasm"
                        )
                        path = target / filename
                        _write(path, program.text)
                        files.append(
                            QasmFile(
                                doc_id=manifest.doc_id,
                                source_name=name,
                                block=block_no,
                                circuit=circuit_no,
                                part=program.part,
                                sign=program.sign,
                                path=path,
                                n_qubits=program.n_qubits,
                                n_clbits=program.n_clbits,
                            )
                        )
                    for reason in export.reasons:
                        refusals.append(f"{name}, block {block_no}, circuit {circuit_no}: {reason}")
            if found == 0:
                notes.append(f"{name}: no circuits found")
        return QasmExportReport(
            out=target,
            documents=len(documents),
            circuits=circuits,
            files=tuple(files),
            refusals=tuple(refusals),
            warnings=tuple(notes),
        )


def _select(documents: Sequence[StoredDocument], selectors: Sequence[str]) -> list[StoredDocument]:
    """Documents named by doc_id, source name or a unique doc_id prefix of at least 8 chars."""
    if not selectors:
        return list(documents)
    chosen: dict[str, StoredDocument] = {}
    for selector in selectors:
        matches = [
            document
            for document in documents
            if selector in {document.manifest.doc_id, document.manifest.source_name}
            or (len(selector) >= 8 and document.manifest.doc_id.startswith(selector))
        ]
        if not matches:
            raise ValueError(f"no stored document matches {selector!r}")
        if len(matches) > 1:
            raise ValueError(
                f"{selector!r} matches {len(matches)} stored documents; use a longer doc_id prefix"
            )
        chosen[matches[0].manifest.doc_id] = matches[0]
    return list(chosen.values())


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
