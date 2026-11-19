"""Export the circuits of stored documents as OpenQASM 3 programs.

The canonical store holds Markdown, and a circuit reaches it as a fenced ``latex`` block. This
use case walks those blocks, calls the :class:`~docingest.ports.CircuitExporter` port once per
circuit, and writes or removes deterministic files. The emitter and its simulator reader sit
behind that port; this service neither imports them nor interprets a gate, a control or an angle.

It never changes the store, the manifest or the cache key: every file it writes is derived and can
be made again from ``document.md``. The output directory is a sibling of the document folders
(``<output_dir>/qasm`` by default), so ``DocumentStore.corpus`` never mistakes it for a document:
it has no ``manifest.json``.

Files are written atomically with the permissions of a normal write (the umask, as the filesystem
store does). The output folder is listed once per run. After a document's circuits are exported,
its ``<doc_id[:16]>-b*-c*-p*.qasm`` files that this run did not write are removed (a refused
circuit, a lost part, block or circuit), and the count is reported. Other documents' programs,
other files and a document whose Markdown cannot be read are left alone. When a removed program
belonged to a circuit refused because a symbol has no value (an export without ``--set``, as the
``--qasm`` hook runs), the note names the circuit and the symbol, so the caller can export it
again with a value.

A filesystem error while writing one document stops that document only: it is reported in
``QasmExportReport.errors``, the programs it already wrote stay in ``files``, its cleanup is
skipped (its earlier programs are kept), and the next document is exported. An error in the
cleanup is reported with the number of files it had removed. An output folder that cannot be
made stops the run with one error. ``documents`` in the report counts the documents the run
reached, and a run with an error gives no "matches no symbol" warning for a ``--set`` key (a
document whose Markdown cannot be read is a warning, not an error, and does not hold it back).
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.circuits import circuit_code_blocks, gate_list
from ..ports import CircuitExporter, DocumentStore, StoredDocument

__all__ = ["QasmExportReport", "QasmExportService", "QasmFile"]

_UMASK = os.umask(0)
os.umask(_UMASK)  # read once at import, as the filesystem store does; os.umask is process-global

# The name of a program: ``<doc_id[:16]>-bNN-cNN-pNN.qasm``.
_PROGRAM = re.compile(r"[0-9A-Za-z]{16}-b(?P<block>\d+)-c(?P<circuit>\d+)-p\d+\.qasm")


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
    """What one export wrote, removed, refused and warned about.

    ``documents`` counts the documents the run reached: a document whose Markdown could not be
    read counts, and so does one whose export stopped on an error, but the documents after an
    output folder error (which stops the run) do not. ``errors`` names each document whose
    export stopped on a filesystem error; the other documents were still exported. A run with
    errors gives no "matches no symbol" warning, because a document it could not finish may use
    the key. A document whose Markdown could not be read is a warning, not an error: the "matches
    no symbol" warning (which speaks of the exported circuits) is still given next to it.
    """

    out: Path
    documents: int
    circuits: int
    files: tuple[QasmFile, ...]
    refusals: tuple[str, ...]
    warnings: tuple[str, ...]
    removed: int = 0  # stale programs of the exported documents deleted by this run
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.refusals and not self.errors

    @property
    def written(self) -> int:
        return len(self.files)


@dataclass
class _Tally:
    """What a run has gathered so far; it fills the report. Mutable, and private to a run."""

    keys: set[str]
    notes: list[str]
    files: list[QasmFile] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    circuits: int = 0
    removed: int = 0


class QasmExportService:
    """Read stored documents, ask the exporter port, write one file per program."""

    def __init__(self, store: DocumentStore, out: Path, exporter: CircuitExporter):
        self.store = store
        self.out = Path(out)
        self.exporter = exporter

    def check_values(self, values: Mapping[str, str] | None = None) -> None:
        """Refuse a bad ``--set`` once, before a command exports any circuit."""
        self.exporter.check_values(values)

    def export_corpus(
        self,
        selectors: Sequence[str] = (),
        *,
        values: Mapping[str, str] | None = None,
        out: Path | None = None,
    ) -> QasmExportReport:
        """Export the selected documents (all of them when no selector is given)."""
        documents, warnings = self.store.corpus()
        if not documents:
            warnings = [*warnings, "no stored documents: ingest a paper first"]
        selected = _select(documents, selectors)
        return self._export(selected, values=values, out=out, warnings=warnings)

    def export_documents(
        self,
        documents: Sequence[StoredDocument],
        *,
        values: Mapping[str, str] | None = None,
        out: Path | None = None,
    ) -> QasmExportReport:
        """Export documents a caller already holds, as the post-ingest hook does.

        A document is exported once however often it is given (identical files share a doc_id),
        and as the corpus holds it when that is the canonical document or a longer variant: a
        partial or forced-OCR variant (``ingest --ocr-all``) gives way to it, because both write
        the same file names. Otherwise (no canonical document, and no longer variant) the
        variant given is exported as it is.
        """
        chosen: dict[str, StoredDocument] = {}
        for document in documents:
            chosen.setdefault(document.manifest.doc_id, document)
        if any(not document.canonical for document in chosen.values()):
            corpus = {document.manifest.doc_id: document for document in self.store.corpus()[0]}
            chosen = {doc_id: _served(corpus.get(doc_id), doc) for doc_id, doc in chosen.items()}
        return self._export(list(chosen.values()), values=values, out=out)

    def _export(
        self,
        documents: Sequence[StoredDocument],
        *,
        values: Mapping[str, str] | None,
        out: Path | None,
        warnings: Sequence[str] = (),
    ) -> QasmExportReport:
        self.exporter.check_values(values)
        target = Path(out or self.out)
        tally = _Tally(keys=set(values or {}), notes=list(warnings))
        earlier = _programs(target)  # the folder is listed once per run
        reached = 0  # the documents this run got to, whether or not their Markdown could be read
        for document in documents:
            reached += 1
            name = document.manifest.source_name
            try:
                markdown = self.store.markdown(document)
            except OSError as e:
                tally.notes.append(f"{name}: cannot read the stored Markdown ({e})")
                continue
            stale = earlier.get(document.manifest.doc_id[:16], ())
            try:
                self._document(document, markdown, target, values, tally=tally, earlier=stale)
            except OSError as e:
                if not target.is_dir():  # the folder itself cannot be made: one error, not one each
                    tally.errors.append(
                        f"cannot finish the export: the output folder {target} cannot be used "
                        f"({e}); the export stopped"
                    )
                    break
                tally.errors.append(
                    f"{name}: cannot finish the export: {e}; its earlier programs were kept"
                )
        if not tally.errors:  # a stopped document may have used the key: no claim either way
            for key in sorted(tally.keys):
                tally.notes.append(f"binding {key!r} matches no symbol of any exported circuit")
        return QasmExportReport(
            out=target,
            documents=reached,
            circuits=tally.circuits,
            files=tuple(tally.files),
            refusals=tuple(tally.refusals),
            warnings=tuple(tally.notes),
            removed=tally.removed,
            errors=tuple(tally.errors),
        )

    def _document(
        self,
        document: StoredDocument,
        markdown: str,
        target: Path,
        values: Mapping[str, str] | None,
        *,
        tally: _Tally,
        earlier: Sequence[str],
    ) -> None:
        """Export one document's circuits, then remove its stale programs (``earlier`` holds the
        names of its programs when the run started). An ``OSError`` from a write ends this
        document before its cleanup; what it wrote stays in ``tally``. An ``OSError`` in the
        cleanup is recorded here, with the files it had removed."""
        manifest = document.manifest
        name = manifest.source_name
        found = 0
        written: set[Path] = set()
        unbound: dict[tuple[int, int], tuple[str, ...]] = {}  # refused: a symbol has no value
        for block_no, block in enumerate(circuit_code_blocks(markdown), 1):
            for circuit_no, circuit in enumerate(gate_list(block.source).circuits, 1):
                found += 1
                tally.circuits += 1
                where = f"block {block_no}, circuit {circuit_no}"
                export = self.exporter.export(
                    circuit, values=values, origin=f"{name}, {where}", tool="docingest"
                )
                if export.unmatched is not None:  # None: not reported, so every key counts
                    tally.keys &= set(export.unmatched)
                tally.notes.extend(f"{name}, {where}: {note}" for note in export.notes)
                for program in export.programs:
                    path = target / _filename(manifest.doc_id, block_no, circuit_no, program.part)
                    _write(path, program.text)
                    written.add(path)
                    tally.files.append(
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
                tally.refusals.extend(f"{name}, {where}: {reason}" for reason in export.reasons)
                if export.unbound:
                    unbound[(block_no, circuit_no)] = export.unbound
        removed: list[Path] = []
        try:
            _remove_stale(target, earlier, keep=written, removed=removed)
        except OSError as e:
            tally.errors.append(
                f"{name}: cannot remove a stale QASM file: {e}; {len(removed)} removed, the "
                "others were kept"
            )
        tally.removed += len(removed)
        if removed:
            tally.notes.append(_stale_note(name, removed, unbound))
        if found == 0:
            tally.notes.append(f"{name}: no circuits found")


def _filename(doc_id: str, block: int, circuit: int, part: int) -> str:
    return f"{doc_id[:16]}-b{block:02d}-c{circuit:02d}-p{part:02d}.qasm"


def _served(held: StoredDocument | None, given: StoredDocument) -> StoredDocument:
    """The document the corpus holds when it is canonical or has more pages than the one given;
    else the one given. A tie goes to the variant the run stored, whatever the corpus picked."""
    if held is None:
        return given
    if held.canonical or held.manifest.n_pages > given.manifest.n_pages:
        return held
    return given


def _stale_note(
    name: str, removed: Sequence[Path], unbound: Mapping[tuple[int, int], tuple[str, ...]]
) -> str:
    """The warning for removed programs. A removed program of a circuit refused because a symbol
    has no value is named, with the symbols: an export with ``--set`` writes it again."""
    note = f"{name}: removed {len(removed)} stale QASM file(s) from an earlier export"
    lost = sorted({_block_and_circuit(path.name) for path in removed} & unbound.keys())
    if not lost:
        return note
    hints = []
    for block, circuit in lost:
        names = unbound[(block, circuit)]
        verb = "has" if len(names) == 1 else "have"
        hints.append(
            f"block {block}, circuit {circuit} was refused because {', '.join(names)} {verb} "
            "no value"
        )
    pronoun = "it" if len(lost) == 1 else "them"
    return (
        f"{note}; {'; '.join(hints)}: run docingest circuits export --set NAME=VALUE to "
        f"write {pronoun} again"
    )


def _block_and_circuit(filename: str) -> tuple[int, int]:
    """The block and circuit of a program's file name (``(0, 0)`` for any other name)."""
    match = _PROGRAM.fullmatch(filename)
    return (int(match["block"]), int(match["circuit"])) if match else (0, 0)


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


def _programs(target: Path) -> dict[str, list[str]]:
    """The program files in ``target`` when a run starts, by the 16-character doc_id prefix of
    their name. The folder is listed once per run, not once per document; an error listing it
    is raised, not ignored."""
    if not target.is_dir():
        return {}
    found: dict[str, list[str]] = {}
    with os.scandir(target) as entries:
        for entry in entries:
            if _PROGRAM.fullmatch(entry.name):
                found.setdefault(entry.name[:16], []).append(entry.name)
    return found


def _remove_stale(
    target: Path, earlier: Sequence[str], *, keep: set[Path], removed: list[Path]
) -> None:
    """Delete this document's programs that this run did not write, appending each to
    ``removed`` as it goes, so an error part-way keeps the count.

    ``earlier`` holds only ``<doc_id[:16]>-bNN-cNN-pNN.qasm`` names: the programs of other
    documents, temporary files and anything else in the folder stay, and so does a directory
    with a program's name. A program that is gone before it is deleted (another export removed
    it) is not an error and is not counted."""
    for filename in earlier:
        path = target / filename
        if path in keep or not path.is_file():
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        removed.append(path)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(descriptor, 0o666 & ~_UMASK)  # mkstemp creates 0600; match a normal write
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
