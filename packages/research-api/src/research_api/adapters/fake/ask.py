"""A fake answer engine and a fake paper reader over the invented corpus.

They stand in for docingest in fake mode and in the tests: no model server, no GPU, no files.
The engine takes a little time between its phases (``pause_s``) so a screen shows them, and it
answers from canned topics (see ``answers``). Three words in a question make it fail on
purpose, for the error screens: ``simulate-unavailable``, ``simulate-upstream`` and
``simulate-crash``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence

from ...domain.ask import (
    SHORT_ID,
    AskAnswer,
    AskContext,
    AskQuestion,
    AskTimings,
    Citation,
    GoldPassage,
    Phase,
    read_formatted_answer,
)
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
from ...domain.errors import NotFoundError, UnavailableError, UpstreamError
from ...domain.runs import CANNOT_ANSWER_PHRASE, ModelFacts
from ...ports.ask import EngineHealth
from .answers import (
    NO_EVIDENCE_EN,
    NO_EVIDENCE_ES,
    Rendered,
    is_spanish,
    pick_topic,
    render,
)
from .bench import ONLINE
from .corpus import PAPERS, FakePaper

GENERATION = "fake-0001"
NO_PAPERS = f"{CANNOT_ANSWER_PHRASE} this question due to having no papers."
CHUNK_CHARS = 900


class FakeAskEngine:
    """Answers from the invented corpus.

    ``hold`` lets a test keep a question in its answering phase until the event is set, to
    fill the queue deterministically.
    """

    def __init__(self, *, pause_s: float = 0.0, hold: asyncio.Event | None = None) -> None:
        self._pause_s = pause_s
        self.hold = hold
        self.questions_asked = 0
        self.closed = False

    def health(self) -> EngineHealth:
        return EngineHealth("ready", None, GENERATION)

    async def warm_up(self) -> None:
        return None

    async def answer_from(self, question: str, passages: Sequence[GoldPassage]) -> AskAnswer:
        """Echoes the passages: the answer is their text, cited from page 1. With none it
        refuses, in the words of PaperQA2 for a question with no papers."""
        started = time.perf_counter()
        await self._pause(2.0)
        contexts = tuple(
            AskContext(p.doc_id, f"{p.doc_id[:SHORT_ID]} pages 1-1", 0, 1, 1, False, p.text)
            for p in passages
        )
        sources = tuple(dict.fromkeys(p.doc_id for p in passages))
        citations = tuple(
            Citation(f"{doc_id[:SHORT_ID]} pages 1-1", doc_id, 1, 1) for doc_id in sources
        )
        wall = round(time.perf_counter() - started, 4)
        return AskAnswer(
            answer="\n\n".join(p.text for p in passages) or NO_PAPERS,
            references_text="",
            citations=citations,
            contexts=contexts,
            sources=sources,
            warnings=(),
            index_generation=None,
            timings=AskTimings(0.0, wall, wall),
        )

    def answer_model(self) -> ModelFacts:
        return ModelFacts(ONLINE)

    def close(self) -> None:
        self.closed = True

    async def _pause(self, factor: float = 1.0) -> None:
        if self._pause_s:
            await asyncio.sleep(self._pause_s * factor)

    async def ask(self, question: AskQuestion, on_phase: Callable[[Phase], None]) -> AskAnswer:
        self.questions_asked += 1
        started = time.perf_counter()
        text = question.text.lower()
        if question.options.use_index:
            on_phase("retrieving")
            await self._pause()
        retrieved = time.perf_counter()
        on_phase("answering")
        await self._pause(2.0)
        if self.hold is not None:
            await self.hold.wait()
        if "simulate-unavailable" in text:
            raise UnavailableError(
                "The model server did not answer (a simulated failure). Try again in a moment.",
                retry_after_s=5,
            )
        if "simulate-upstream" in text:
            raise UpstreamError("The model server refused the request (a simulated failure).")
        if "simulate-crash" in text:
            raise RuntimeError("simulated crash with the question inside: " + question.text)
        topic = pick_topic(question.text)
        spanish = is_spanish(question.text)
        if topic is None:
            rendered = Rendered(NO_EVIDENCE_ES if spanish else NO_EVIDENCE_EN, ())
            warnings: tuple[str, ...] = ()
        else:
            rendered = render(topic.spanish if spanish else topic.english)
            warnings = (topic.warning,) if topic.warning else ()
        read = read_formatted_answer(
            rendered.text, question=question.text, full_ids=(p.doc_id for p in PAPERS)
        )
        use_index = question.options.use_index
        contexts = tuple(self._context(p, a, b) for p, a, b in rendered.cited) if use_index else ()
        sources: list[str] = []
        for paper, _, _ in rendered.cited:
            if paper.doc_id not in sources:
                sources.append(paper.doc_id)
        done = time.perf_counter()
        return AskAnswer(
            answer=read.answer,
            references_text=read.references_text,
            citations=read.citations,
            contexts=contexts,
            sources=tuple(sources) if use_index else (),
            warnings=warnings,
            index_generation=GENERATION if use_index else None,
            timings=AskTimings(
                retrieve_s=round(retrieved - started, 4) if use_index else 0.0,
                answer_s=round(done - retrieved, 4),
                wall_s=round(done - started, 4),
            ),
        )

    @staticmethod
    def _context(paper: FakePaper, first: int, last: int) -> AskContext:
        joined = "\n\n".join(page.markdown for page in paper.pages[first - 1 : last])
        offset = sum(len(page.markdown) + 2 for page in paper.pages[: first - 1])
        return AskContext(
            doc_id=paper.doc_id,
            name=f"{paper.short_id} pages {first}-{last}",
            start=offset,
            first_page=first,
            last_page=last,
            is_reference=False,
            text=joined[:CHUNK_CHARS],
        )


class FakeDocumentReader:
    def __init__(self, papers: tuple[FakePaper, ...] = PAPERS) -> None:
        self._papers = {p.doc_id: p for p in papers}

    @staticmethod
    def _summary(paper: FakePaper) -> DocumentSummary:
        return DocumentSummary(
            doc_id=paper.doc_id,
            title=paper.title,
            source_name=paper.source_name,
            source_kind=paper.source_kind,
            pages=len(paper.pages),
            page_kind=page_kind_of(paper.source_kind),
        )

    def search(self, query: str, limit: int) -> DocumentList:
        needle = query.lower()
        found = [
            p
            for p in sorted(self._papers.values(), key=lambda p: (p.title, p.doc_id))
            if not needle or needle in p.title.lower() or p.doc_id.startswith(needle)
        ]
        return DocumentList(tuple(self._summary(p) for p in found[:limit]), len(found))

    def _paper(self, doc_id: str) -> FakePaper:
        return self._papers[resolve_doc_id(self._papers, doc_id)]

    def get(self, doc_id: str) -> DocumentDetail:
        paper = self._paper(doc_id)
        return DocumentDetail(
            summary=self._summary(paper),
            authors=paper.authors,
            year=paper.year,
            doi=None,
            arxiv_id=None,
            url=None,
            pages=tuple(
                PageInfo(n, page.method, page.title) for n, page in enumerate(paper.pages, start=1)
            ),
        )

    def page(self, doc_id: str, page: int) -> DocumentPage:
        paper = self._paper(doc_id)
        if not 1 <= page <= len(paper.pages):
            raise NotFoundError("No such page.")
        chosen = paper.pages[page - 1]
        return DocumentPage(
            doc_id=paper.doc_id,
            page=page,
            pages=len(paper.pages),
            kind=page_kind_of(paper.source_kind),
            title=chosen.title,
            markdown=chosen.markdown,
        )

    def text(self, doc_id: str) -> DocumentText:
        """The pages joined by a blank line, as the fake engine's contexts join them (the
        invented papers have no page markers)."""
        paper = self._paper(doc_id)
        return DocumentText(
            doc_id=paper.doc_id,
            source_name=paper.source_name,
            markdown="\n\n".join(page.markdown for page in paper.pages),
        )
