"""The invented corpus and the fake engine: what the demo shows has to be consistent."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from helpers import arun

from research_api.adapters.fake import FakeAskEngine, FakeDocumentReader
from research_api.adapters.fake.answers import (
    BY_KEY,
    NO_EVIDENCE_EN,
    NO_EVIDENCE_ES,
    TOPICS,
    is_spanish,
    pick_topic,
    render,
)
from research_api.adapters.fake.corpus import PAPERS, TELEPORT, TRANSMON, FakePaper
from research_api.domain.ask import (
    AskAnswer,
    AskOptions,
    AskQuestion,
    GoldPassage,
    read_formatted_answer,
)
from research_api.domain.errors import NotFoundError, UnavailableError, UpstreamError

HEX64 = re.compile(r"[0-9a-f]{64}")


def test_the_papers_have_distinct_stable_ids() -> None:
    assert len({p.doc_id for p in PAPERS}) == len(PAPERS)
    assert len({p.short_id for p in PAPERS}) == len(PAPERS)
    assert all(HEX64.fullmatch(p.doc_id) for p in PAPERS)
    assert TRANSMON.doc_id == TRANSMON.doc_id  # same every run
    assert TRANSMON.short_id == TRANSMON.doc_id[:16]


def test_the_corpus_is_marked_as_invented() -> None:
    for paper in PAPERS:
        assert paper.title.endswith("(synthetic)")
        assert paper.source_name.startswith("synthetic-")
        assert all(page.markdown.strip() for page in paper.pages)


def test_only_the_latex_paper_has_sections_with_titles() -> None:
    for paper in PAPERS:
        titled = [p.title for p in paper.pages]
        assert (paper.source_kind == "latex") == all(titled)
        assert (paper.source_kind == "latex") == (paper is TELEPORT)


@pytest.mark.parametrize("language", ["english", "spanish"])
def test_every_canned_answer_cites_pages_that_exist(language: str) -> None:
    for topic in TOPICS:
        rendered = render(getattr(topic, language))
        assert rendered.cited, topic.keywords
        for paper, first, last in rendered.cited:
            assert 1 <= first <= last <= len(paper.pages)
        read = read_formatted_answer(rendered.text, full_ids=[p.doc_id for p in PAPERS])
        assert [(c.doc_id, c.first_page, c.last_page) for c in read.citations] == [
            (p.doc_id, a, b) for p, a, b in rendered.cited
        ]
        assert "References" not in read.answer and "[[" not in rendered.text
        # every citation in the text is in the list and the other way round
        inline = set(re.findall(r"\(([0-9a-f]{16}) pages (\d+)-(\d+)\)", read.answer))
        listed = {(p.short_id, str(a), str(b)) for p, a, b in rendered.cited}
        assert inline == listed


def test_both_languages_cite_the_same_pages() -> None:
    for topic in TOPICS:
        assert render(topic.english).cited == render(topic.spanish).cited


def test_the_placeholder_names_only_known_papers() -> None:
    keys = set(re.findall(r"\[\[([a-z-]+):", "".join(t.english + t.spanish for t in TOPICS)))
    assert keys <= set(BY_KEY)


@pytest.mark.parametrize(
    ("question", "paper"),
    [
        ("What limits T1 in transmons?", "transmon"),
        ("¿Qué limita la coherencia de un transmón?", "transmon"),
        ("What is the surface code threshold?", "surface"),
        ("umbral del código de superficie", "surface"),
        ("How does the teleportation circuit use mid-circuit measurement?", "teleport"),
        ("Explica la teletransportación cuántica", "teleport"),
        ("Why does Grover search need fewer iterations with a noisy oracle?", "grover"),
        ("barren plateau in VQE", "vqe"),
        ("How is a cross-resonance gate calibrated with an echo?", "cross-resonance"),
        ("calibración de la compuerta de resonancia cruzada", "cross-resonance"),
    ],
)
def test_a_question_finds_its_topic(question: str, paper: str) -> None:
    topic = pick_topic(question)
    assert topic is not None
    assert paper in topic.english


@pytest.mark.parametrize(
    "question",
    ["What is the weather?", "Please recommend a restaurant", "economy of scale", ""],
)
def test_other_questions_find_no_topic(question: str) -> None:
    assert pick_topic(question) is None


def test_language_guess() -> None:
    assert is_spanish("¿Qué limita la coherencia de los transmones?")
    assert is_spanish("dime cuál es el umbral del código")
    assert not is_spanish("What limits T1 in transmons?")
    assert not is_spanish("surface code threshold")


def ask_fake(text: str, **options: Any) -> tuple[AskAnswer, list[str]]:
    engine = FakeAskEngine()
    phases: list[str] = []
    answer = asyncio.run(engine.ask(AskQuestion(text, AskOptions(**options)), phases.append))
    return answer, phases


def test_the_engine_gives_phases_contexts_and_citations_for_one_topic() -> None:
    answer, phases = ask_fake("What limits T1 in transmons?")
    assert phases == ["retrieving", "answering"]
    assert answer.sources == (TRANSMON.doc_id,)
    contexts = answer.contexts
    assert [(c.first_page, c.last_page) for c in contexts] == [
        (c.first_page, c.last_page) for c in answer.citations
    ]
    for context in contexts:
        assert context.doc_id == TRANSMON.doc_id and not context.is_reference
        assert context.text and len(context.text) <= 900
        assert context.name == f"{TRANSMON.short_id} pages {context.first_page}-{context.last_page}"


def test_a_context_is_the_text_of_the_cited_pages() -> None:
    answer, _ = ask_fake("Grover search")
    for context in answer.contexts:
        paper = next(p for p in PAPERS if p.doc_id == context.doc_id)
        pages = "\n\n".join(
            p.markdown for p in paper.pages[context.first_page - 1 : context.last_page]
        )
        assert pages.startswith(context.text)


UNACCENTED = re.compile(
    r"\b(perdidas|dielectric\w*|anaden|termin\w*|termicos|codigo|operacion|logico|fisicos|"
    r"medicion|clasicamente|oraculo|exito|numero|busqueda|parametros|energia|esteril|angulo|"
    r"dias|recalibracion|articulos|demostracion|informacion|teletransportacion|version)\b",
    re.IGNORECASE,
)


def test_the_spanish_text_has_its_accents() -> None:
    """The web app opens in Spanish, so this is the first text a demo shows."""
    texts = [NO_EVIDENCE_ES, *(topic.spanish for topic in TOPICS)]
    for text in texts:
        assert UNACCENTED.findall(text) == []
        assert re.search(r"[áéíóúñ]", text)


def test_the_warning_counts_the_papers_of_the_corpus() -> None:
    warnings = {topic.warning for topic in TOPICS if topic.warning}
    assert warnings == {
        f"1 of {len(PAPERS)} documents of the corpus is not in the index and cannot be used for "
        "this answer; run `docingest index build`"
    }


def test_the_engine_can_say_it_does_not_know_in_both_languages() -> None:
    english, _ = ask_fake("What is the weather?")
    spanish, _ = ask_fake("¿Cuál es el clima de hoy en la ciudad?")
    assert english.answer == NO_EVIDENCE_EN and spanish.answer == NO_EVIDENCE_ES
    assert english.citations == () and english.contexts == ()


def test_without_the_index_there_is_one_phase_and_no_retrieval_numbers() -> None:
    answer, phases = ask_fake("Grover search", use_index=False)
    assert phases == ["answering"]
    assert answer.contexts == () and answer.sources == ()
    assert answer.index_generation is None and answer.timings.retrieve_s == 0.0
    assert answer.citations


@arun
async def test_the_simulated_failures() -> None:
    engine = FakeAskEngine()
    with pytest.raises(UnavailableError) as unavailable:
        await engine.ask(AskQuestion("x simulate-unavailable"), lambda _: None)
    assert unavailable.value.retry_after_s
    with pytest.raises(UpstreamError):
        await engine.ask(AskQuestion("x SIMULATE-UPSTREAM"), lambda _: None)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await engine.ask(AskQuestion("x simulate-crash"), lambda _: None)


def test_the_engine_is_ready_at_once_and_counts_its_questions() -> None:
    engine = FakeAskEngine()
    assert engine.health().state == "ready" and engine.health().reason is None
    ask_fake("x")
    engine.close()
    assert engine.closed


def test_the_fake_engine_answers_with_the_demo_model_that_answers() -> None:
    assert FakeAskEngine().answer_model().name == "fake-model"


@arun
async def test_the_fake_engine_answers_from_passages_by_echoing_them() -> None:
    engine = FakeAskEngine()
    passages = [GoldPassage(TRANSMON.doc_id, "first text"), GoldPassage(TELEPORT.doc_id, "second")]
    answer = await engine.answer_from("What is it?", passages)
    assert answer.answer == "first text\n\nsecond"
    assert [(c.doc_id, c.text, c.first_page, c.last_page) for c in answer.contexts] == [
        (TRANSMON.doc_id, "first text", 1, 1),
        (TELEPORT.doc_id, "second", 1, 1),
    ]
    assert [c.key for c in answer.citations] == [
        f"{TRANSMON.short_id} pages 1-1",
        f"{TELEPORT.short_id} pages 1-1",
    ]
    assert answer.sources == (TRANSMON.doc_id, TELEPORT.doc_id)
    assert answer.index_generation is None and answer.truncated is False
    nothing = await engine.answer_from("What is it?", [])
    assert nothing.answer == "I cannot answer this question due to having no papers."
    assert nothing.contexts == () and nothing.citations == () and nothing.sources == ()


@pytest.mark.parametrize("paper", PAPERS, ids=lambda p: p.key)
def test_the_whole_text_of_a_fake_paper_is_its_pages_joined(paper: FakePaper) -> None:
    text = FakeDocumentReader().text(paper.short_id)
    assert (text.doc_id, text.source_name) == (paper.doc_id, paper.source_name)
    assert text.markdown == "\n\n".join(page.markdown for page in paper.pages)


def test_the_whole_text_keeps_the_circuit_of_the_latex_paper() -> None:
    markdown = FakeDocumentReader().text(TELEPORT.doc_id).markdown
    assert "```latex\n\\begin{quantikz}" in markdown


def test_the_whole_text_of_an_unknown_fake_paper_is_not_found() -> None:
    with pytest.raises(NotFoundError):
        FakeDocumentReader().text("f" * 64)
