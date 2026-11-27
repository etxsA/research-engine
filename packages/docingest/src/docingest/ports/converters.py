"""Port: convert a whole document (office, LaTeX, text) into Markdown segments."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..domain.models import PageMethod, SourceMetadata


@dataclass(frozen=True)
class Segment:
    """One logical unit of a converted document (a section, a slide, the whole file)."""

    text: str
    title: str | None = None
    # Only for a segment that was made differently from the rest of its conversion (a LaTeX
    # section that fell back to plain text): its page record takes this method instead of
    # the conversion's, and these notes as its warnings.
    method: PageMethod | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Conversion:
    segments: list[Segment]
    method: PageMethod
    engine: str  # e.g. "pandoc 3.8", "docling 2.130"
    title: str | None = None
    metadata: SourceMetadata | None = None
    warnings: list[str] = field(default_factory=list)
    # True when a fallback ran because of the environment (timeout, crash, missing tool),
    # not because the input is unconvertible: the result must not be cached as canonical.
    degraded: bool = False
    # What a converter found besides the text (images, tables), one dict per item. The ingest
    # service does not store them yet; paths in them are relative to the converter's own
    # output folder.
    artifacts: list[dict[str, object]] = field(default_factory=list)


@runtime_checkable
class DocumentConverter(Protocol):
    fingerprint: str

    def convert(self, path: Path) -> Conversion:
        """Raise ``ConversionError`` when the document cannot be converted."""
        ...


@dataclass(frozen=True)
class MultimodalRequest:
    """What the ingest service knows about a document before a multimodal converter reads it."""

    doc_id: str  # sha256 of the file: names the converter's work folder
    source_pages: int  # pages (frames) of the source; the conversion has exactly this many segments
    ocr: bool  # some page has no usable text layer, so the whole document is read as a scan


@runtime_checkable
class AsyncDocumentConverter(Protocol):
    """A converter that runs outside the event loop: PDFs and images, one segment per page."""

    fingerprint: str  # everything that changes its output: tool version, model, settings

    async def convert_async(self, path: Path, request: MultimodalRequest) -> Conversion:
        """Raise ``ConversionError`` when the document cannot be converted (the ingest service
        then reads it the regular way)."""
        ...
