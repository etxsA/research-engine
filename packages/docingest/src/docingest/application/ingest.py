"""Use case: ingest one document -> canonical Markdown + manifest (via the store port)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from PIL.Image import Image

from ..domain.artifacts import extract_artifacts, figure_artifacts
from ..domain.errors import ConversionError, NotConfiguredError, UnsupportedInputError
from ..domain.layout import PageLayout
from ..domain.models import (
    DocumentManifest,
    ModelRef,
    PageMethod,
    PageProbe,
    PageRecord,
    SourceKind,
    SourceMetadata,
)
from ..domain.ocr_guards import (
    OcrGuards,
    OutputProblem,
    duplicate_pages,
    find_problem,
    image_area_sq_in,
)
from ..domain.routing import (
    RoutingPolicy,
    blank_probe,
    could_be_blank,
    decide,
    is_blank,
    is_page_number,
)
from ..domain.structure import structure_pages
from ..domain.text import (
    clean_text_layer,
    collapse_dot_leaders,
    render_markdown,
    text_vocabulary,
)
from ..ports import (
    AsyncDocumentConverter,
    DocumentConverter,
    DocumentStore,
    ImageSource,
    MultimodalRequest,
    OcrEngine,
    OcrPreflight,
    PdfPage,
    PdfReader,
    StoredDocument,
    TypeDetector,
)
from .ink import ink_ratio

PIPELINE_VERSION = "0.4.1"
SIDECAR_SUFFIX = ".meta.json"  # optional SourceMetadata next to an input file

Log = Callable[[str], None]


@dataclass(frozen=True)
class IngestOptions:
    force: bool = False  # ignore cached results
    ocr_all: bool = False  # OCR every PDF page, even with a good text layer
    max_pages: int | None = None  # only the first N pages / segments

    def __post_init__(self) -> None:
        if self.max_pages is not None and self.max_pages < 1:
            raise ValueError("max_pages must be >= 1")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sidecar(path: Path) -> SourceMetadata | None:
    sidecar = path.with_name(path.name + SIDECAR_SUFFIX)
    if not sidecar.exists():
        return None
    return SourceMetadata.model_validate_json(sidecar.read_text())


def retitle_markdown(markdown: str, old: str | None, new: str | None) -> str | None:
    """Swap the ``# title`` line :func:`render_markdown` wrote; page text is kept as is.

    Same output as rendering the pages again under ``new``. None when ``markdown`` does
    not start with the line rendered for ``old`` (edited by hand, another writer).
    """
    head = f"# {old}\n" if old else ""
    if not markdown.startswith(head):
        return None
    body = markdown[len(head) :].lstrip("\n")
    return "\n".join([f"# {new}\n" if new else "", body]).strip() + "\n"


# What a rejected OCR page falls back to: its routing probe and its raw text layer.
Layer = tuple[PageProbe, str]


class IngestService:
    def __init__(
        self,
        *,
        detector: TypeDetector,
        pdf: PdfReader,
        ocr: OcrEngine,
        images: ImageSource,
        converters: Mapping[SourceKind, DocumentConverter],
        store: DocumentStore,
        policy: RoutingPolicy,
        log: Log = print,
        guards: OcrGuards | None = None,
        multimodal: AsyncDocumentConverter | None = None,
    ):
        self.detector = detector
        self.pdf = pdf
        self.ocr = ocr
        self.images = images
        self.converters = converters
        self.store = store
        self.policy = policy
        self.log = log
        self.guards = guards or OcrGuards()
        self.multimodal = multimodal
        self._ocr_checked = False
        # pdfium, the OCR engine and the store index are not safe to share between threads
        self._blocking = threading.Lock()

    # ----------------------------------------------------------------- public
    def multimodal_hash(self, kind: SourceKind) -> str:
        """Cache key of ``ingest_async`` for a PDF or an image: the converter's fingerprint (tool
        version, model, settings) and, for a PDF, what decides how it is read. Kept apart from
        :meth:`config_hash`, so turning the converter on changes nothing for ``ingest``."""
        assert self.multimodal is not None
        parts = [self.multimodal.fingerprint]
        if kind == SourceKind.PDF:
            parts += [self.policy.cache_json(), self.pdf.fingerprint]
        key = json.dumps([PIPELINE_VERSION, kind.value, "multimodal", *parts])
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    async def ingest_async(
        self, path: Path, metadata: SourceMetadata | None = None
    ) -> StoredDocument:
        """Read a PDF or an image with the multimodal converter, one segment per page.

        Everything else (LaTeX, text, office) and any document the converter cannot read go
        through :meth:`ingest`, so ``ingest-async`` never stores less than ``ingest`` would.
        Blocking work runs in threads: the event loop of an API server stays free.
        """
        converter = self.multimodal
        if converter is None:
            raise NotConfiguredError(
                '[adapters] multimodal is "none": select "mineru" to use ingest-async'
            )
        path = path.resolve()
        kind, mime = self.detector.detect(path)
        if kind not in (SourceKind.PDF, SourceKind.IMAGE):
            return await asyncio.to_thread(self._locked, self.ingest, path, None, metadata)
        doc_id = await asyncio.to_thread(sha256_file, path)
        config_hash = self.multimodal_hash(kind)
        metadata = metadata or read_sidecar(path)
        cached = await asyncio.to_thread(
            self._locked, self._multimodal_cached, path, doc_id, config_hash, metadata
        )
        if cached is not None:
            return cached
        t0 = time.perf_counter()
        request, title = await asyncio.to_thread(
            self._locked, self._multimodal_request, path, kind, doc_id
        )
        try:
            conversion = await converter.convert_async(path, request)
            if len(conversion.segments) != request.source_pages:
                raise ConversionError(
                    f"the converter returned {len(conversion.segments)} segment(s) for "
                    f"{request.source_pages} page(s)"
                )
        except ConversionError as error:
            self.log(f"  {path.name}: {error}; reading it the regular way")
            return await asyncio.to_thread(self._locked, self.ingest, path, None, metadata)
        total = round(time.perf_counter() - t0, 2)
        records = [
            PageRecord(
                index=i,
                method=segment.method or conversion.method,
                n_chars=len(segment.text),
                seconds=round(total / len(conversion.segments), 3),
                engine=conversion.engine,
                warnings=list(segment.warnings),
            )
            for i, segment in enumerate(conversion.segments)
        ]
        manifest = DocumentManifest(
            doc_id=doc_id,
            source_path=str(path),
            source_name=path.name,
            source_kind=kind,
            mime=mime,
            size_bytes=path.stat().st_size,
            n_pages=len(records),
            source_pages=request.source_pages,
            pages=records,
            pipeline_version=PIPELINE_VERSION,
            config_hash=config_hash,
            title=(metadata.title if metadata and metadata.title else None) or title or path.stem,
            metadata=metadata,
            total_seconds=total,
        )
        self.log(f"  {conversion.method.value}: {len(records)} page(s) via {conversion.engine}")
        texts = [segment.text for segment in conversion.segments]
        markdown = render_markdown(manifest, texts)
        artifacts, notes = extract_artifacts(markdown)
        figures, figure_notes = figure_artifacts(conversion.artifacts, len(records))
        for note in [*notes, *figure_notes]:
            self.log(f"  warning: layout artifact: {note}")
        manifest = manifest.model_copy(
            update={"artifacts": sorted([*artifacts, *figures], key=lambda a: a.page)}
        )
        return await asyncio.to_thread(self._locked, self.store.save, manifest, markdown)

    def _locked[**P, T](self, fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
        """Run one of the steps that cannot overlap with another document's (see ``_blocking``)."""
        with self._blocking:
            return fn(*args, **kwargs)

    def _multimodal_cached(
        self, path: Path, doc_id: str, config_hash: str, metadata: SourceMetadata | None
    ) -> StoredDocument | None:
        cached = self.store.lookup(doc_id, config_hash, max_pages=None, ocr_all=False)
        if not cached:
            return None
        self.log(f"[cache] {path.name} -> {cached.location} (config {config_hash})")
        if metadata is None or metadata == cached.manifest.metadata:
            return cached
        refreshed = self._refresh_metadata(cached, metadata)
        if refreshed is None:
            self.log("  stored Markdown is not what this pipeline wrote: re-processing")
        return refreshed

    def _multimodal_request(
        self, path: Path, kind: SourceKind, doc_id: str
    ) -> tuple[MultimodalRequest, str | None]:
        """Pages of the source and whether it is read as a scan. A PDF is a scan as soon as one
        page needs OCR by the routing policy (the per-page signals ``ingest`` uses) and is not
        a blank one: a mixed document read as text would lose its scanned pages."""
        if kind == SourceKind.IMAGE:
            return MultimodalRequest(doc_id, len(self.images.frames(path)), ocr=True), None
        doc = self.pdf.open(path)
        try:
            ocr = any(self._page_needs_scan(doc.page(i)) for i in range(len(doc)))
            return MultimodalRequest(doc_id, len(doc), ocr), doc.title
        finally:
            doc.close()

    def _page_needs_scan(self, page: PdfPage) -> bool:
        probe = decide(page.signals()[0], self.policy)
        if not probe.needs_ocr:
            return False
        if not could_be_blank(probe, self.policy):
            return True
        # a blank page, or one with a few characters and no other ink, is what ingest() keeps
        # as it is: it does not make the whole document a scan
        image = page.render(self.ocr.dpi)
        ink = ink_ratio(image, self.policy.blank_ink_contrast, self.policy.blank_dark_page)
        return not is_blank(probe, ink, self.policy)

    def config_hash(self, kind: SourceKind, *, ocr_all: bool = False) -> str:
        """Cache key: everything that can change this kind's output, nothing else."""
        match kind:
            case SourceKind.PDF:
                parts = [
                    self.policy.cache_json(),
                    self.guards.model_dump_json(),
                    self.pdf.fingerprint,
                    self.ocr.fingerprint,
                ]
            case SourceKind.IMAGE:
                parts = [
                    self.guards.model_dump_json(),
                    self.images.fingerprint,
                    self.ocr.fingerprint,
                ]
            case _:
                parts = [self._converter(kind).fingerprint]
        key = json.dumps([PIPELINE_VERSION, kind.value, ocr_all, *parts])
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    def ingest(
        self,
        path: Path,
        options: IngestOptions | None = None,
        metadata: SourceMetadata | None = None,
    ) -> StoredDocument:
        options = options or IngestOptions()
        path = path.resolve()
        kind, mime = self.detector.detect(path)
        doc_id = sha256_file(path)
        ocr_all = options.ocr_all and kind == SourceKind.PDF
        config_hash = self.config_hash(kind, ocr_all=ocr_all)
        metadata = metadata or read_sidecar(path)

        if not options.force:
            cached = self.store.lookup(
                doc_id, config_hash, max_pages=options.max_pages, ocr_all=ocr_all
            )
            if cached:
                self.log(f"[cache] {path.name} -> {cached.location} (config {config_hash})")
                if metadata is None or metadata == cached.manifest.metadata:
                    return cached
                refreshed = self._refresh_metadata(cached, metadata)
                if refreshed is not None:
                    return refreshed
                self.log("  stored Markdown is not what this pipeline wrote: re-processing")

        t0 = time.perf_counter()
        title = None
        degraded = False
        match kind:
            case SourceKind.PDF:
                records, texts, title, source_pages = self._pdf(path, options)
            case SourceKind.IMAGE:
                records, texts, source_pages = self._image(path, options)
            case _:
                records, texts, title, source_pages, conv_meta, degraded = self._convert(
                    path, kind, options
                )
                metadata = metadata or conv_meta

        uses_ocr = any(r.method == PageMethod.VLM_OCR for r in records)
        manifest = DocumentManifest(
            doc_id=doc_id,
            source_path=str(path),
            source_name=path.name,
            source_kind=kind,
            mime=mime,
            size_bytes=path.stat().st_size,
            n_pages=len(records),
            source_pages=source_pages,
            max_pages=options.max_pages,
            ocr_all=ocr_all,
            pages=records,
            pipeline_version=PIPELINE_VERSION,
            config_hash=config_hash,
            ocr_model=self.ocr.model if uses_ocr else None,
            title=(metadata.title if metadata and metadata.title else None) or title or path.stem,
            metadata=metadata,
            total_seconds=round(time.perf_counter() - t0, 2),
        )
        if degraded:
            self.log(
                "  warning: degraded fallback conversion (timeout / crash): stored outside the"
                " cache, the next run retries the converter"
            )
        markdown = render_markdown(manifest, texts)
        artifacts, notes = extract_artifacts(markdown)
        for note in notes:
            self.log(f"  warning: layout artifact: {note}")
        manifest = manifest.model_copy(update={"artifacts": artifacts})
        return self.store.save(manifest, markdown, degraded=degraded)

    def _refresh_metadata(
        self, cached: StoredDocument, metadata: SourceMetadata
    ) -> StoredDocument | None:
        """Put new bibliographic metadata on a cached result without re-processing it.

        doc_id and config_hash cover only the bytes and the adapters, so a sidecar
        written or edited later, or a crawl that has since learned the license, would
        otherwise never reach the manifest short of ``--force``, which re-runs OCR or
        pandoc. Only the manifest's metadata and title, the Markdown's ``# title`` line
        and (through ``save``) the store's catalog change. A new title comes from the
        new metadata; without one the stored title is kept, since the converter's own
        title is not known without converting again. None: re-process instead.
        """
        old = cached.manifest
        title = metadata.title or old.title
        try:
            markdown = retitle_markdown(self.store.markdown(cached), old.title, title)
        except OSError:  # the manifest survived but its Markdown did not
            return None
        if markdown is None:
            return None
        self.log("  metadata changed: manifest and title updated, not re-processed")
        manifest = old.model_copy(update={"metadata": metadata, "title": title})
        return self.store.save(manifest, markdown)

    # --------------------------------------------------------------- handlers
    def _converter(self, kind: SourceKind) -> DocumentConverter:
        try:
            return self.converters[kind]
        except KeyError:
            raise UnsupportedInputError(
                f"no converter configured for {kind.value} inputs"
            ) from None

    def _require_ocr(self) -> None:
        """Before the first page that needs OCR: stop the run if no page could be read.

        Without this a missing backend fails file after file, deep into a long run.
        Runs that never reach OCR (text-layer PDFs, blank pages, cached results,
        converters) never pay for it, and the engine checks only what is cheap (see
        ``OcrPreflight``).
        """
        if self._ocr_checked:
            return
        if isinstance(self.ocr, OcrPreflight):
            self.ocr.check()
        self._ocr_checked = True

    def _pdf(
        self, path: Path, options: IngestOptions
    ) -> tuple[list[PageRecord], list[str], str | None, int]:
        doc = self.pdf.open(path)
        try:
            title = doc.title
            total = len(doc)
            n = total if options.max_pages is None else min(total, options.max_pages)
            records: list[PageRecord] = []
            texts: list[str] = []
            raw_layers: dict[int, str] = {}
            ocr_context: dict[int, Layer] = {}
            layouts: dict[int, PageLayout] = {}
            for i in range(n):
                page = doc.page(i)
                t0 = time.perf_counter()
                signals, raw = page.signals()
                probe = decide(signals, self.policy, force_ocr=options.ocr_all)
                if probe.needs_ocr:
                    image = page.render(self.ocr.dpi)
                    image.info["dpi"] = (self.ocr.dpi, self.ocr.dpi)  # a page has a physical size
                    if could_be_blank(probe, self.policy):
                        ink = ink_ratio(
                            image, self.policy.blank_ink_contrast, self.policy.blank_dark_page
                        )
                        if is_blank(probe, ink, self.policy):
                            if any(c.isalpha() for c in raw) and not is_page_number(raw):
                                # "Index", "Part I": nothing is printed beyond the few
                                # characters of the layer, so the layer is the page.
                                self.log(f"  p{i + 1}/{n}: short text layer, no other ink")
                                raw_layers[i] = raw
                                records.append(
                                    PageRecord(
                                        index=i,
                                        method=PageMethod.TEXT_LAYER,
                                        n_chars=0,  # set after cleaning
                                        seconds=round(time.perf_counter() - t0, 3),
                                        engine=self.pdf.fingerprint,
                                        probe=probe.model_copy(
                                            update={
                                                "needs_ocr": False,
                                                "reasons": [
                                                    f"{probe.n_chars} embedded chars and no other"
                                                    " ink: the text layer is the page"
                                                ],
                                            }
                                        ),
                                    )
                                )
                                texts.append("")
                                continue
                            blank = blank_probe(probe, ink, self.policy)
                            self.log(f"  p{i + 1}/{n}: {blank.reasons[0]}")
                            records.append(
                                PageRecord(
                                    index=i,
                                    method=PageMethod.BLANK,
                                    n_chars=0,
                                    seconds=round(time.perf_counter() - t0, 3),
                                    engine=self.pdf.fingerprint,
                                    probe=blank,
                                )
                            )
                            texts.append("")
                            continue
                    self.log(f"  p{i + 1}/{n}: OCR ({'; '.join(probe.reasons)})")
                    self._require_ocr()
                    record, text, rejected = self._ocr_record(i, image, probe)
                    ocr_context[i] = (probe, raw)
                    if rejected is not None:
                        record, text = self._rejected(record, rejected, raw_layers, (probe, raw))
                else:
                    self.log(f"  p{i + 1}/{n}: text layer ({probe.n_chars} chars)")
                    raw_layers[i] = raw
                    layouts[i] = page.layout()
                    record = PageRecord(
                        index=i,
                        method=PageMethod.TEXT_LAYER,
                        n_chars=0,  # set after cleaning
                        seconds=round(time.perf_counter() - t0, 3),
                        engine=self.pdf.fingerprint,
                        probe=probe,
                    )
                    text = ""
                records.append(record)
                texts.append(text)
        finally:
            doc.close()

        self._drop_repeats(records, texts, raw_layers, ocr_context)
        self._finish_pages(records, texts, raw_layers, layouts)
        return records, texts, title, total

    def _finish_pages(
        self,
        records: list[PageRecord],
        texts: list[str],
        raw_layers: dict[int, str],
        layouts: dict[int, PageLayout],
    ) -> None:
        """Last steps on the pages of a PDF: the text of the text-layer pages, then the
        clean-up that every page gets."""
        self._write_text_layers(records, texts, raw_layers, layouts)
        self._collapse_leaders(records, texts)

    @staticmethod
    def _collapse_leaders(records: list[PageRecord], texts: list[str]) -> None:
        """Contents lines lose their dot leaders, whichever way the page was read.

        Runs after the output checks and the text-layer cleaning, which see the text as it
        came; ``n_chars`` counts the text that is written.
        """
        for i, text in enumerate(texts):
            if (collapsed := collapse_dot_leaders(text)) != text:
                texts[i] = collapsed
                records[i].n_chars = len(collapsed)

    def _write_text_layers(
        self,
        records: list[PageRecord],
        texts: list[str],
        raw_layers: dict[int, str],
        layouts: dict[int, PageLayout],
    ) -> None:
        """Set the text of every page that keeps its text layer.

        Structure and de-hyphenation use the whole document's vocabulary and pages: a running
        header is a line that comes back on other pages. Only pages routed to the text layer
        have a layout. A page whose layer stands in for rejected OCR, or for the blank-page
        rule, keeps its flat text: routing judged that layer unreliable (or the page nearly
        empty), so its geometry is not trusted either.
        """
        vocab = text_vocabulary(*raw_layers.values(), *texts)
        structured = structure_pages(layouts, vocab)
        for i, raw in raw_layers.items():
            if page := structured.get(i):
                texts[i] = page.text
                records[i].removed_lines = list(page.removed)
            else:
                texts[i] = clean_text_layer(raw, vocab)
            records[i].n_chars = len(texts[i])

    def _image(self, path: Path, options: IngestOptions) -> tuple[list[PageRecord], list[str], int]:
        frames = self.images.frames(path)  # multi-page TIFF -> several frames
        total = len(frames)
        if options.max_pages is not None:
            frames = frames[: options.max_pages]
        self._require_ocr()
        records, texts = [], []
        for i, frame in enumerate(frames):
            self.log(f"  frame {i + 1}/{len(frames)}: OCR (image input)")
            record, text, rejected = self._ocr_record(i, frame, None)
            if rejected is not None:
                record, text = self._rejected(record, rejected, {}, None)
            records.append(record)
            texts.append(text)
        self._drop_repeats(records, texts, {}, {})
        self._collapse_leaders(records, texts)
        return records, texts, total

    def _convert(
        self, path: Path, kind: SourceKind, options: IngestOptions
    ) -> tuple[list[PageRecord], list[str], str | None, int, SourceMetadata | None, bool]:
        t0 = time.perf_counter()
        conv = self._converter(kind).convert(path)
        for w in conv.warnings:
            self.log(f"  warning: {w}")
        segments = conv.segments
        total = len(segments)
        if options.max_pages is not None:
            segments = segments[: options.max_pages]
        per = round((time.perf_counter() - t0) / max(len(segments), 1), 3)
        records = [
            PageRecord(
                index=i,
                method=s.method or conv.method,
                n_chars=len(s.text),
                seconds=per,
                engine=conv.engine,
                title=s.title,
                warnings=list(s.warnings),
            )
            for i, s in enumerate(segments)
        ]
        self.log(f"  {conv.method.value}: {len(segments)} segment(s) via {conv.engine}")
        texts = [s.text for s in segments]
        return records, texts, conv.title, total, conv.metadata, conv.degraded

    def _ocr_record(
        self, i: int, image: Image, probe: PageProbe | None
    ) -> tuple[PageRecord, str, str | None]:
        """Transcribe one page. The last item is why the output cannot be used, if it cannot."""
        res = self.ocr.transcribe(image)
        self.log(
            f"    -> {len(res.text)} chars, {res.gen_tokens} tok in {res.seconds:.1f}s"
            f" ({res.finish_reason})"
        )
        area = image_area_sq_in(image.width, image.height, image.info.get("dpi"))
        problem = find_problem(res.text, res.finish_reason, area, self.guards)
        warnings: list[str] = []
        rejected = None
        if problem is OutputProblem.TOO_LONG:
            rejected = "more text than the page can hold"
        elif problem is OutputProblem.LOOP:
            rejected = "repeating text"
        elif problem is OutputProblem.TRUNCATED:
            warnings.append(
                f"OCR stopped at the token limit ({res.gen_tokens} tokens): the page may be cut off"
            )
            self.log(f"    warning: {warnings[0]}")
        ref: ModelRef = self.ocr.model
        return (
            PageRecord(
                index=i,
                method=PageMethod.VLM_OCR,
                n_chars=len(res.text),
                seconds=round(res.seconds, 2),
                engine=self.ocr.fingerprint,
                probe=probe,
                model=ref.repo_id,
                model_revision=ref.revision,
                gen_tokens=res.gen_tokens,
                finish_reason=res.finish_reason,
                warnings=warnings,
            ),
            res.text,
            rejected,
        )

    def _rejected(
        self,
        record: PageRecord,
        why: str,
        raw_layers: dict[int, str],
        layer: Layer | None,
    ) -> tuple[PageRecord, str]:
        """Replace OCR output that cannot be trusted: the text layer if the page has a
        usable one (its text comes back through ``raw_layers``), else an empty page.

        A layer is usable when it has text and is not garbled; a page that went to OCR
        because its layer was scanned-in noise gets the empty page, not the noise.
        """
        if (
            layer
            and layer[0].n_chars > 0
            and layer[0].garbage_ratio <= self.policy.max_garbage_ratio
        ):
            probe, raw = layer
            warning = f"OCR output rejected ({why}); the text layer was used"
            raw_layers[record.index] = raw
            record = PageRecord(
                index=record.index,
                method=PageMethod.TEXT_LAYER,
                n_chars=0,  # set after cleaning
                seconds=record.seconds,
                engine=self.pdf.fingerprint,
                probe=probe,
                warnings=[warning],
            )
        else:
            warning = f"OCR output rejected ({why}); the page was left empty"
            record = record.model_copy(update={"n_chars": 0, "warnings": [warning]})
        self.log(f"    warning: {warning}")
        return record, ""

    def _drop_repeats(
        self,
        records: list[PageRecord],
        texts: list[str],
        raw_layers: dict[int, str],
        context: Mapping[int, Layer],
    ) -> None:
        """Pages of this document whose OCR text repeats an earlier page's (see
        :func:`~docingest.domain.ocr_guards.duplicate_pages`) are replaced like any other
        rejected output; the first page of each group is kept."""
        ocr = {
            i: text
            for i, (rec, text) in enumerate(zip(records, texts, strict=True))
            if rec.method == PageMethod.VLM_OCR
        }
        for i, first in sorted(duplicate_pages(ocr, self.guards).items()):
            records[i], texts[i] = self._rejected(
                records[i], f"same text as page {first + 1}", raw_layers, context.get(i)
            )
