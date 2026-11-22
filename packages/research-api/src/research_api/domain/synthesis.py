"""Text to speech: what part of an answer is worth reading aloud, in which language, and the
limits a request keeps.

The browser sends a sentence of an answer as it was written: markdown, a citation of the form
``(40a228002007ca7f pages 3-3)``, a formula in LaTeX. Only the words are spoken. Nothing here is
kept: the text and the audio live only for the length of one request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from .errors import InvalidInputError
from .speech import DEFAULT_PER_MEMBER_OPEN, Language

DEFAULT_MAX_CHARS: Final = 400
MAX_CHARS_LIMIT: Final = 2000
DEFAULT_TIMEOUT_S: Final = 30.0
DEFAULT_VOICE: Final = "default"
RAW_PER_CHAR: Final = 4
MAX_RAW_CHARS: Final = MAX_CHARS_LIMIT * RAW_PER_CHAR

EQUATION: Final[dict[Language, str]] = {"es": "ecuación", "en": "equation"}
NOTHING_TO_SAY: Final = "There is nothing to read aloud in this text."

_HOLE = "\x00"

# A pair of marks that has no partner must not be searched to the end of the text from every
# opener, which is quadratic: a span is at most this long.
SPAN = 400
MATH_SPAN = 1000

# A citation is a parenthesis that opens with a hexadecimal document id (at least one digit, so a
# word such as "decade" is not one) and says the page: "(40a228002007ca7f pages 3-3)".
_CITATION = re.compile(r"\(\s*(?=[0-9a-f]*\d)[0-9a-f]{8,}\s+pages?\b[^()]*\)", re.IGNORECASE)
_MATH = re.compile(
    rf"\$\$.{{1,{MATH_SPAN}}}?\$\$"
    rf"|\\\[.{{1,{MATH_SPAN}}}?\\\]"
    rf"|\\\(.{{1,{MATH_SPAN}}}?\\\)"
    rf"|(?<!\\)\$(?!\d+(?:[.,]\d+)*(?:[\s,.;:!?)]|$))(?=[^\s$])"
    rf"(?:[^$\n]|\\\$){{1,{SPAN}}}?(?<=[^\s$])\$(?!\d)",
    re.DOTALL,
)
_FENCE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$|^[ \t]*(```|~~~).*\Z", re.DOTALL | re.M)
_IMAGE = re.compile(rf"!\[([^\]]{{0,{SPAN}}})\]\([^)]{{0,{SPAN}}}\)")
_LINK = re.compile(rf"\[([^\]]{{1,{SPAN}}})\]\([^)]{{0,{SPAN}}}\)")
_AUTOLINK = re.compile(r"<(?:https?://|mailto:)[^>\s]+>")
_URL = re.compile(r"https?://\S+")
_TAG = re.compile(rf"</?[A-Za-z][^>\n]{{0,{SPAN}}}>")
_INLINE_CODE = re.compile(r"`+([^`\n]*)`+")
_BOLD = re.compile(rf"(\*\*|__)(?=\S)(.{{1,{SPAN}}}?)(?<=\S)\1", re.DOTALL)
_ITALIC_STAR = re.compile(rf"(?<![\w*])\*(?=[^\s*])([^*\n]{{1,{SPAN}}}?)(?<=[^\s*])\*(?![\w*])")
_ITALIC_UNDERSCORE = re.compile(rf"(?<![\w_])_(?=[^\s_])([^_\n]{{1,{SPAN}}}?)(?<=[^\s_])_(?![\w_])")
_STRIKE = re.compile(rf"~~(?=\S)(.{{1,{SPAN}}}?)(?<=\S)~~")
_LINE_MARK = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+|\d{1,3}[.)][ \t]+)", re.M)
_RULE = re.compile(r"^[ \t]*(?:[-*_=][ \t]*){3,}$", re.M)
_TABLE_BAR = re.compile(r"^[ \t]*\|?(?:[ \t]*:?-{2,}:?[ \t]*\|)+[ \t]*:?-*:?[ \t]*$", re.M)
_SPACES = re.compile(r"\s+")
_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)

_SPANISH_MARKS = frozenset("áéíóúüñ¿¡ÁÉÍÓÚÜÑ")
_SPANISH = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "del",
        "que",
        "y",
        "en",
        "un",
        "una",
        "por",
        "para",
        "es",
        "se",
        "su",
        "sus",
        "al",
        "lo",
        "como",
        "más",
        "pero",
        "este",
        "esta",
        "estos",
        "estas",
        "entre",
        "cuando",
        "muy",
        "sobre",
        "también",
        "hasta",
        "hay",
        "donde",
        "quien",
        "desde",
        "todo",
        "nos",
        "durante",
        "son",
        "ser",
        "fue",
        "qué",
        "cómo",
        "porque",
        "cada",
        "otro",
        "otra",
    }
)
_ENGLISH = frozenset(
    {
        "the",
        "and",
        "of",
        "is",
        "to",
        "in",
        "that",
        "it",
        "for",
        "with",
        "as",
        "was",
        "on",
        "are",
        "this",
        "by",
        "be",
        "at",
        "or",
        "from",
        "an",
        "which",
        "not",
        "have",
        "has",
        "were",
        "can",
        "its",
        "their",
        "than",
        "then",
        "when",
        "what",
        "how",
        "because",
        "each",
        "other",
    }
)


@dataclass(frozen=True)
class SynthesisLimits:
    """What one request and the desk keep to. ``per_member_open`` counts syntheses waiting or
    running."""

    max_chars: int = DEFAULT_MAX_CHARS
    timeout_s: float = DEFAULT_TIMEOUT_S
    per_member_open: int = DEFAULT_PER_MEMBER_OPEN


@dataclass(frozen=True)
class Voices:
    """The voice of the speech server for each language."""

    es: str = DEFAULT_VOICE
    en: str = DEFAULT_VOICE

    def of(self, language: Language) -> str:
        return self.es if language == "es" else self.en


@dataclass(frozen=True)
class Utterance:
    """What is said and in which language."""

    text: str
    language: Language


def guess_language(text: str) -> Language:
    """``es`` when the text has a Spanish letter or sign or more Spanish stop words than English
    ones, else ``en``."""
    if any(c in _SPANISH_MARKS for c in text):
        return "es"
    words = [w.lower() for w in _WORDS.findall(text)]
    spanish = sum(w in _SPANISH for w in words)
    english = sum(w in _ENGLISH for w in words)
    return "es" if spanish > english else "en"


def speakable(text: str, language: Language) -> str:
    """The words of ``text`` without its citations and markdown, a formula said as "ecuación" or
    "equation" by ``language``, and the spaces squeezed. May be empty."""
    return _clean(text, EQUATION[language])


def utterance(text: str, language: Language | None, *, max_chars: int) -> Utterance:
    """What to say for ``text``: ``language`` or a guess from its words (formulas do not count),
    cleaned. ``InvalidInputError`` when nothing is left or more than ``max_chars`` remain."""
    raw_cap = min(MAX_RAW_CHARS, max_chars * RAW_PER_CHAR)
    if len(text) > raw_cap:
        raise InvalidInputError(f"The text is over {raw_cap} characters; send less.")
    said = language or guess_language(_clean(text, ""))
    spoken = speakable(text, said)
    if not spoken:
        raise InvalidInputError(NOTHING_TO_SAY)
    if len(spoken) > max_chars:
        raise InvalidInputError(f"The text to read is over {max_chars} characters; send less.")
    return Utterance(spoken, said)


def _clean(text: str, equation: str) -> str:
    text = text.replace(_HOLE, " ")
    text = _FENCE.sub(" ", text)
    text = _MATH.sub(f" {_HOLE} ", text)
    text = _CITATION.sub(" ", text)
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _AUTOLINK.sub(" ", text)
    text = _URL.sub(" ", text)
    text = _TAG.sub(" ", text)
    text = _TABLE_BAR.sub(" ", text)
    text = _RULE.sub(" ", text)
    text = _LINE_MARK.sub("", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _BOLD.sub(r"\2", text)
    text = _STRIKE.sub(r"\1", text)
    text = _ITALIC_STAR.sub(r"\1", text)
    text = _ITALIC_UNDERSCORE.sub(r"\1", text)
    text = text.replace("|", " ")
    text = _SPACES.sub(" ", text).strip()
    text = re.sub(f"(?:{_HOLE} ?)+", equation + " " if equation else " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return _SPACES.sub(" ", text).strip()
