"""Pure text functions: text-layer cleanup, quality signals, Markdown (de)serialization."""

from __future__ import annotations

import re

from .models import DocumentManifest

HYPHEN_MARK = "\x02"  # pdfium replaces a line-end hyphen with this and joins the lines

_CID = re.compile(r"\(cid:\d+\)")
_SOFT_HYPHEN = re.compile(rf"(\w+){HYPHEN_MARK}(\w+)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
PAGE_MARKER = re.compile(r"^<!-- page (\d+) \| method=(\w+) -->$", re.MULTILINE)

# A leader is a run of dots (or middle dots, or ellipsis characters), with or without
# any number of spaces, tabs or other blanks between them, that joins an entry of a
# contents page to its page number.
_LEADER_CHARS = ".·∙⋅…"
_LEADER_RUN = re.compile(rf"[{_LEADER_CHARS}](?:[^\S\n]*[{_LEADER_CHARS}])*")
# What may follow it: arabic, roman or lettered ("A-12", "3-14") page numbers. A Roman
# numeral must be well formed and below 400 (front matter), in one case: a word made of
# the same letters ("mild", "civil", "mix", "did") is not a page number.
_ROMAN = "(?=[ivxlc])c{0,3}(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
_PAGE_NUMBER = re.compile(
    rf"\d{{1,4}}|{_ROMAN}|{_ROMAN.upper()}|[A-Z]-\d{{1,4}}|\d{{1,3}}-\d{{1,3}}"
)
_MIN_LEADER = 4  # dots; "..." and ". . ." are ellipses of prose and stay
_MIN_LEADER_WITHOUT_PAGE = 6  # a run at the end of a line with no page number after it


def garbage_ratio(text: str) -> float:
    """Share of characters that indicate a broken text layer."""
    stripped = "".join(text.split())
    if not stripped:
        return 0.0
    bad = stripped.count("�") + sum(
        1
        for c in stripped
        # control / private-use; U+0002 is pdfium's soft-hyphen marker, not garbage
        if (ord(c) < 32 and c != HYPHEN_MARK) or 0xE000 <= ord(c) <= 0xF8FF
    )
    bad += sum(len(m) for m in _CID.findall(text))
    return bad / len(stripped)


def alpha_ratio(text: str) -> float:
    """Share of letters among non-space chars (olmOCR flags < 0.5 as bad text)."""
    stripped = "".join(text.split())
    return sum(c.isalpha() for c in stripped) / len(stripped) if stripped else 0.0


def is_math_char(c: str) -> bool:
    """Whether a character is a math symbol: Greek, arrows, operators, brackets, math alphabets."""
    o = ord(c)
    return (
        0x0370 <= o <= 0x03FF  # Greek
        or 0x2190 <= o <= 0x21FF  # arrows
        or 0x2200 <= o <= 0x22FF  # math operators
        or 0x2300 <= o <= 0x23FF  # misc technical (brackets)
        or 0x27C0 <= o <= 0x27EF  # misc math A (angle brackets)
        or 0x2980 <= o <= 0x2AFF  # misc math B, supplemental operators
        or 0x2100 <= o <= 0x214F  # letterlike (hbar)
        or 0x1D400 <= o <= 0x1D7FF  # math alphanumerics
        or c in "±×÷√∞∑∏∫≈≠≤≥⊗⊕†ˆ"
    )


def equals_count(text: str) -> int:
    """Number of "=" signs in a text layer."""
    return text.count("=")


def math_density(text: str) -> float:
    """Share of math-symbol characters among the non-space characters of a text layer.

    pdfium's soft-hyphen mark is not a character of the page and is left out.
    """
    chars = [c for c in text.replace(HYPHEN_MARK, "") if not c.isspace()]
    return sum(is_math_char(c) for c in chars) / len(chars) if chars else 0.0


def text_vocabulary(*texts: str) -> set[str]:
    return {w.lower() for t in texts for w in re.findall(r"\w+", t.replace(HYPHEN_MARK, " "))}


def clean_text_layer(text: str, vocab: set[str] | None = None) -> str:
    """Normalize a PDF text layer.

    pdfium turns a line-end hyphen into U+0002 and joins the lines, for both real
    hyphenation ("transduc-tion") and compounds ("sequence-aligned"). Join the two
    halves only when the joined word occurs elsewhere in the document; otherwise
    keep the hyphen, which never loses information.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    vocab = text_vocabulary(text) if vocab is None else vocab

    def _join(m: re.Match[str]) -> str:
        a, b = m.group(1), m.group(2)
        return a + b if (a + b).lower() in vocab else f"{a}-{b}"

    text = _SOFT_HYPHEN.sub(_join, text).replace(HYPHEN_MARK, "-")
    # Remaining "-\n" breaks are numbers / identifiers (2019-2020, Qwen-2.5-VL): keep the hyphen.
    text = re.sub(r"(?<=\w)-\n(?=\w)", "-", text)
    text = _CONTROL.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _leader_weight(run: str) -> int:
    """Dots in a run: an ellipsis character stands for three."""
    return sum(3 if c == "…" else 1 for c in run if not c.isspace())


def _collapse_leader_line(line: str) -> str:
    for run in reversed(list(_LEADER_RUN.finditer(line))):
        weight = _leader_weight(run.group())
        if weight < _MIN_LEADER:
            continue
        page = line[run.end() :].strip()
        if page and not _PAGE_NUMBER.fullmatch(page):
            return line  # the run is followed by text: not a contents line
        head = line[: run.start()].rstrip()
        if not page and (weight < _MIN_LEADER_WITHOUT_PAGE or not head.strip()):
            return line  # prose that trails off, or a line of nothing but dots
        return f"{head} {page}" if head.strip() and page else head or page
    return line


def collapse_dot_leaders(text: str) -> str:
    """Contents lines without their dot leaders: ``Title . . . . . 23`` becomes ``Title 23``.

    A vision model writes a contents page with leaders on one run and without on the next,
    and a text layer carries them as typeset. Only a line that ends with a run of at least
    four dots (``. . . .`` or ``....``) followed by a page number is changed, or one that
    ends with a run of six or more. Ellipses of prose, section numbers (``1.2.3``) and
    dots inside a line stay. The blanks between the dots do not matter. Idempotent.
    """
    if not any(c in text for c in _LEADER_CHARS):
        return text
    return "\n".join(_collapse_leader_line(line) for line in text.split("\n"))


def render_markdown(manifest: DocumentManifest, texts: list[str]) -> str:
    parts = [f"# {manifest.title}\n" if manifest.title else ""]
    for rec, text in zip(manifest.pages, texts, strict=True):
        parts.append(f"<!-- page {rec.index + 1} | method={rec.method.value} -->\n{text}\n")
    return "\n".join(parts).strip() + "\n"


def split_pages(markdown: str) -> dict[int, str]:
    """Inverse of :func:`render_markdown`: {1-based page number: page text}."""
    marks = list(PAGE_MARKER.finditer(markdown))
    pages = {}
    for m, nxt in zip(marks, [*marks[1:], None], strict=True):
        end = nxt.start() if nxt else len(markdown)
        pages[int(m.group(1))] = markdown[m.end() : end].strip()
    return pages
