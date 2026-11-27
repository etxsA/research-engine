"""Papers of the corpus as the API shows them, and the rule that finds one by a short id."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from .errors import ConflictError, NotFoundError

SHORT_ID = 16
DOC_ID_PATTERN = r"^[0-9a-f]{16,64}$"
_DOC_ID = re.compile(DOC_ID_PATTERN)

# What a page number means: a printed page of a PDF, or a section of a LaTeX source (the page
# markers of a LaTeX paper number its sections).
PageKind = Literal["page", "section"]


@dataclass(frozen=True)
class DocumentSummary:
    doc_id: str
    title: str
    source_name: str
    source_kind: str  # "pdf", "latex", "office", "image", "text"
    pages: int
    page_kind: PageKind

    @property
    def short_id(self) -> str:
        return self.doc_id[:SHORT_ID]


@dataclass(frozen=True)
class PageInfo:
    page: int
    method: str
    title: str | None = None


@dataclass(frozen=True)
class DocumentDetail:
    summary: DocumentSummary
    authors: tuple[str, ...]
    year: int | None
    doi: str | None
    arxiv_id: str | None
    url: str | None
    pages: tuple[PageInfo, ...]


@dataclass(frozen=True)
class DocumentPage:
    doc_id: str
    page: int
    pages: int
    kind: PageKind
    title: str | None
    markdown: str


@dataclass(frozen=True)
class DocumentList:
    items: tuple[DocumentSummary, ...]
    total: int


@dataclass(frozen=True)
class DocumentText:
    """The whole converted text of a paper (``document.md``, page markers included), under its
    full id and its source file name."""

    doc_id: str
    source_name: str
    markdown: str


def is_doc_id(value: str) -> bool:
    """A full id or a prefix of one of at least 16 hex characters (what a citation prints)."""
    return _DOC_ID.fullmatch(value) is not None


def resolve_doc_id(known: Iterable[str], wanted: str) -> str:
    """The full id that ``wanted`` names: itself, or the one id it is a prefix of."""
    ids = list(known)
    if wanted in ids:
        return wanted
    matches = [doc_id for doc_id in ids if doc_id.startswith(wanted)]
    if not matches:
        raise NotFoundError("No such document.")
    if len(matches) > 1:
        raise ConflictError("More than one document starts with that id; give more characters.")
    return matches[0]


def page_kind_of(source_kind: str) -> PageKind:
    return "section" if source_kind == "latex" else "page"
