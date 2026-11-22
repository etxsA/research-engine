"""What is read aloud: the cleanup of an answer, the language guess and the limits."""

from __future__ import annotations

import contextlib
import time

import pytest

from research_api.domain.errors import InvalidInputError
from research_api.domain.speech import Language
from research_api.domain.synthesis import MAX_RAW_CHARS, guess_language, speakable, utterance

CASES = [
    ("a citation is dropped", "Es corto (40a228002007ca7f pages 3-3).", "es", "Es corto."),
    (
        "two ids in one citation",
        "Holds (40a228002007ca7f pages 3-3; 99aa00bb11cc22dd page 7) here.",
        "en",
        "Holds here.",
    ),
    (
        "a word in parentheses stays",
        "It lasts a decade (deadbeef) so.",
        "en",
        "It lasts a decade (deadbeef) so.",
    ),
    (
        "bold and italic",
        "A **strong** and *soft* and _quiet_ line",
        "en",
        "A strong and soft and quiet line",
    ),
    ("heading", "## The gate\nText", "en", "The gate Text"),
    ("bullets", "- one\n- two\n1. three", "en", "one two three"),
    ("quote", "> said so", "en", "said so"),
    ("inline code", "Use `H` first", "en", "Use H first"),
    ("fenced code is skipped", "Before\n```py\nprint(1)\n```\nAfter", "en", "Before After"),
    ("an open fence is skipped", "Before\n```py\nprint(1)", "en", "Before"),
    ("link keeps its words", "See [the paper](https://x.test/a) now", "en", "See the paper now"),
    ("a bare url is dropped", "Read https://x.test/a/b now", "en", "Read now"),
    ("image keeps alt", "![a circuit](c.png) shown", "en", "a circuit shown"),
    ("table", "| a | b |\n|---|---|\n| 1 | 2 |", "en", "a b 1 2"),
    ("rule", "one\n---\ntwo", "en", "one two"),
    ("inline math", "Sea $x^2 + 1$ un valor", "es", "Sea ecuación un valor"),
    ("inline math en", "Let $x^2 + 1$ be a value", "en", "Let equation be a value"),
    ("display math", "Dado\n$$\\sum_i a_i = 1$$\nentonces", "es", "Dado ecuación entonces"),
    ("bracket math", "We get \\[ E = mc^2 \\] here", "en", "We get equation here"),
    ("paren math", "We get \\( E = mc^2 \\) here", "en", "We get equation here"),
    ("two formulas in a row", "$a$ $b$ end", "en", "equation end"),
    ("money is not math", "It costs $5 and $10 in all", "en", "It costs $5 and $10 in all"),
    ("T1 and T2 are kept", "T1 and T2 with T_1 <= T2", "en", "T1 and T2 with T_1 <= T2"),
    ("T2 less than twice T1 kept", "Why is *T2* below 2*T1* ok", "en", "Why is T2 below 2*T1* ok"),
    ("snake case kept", "the my_var_name stays", "en", "the my_var_name stays"),
    ("spaces before punctuation", "Value ( $x$ ) .", "en", "Value ( equation )."),
    ("html tag", "a <b>bold</b> b", "en", "a bold b"),
]


@pytest.mark.parametrize(("name", "text", "language", "expected"), CASES, ids=[c[0] for c in CASES])
def test_speakable(name: str, text: str, language: Language, expected: str) -> None:
    assert speakable(text, language) == expected


def test_nothing_left_after_the_cleanup_is_an_error() -> None:
    for text in ("", "   \n", "(40a228002007ca7f pages 3-3)", "```\ncode\n```", "---"):
        with pytest.raises(InvalidInputError, match="nothing to read"):
            utterance(text, "en", max_chars=400)


def test_a_formula_alone_is_said_as_a_word() -> None:
    assert utterance("$$x$$", "es", max_chars=400).text == "ecuación"


def test_the_length_is_measured_after_the_cleanup() -> None:
    cited = "Hello (40a228002007ca7f pages 3-3)" + " word" * 3
    assert utterance(cited, "en", max_chars=400).text == "Hello word word word"
    assert utterance("a" * 400, "en", max_chars=400).text == "a" * 400
    with pytest.raises(InvalidInputError, match="400"):
        utterance("a" * 401, "en", max_chars=400)


def test_a_text_past_the_raw_limit_is_an_error_even_when_it_cleans_short() -> None:
    with pytest.raises(InvalidInputError):
        utterance("$x$ " * 6000, "en", max_chars=2000)


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("El tiempo de coherencia es corto", "es"),
        ("La puerta Hadamard", "es"),
        ("Qué es T2", "es"),
        ("canción", "es"),
        ("¿Why?", "es"),
        ("The coherence time is short", "en"),
        ("Hadamard gate", "en"),
        ("", "en"),
        ("T1 T2", "en"),
        ("The gate of the qubit y", "en"),
    ],
)
def test_the_language_guess(text: str, language: Language) -> None:
    assert guess_language(text) == language


def test_the_guess_ignores_formulas_and_decides_the_word_for_them() -> None:
    said = utterance("La energía es $E = mc^2$", None, max_chars=400)
    assert (said.text, said.language) == ("La energía es ecuación", "es")
    said = utterance("The value is $\\alpha$", None, max_chars=400)
    assert (said.text, said.language) == ("The value is equation", "en")


def test_a_given_language_wins_over_the_guess() -> None:
    assert utterance("El tiempo es corto", "en", max_chars=400).language == "en"


def test_a_raw_text_is_capped_by_the_character_limit() -> None:
    with pytest.raises(InvalidInputError):
        utterance("**a " * 500, "en", max_chars=400)


def test_money_before_a_formula_is_not_a_formula() -> None:
    said = utterance("It costs $5, and $x$ more", "en", max_chars=400).text
    assert said == "It costs $5, and equation more"


def test_a_number_in_parentheses_is_not_a_citation() -> None:
    said = utterance("Seen in (12345678 participants) overall", "en", max_chars=400).text
    assert said == "Seen in (12345678 participants) overall"


def test_a_pathological_text_within_the_cap_is_cleaned_fast() -> None:
    started = time.monotonic()
    for unit in ("**a ", "[a ", "![a ", "<a ", "\\(", "\\[", "~~a ", "$a ", "$$a "):
        text = unit * (MAX_RAW_CHARS // len(unit))
        with contextlib.suppress(InvalidInputError):
            utterance(text, None, max_chars=2000)
    assert time.monotonic() - started < 1.0
