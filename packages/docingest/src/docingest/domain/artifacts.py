"""Layout artifacts read from the normalized Markdown: display equations and tables.

``extract_artifacts`` runs on the text that ``render_markdown`` wrote, so it sees every input
kind the same way and the page of an artifact is the page marker above it. Inline math
(``$x$``) and code blocks are not artifacts: a physics paper has thousands of inline
formulas, and the manifest is read on every query. Pure functions; no I/O.
"""

from __future__ import annotations

import re

from .blocks import find_blocks
from .models import ArtifactKind, LayoutArtifact
from .text import PAGE_MARKER, split_pages

_DELIMITED = (("$$", "$$"), ("\\[", "\\]"))
_ENVIRONMENT = re.compile(r"\\begin\{")


def checked_confidence(value: float | None) -> tuple[float | None, str | None]:
    """``value`` if it is a probability, else None and the warning that says why."""
    if value is None or 0.0 <= value <= 1.0:  # NaN fails the comparison and is rejected too
        return value, None
    return None, f"confidence {value} is outside [0, 1]: not kept"


def build_artifact(
    kind: ArtifactKind,
    page: int,
    *,
    text: str | None = None,
    latex: str | None = None,
    confidence: float | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> tuple[LayoutArtifact, list[str]]:
    """An artifact and the warnings about what was dropped from the values given.

    A confidence outside [0, 1] becomes None with a warning instead of an error, so one bad
    value from a layout reader never fails an ingest.
    """
    kept, warning = checked_confidence(confidence)
    artifact = LayoutArtifact(
        kind=kind, page=page, text=text, latex=latex, confidence=kept, bbox=bbox
    )
    return artifact, [warning] if warning else []


def display_latex(block_text: str) -> str:
    """The LaTeX inside a display block: its ``$$`` or ``\\[`` delimiters taken off. A raw
    ``\\begin{equation}`` environment is kept whole, since the environment is the math."""
    if not _ENVIRONMENT.match(block_text):
        for opener, closer in _DELIMITED:
            if block_text.startswith(opener) and block_text.endswith(closer):
                return block_text[len(opener) : -len(closer)].strip()
    return block_text.strip()


def extract_artifacts(markdown: str) -> tuple[list[LayoutArtifact], list[str]]:
    """The display equations and tables of ``markdown``, in order, with warnings.

    Each page is searched on its own, so an unclosed ``$$`` on a truncated page cannot pair
    with one of a later page and shift every block after it. ``page`` is 0-based, from the
    ``<!-- page N | ... -->`` marker above the block (N - 1, as ``PageRecord.index``); text
    above the first marker is page 0.
    """
    first = PAGE_MARKER.search(markdown)
    sections = [(0, markdown[: first.start()] if first else markdown)]
    if first:
        sections += [(max(n - 1, 0), body) for n, body in split_pages(markdown).items()]
    artifacts: list[LayoutArtifact] = []
    warnings: list[str] = []
    for page, body in sections:
        for block in find_blocks(body):
            if block.kind not in ("display", "table"):
                continue
            piece = body[block.start : block.end]
            if block.kind == "display":
                artifact, notes = build_artifact(
                    ArtifactKind.EQUATION, page, latex=display_latex(piece)
                )
            else:
                artifact, notes = build_artifact(ArtifactKind.TABLE, page, text=piece.strip())
            artifacts.append(artifact)
            warnings.extend(notes)
    return artifacts, warnings


def figure_artifacts(
    entries: list[dict[str, object]], n_pages: int
) -> tuple[list[LayoutArtifact], list[str]]:
    """The figures among what a converter found besides the text, with warnings.

    Only ``{"kind": "image", "page": <0-based>, "path": <relative>}`` entries are figures; the
    path (relative to the converter's output folder) is kept in ``text``. An entry that is
    malformed or names a page the document does not have is dropped with a warning, so one bad
    entry never fails an ingest. The same path on the same page is listed once.
    """
    figures: list[LayoutArtifact] = []
    warnings: list[str] = []
    seen: set[tuple[int, str]] = set()
    for entry in entries:
        if entry.get("kind") != "image":
            continue
        page, path = entry.get("page"), entry.get("path")
        if not (isinstance(page, int) and isinstance(path, str) and path):
            warnings.append(f"image entry {entry!r} has no page or path: not kept")
        elif not 0 <= page < n_pages:
            warnings.append(f"image {path!r} is on page {page}, outside the {n_pages} pages")
        elif (page, path) not in seen:
            seen.add((page, path))
            figures.append(LayoutArtifact(kind=ArtifactKind.FIGURE, page=page, text=path))
    return figures, warnings
