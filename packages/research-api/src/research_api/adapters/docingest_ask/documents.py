"""The converted papers of a docingest store, read for the API."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from docingest.domain.text import split_pages
from docingest.ports import DocumentStore, StoredDocument

from ...domain.documents import (
    DocumentDetail,
    DocumentList,
    DocumentPage,
    DocumentSummary,
    DocumentText,
    PageInfo,
    page_kind_of,
    resolve_doc_id,
)
from ...domain.errors import NotFoundError, UnavailableError

LISTING_TTL_S = 30.0  # the corpus listing reads every manifest; a new paper shows within this
LISTING_RETRY_S = 5
PAGES_KEPT = 8  # papers whose split pages are kept (a reader pages through one paper)


class DocingestDocumentReader:
    def __init__(
        self,
        open_store: Callable[[], DocumentStore],
        *,
        monotonic: Callable[[], float] = time.monotonic,
        ttl_s: float = LISTING_TTL_S,
    ) -> None:
        self._open_store = open_store
        self._opened: DocumentStore | None = None
        self._monotonic = monotonic
        self._ttl_s = ttl_s
        self._lock = threading.Lock()
        self._open_lock = threading.Lock()
        self._listing: tuple[float, dict[str, StoredDocument]] | None = None
        self._pages: OrderedDict[str, dict[int, str]] = OrderedDict()

    @property
    def _store(self) -> DocumentStore:
        with self._open_lock:  # two first requests must not both open it
            if self._opened is None:
                self._opened = self._open_store()
            return self._opened

    def _corpus(self) -> dict[str, StoredDocument]:
        with self._lock:
            now = self._monotonic()
            if self._listing is None or now - self._listing[0] >= self._ttl_s:
                try:
                    documents, _ = self._store.corpus()
                except OSError:  # the folder is gone or unreadable; it may be back in a moment
                    raise UnavailableError(
                        "The papers cannot be read right now.", retry_after_s=LISTING_RETRY_S
                    ) from None
                self._listing = (now, {d.manifest.doc_id: d for d in documents})
            return self._listing[1]

    @staticmethod
    def _title(document: StoredDocument) -> str:
        m = document.manifest
        return m.title or (m.metadata.title if m.metadata and m.metadata.title else m.source_name)

    def _summary(self, document: StoredDocument) -> DocumentSummary:
        m = document.manifest
        kind = m.source_kind.value
        return DocumentSummary(
            doc_id=m.doc_id,
            title=self._title(document),
            source_name=m.source_name,
            source_kind=kind,
            pages=len(m.pages),
            page_kind=page_kind_of(kind),
        )

    def search(self, query: str, limit: int) -> DocumentList:
        needle = query.lower()
        documents = sorted(
            self._corpus().values(), key=lambda d: (self._title(d), d.manifest.doc_id)
        )
        found = [
            d
            for d in documents
            if not needle
            or needle in self._title(d).lower()
            or d.manifest.doc_id.startswith(needle)
        ]
        return DocumentList(tuple(self._summary(d) for d in found[:limit]), len(found))

    def _document(self, doc_id: str) -> StoredDocument:
        corpus = self._corpus()
        return corpus[resolve_doc_id(corpus, doc_id)]

    def get(self, doc_id: str) -> DocumentDetail:
        document = self._document(doc_id)
        m = document.manifest
        meta = m.metadata
        return DocumentDetail(
            summary=self._summary(document),
            authors=tuple(meta.authors) if meta else (),
            year=meta.year if meta else None,
            doi=meta.doi if meta else None,
            arxiv_id=meta.arxiv_id if meta else None,
            url=meta.url if meta else None,
            pages=tuple(PageInfo(p.index + 1, p.method.value, p.title) for p in m.pages),
        )

    def _split(self, document: StoredDocument) -> dict[int, str]:
        doc_id = document.manifest.doc_id
        with self._lock:
            if doc_id in self._pages:
                self._pages.move_to_end(doc_id)
                return self._pages[doc_id]
        pages = split_pages(self._store.markdown(document))  # read outside the lock
        with self._lock:
            self._pages[doc_id] = pages
            while len(self._pages) > PAGES_KEPT:
                self._pages.popitem(last=False)
        return pages

    def page(self, doc_id: str, page: int) -> DocumentPage:
        document = self._document(doc_id)
        try:
            pages = self._split(document)
        except (OSError, UnicodeDecodeError):  # gone, unreadable, or not UTF-8
            raise NotFoundError("This paper's text is not available.") from None
        m = document.manifest
        # The manifest's count is the paper's page count everywhere (the listing, the detail and
        # here), so "page 2 of 5" never disagrees with the listing.
        if not 1 <= page <= len(m.pages) or page not in pages:
            raise NotFoundError("No such page.")
        titles = {p.index + 1: p.title for p in m.pages}
        return DocumentPage(
            doc_id=m.doc_id,
            page=page,
            pages=len(m.pages),
            kind=page_kind_of(m.source_kind.value),
            title=titles.get(page),
            markdown=pages[page],
        )

    def text(self, doc_id: str) -> DocumentText:
        """The whole ``document.md``, read fresh each time (the export reads a paper once)."""
        document = self._document(doc_id)
        try:
            markdown = self._store.markdown(document)
        except (OSError, UnicodeDecodeError):  # gone, unreadable, or not UTF-8
            raise NotFoundError("This paper's text is not available.") from None
        m = document.manifest
        return DocumentText(doc_id=m.doc_id, source_name=m.source_name, markdown=markdown)
