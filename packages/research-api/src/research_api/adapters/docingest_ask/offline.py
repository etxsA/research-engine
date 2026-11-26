"""Stand-ins for a service started without a docingest configuration."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ...domain.ask import AskAnswer, AskQuestion, GoldPassage, Phase
from ...domain.documents import DocumentDetail, DocumentList, DocumentPage, DocumentText
from ...domain.errors import NotFoundError, UnavailableError
from ...domain.runs import ModelFacts
from ...ports.ask import EngineHealth

REASON = "No docingest configuration is set, so there are no papers to ask about."


class OfflineAskEngine:
    """Sign-in, circuits and the rest work; asking says why it cannot."""

    def health(self) -> EngineHealth:
        return EngineHealth("unavailable", REASON)

    async def warm_up(self) -> None:
        return None

    async def ask(self, question: AskQuestion, on_phase: Callable[[Phase], None]) -> AskAnswer:
        raise UnavailableError(REASON)

    async def answer_from(self, question: str, passages: Sequence[GoldPassage]) -> AskAnswer:
        raise UnavailableError(REASON)

    def answer_model(self) -> ModelFacts:
        raise UnavailableError(REASON)

    def close(self) -> None:
        return None


class OfflineDocumentReader:
    def search(self, query: str, limit: int) -> DocumentList:
        return DocumentList((), 0)

    def get(self, doc_id: str) -> DocumentDetail:
        raise NotFoundError("No such document.")

    def page(self, doc_id: str, page: int) -> DocumentPage:
        raise NotFoundError("No such document.")

    def text(self, doc_id: str) -> DocumentText:
        raise NotFoundError("No such document.")
