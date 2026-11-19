"""A safe parser for the angles in gate labels. Standard library only; nothing is ever evaluated
as code.

The grammar is the arithmetic authors write in LaTeX:

    expression := term (("+" | "-") term)*          a leading "+" or "-" is a unary sign
    term       := factor (("*" | "/" | implicit) factor)*
    factor     := ("+" | "-") factor | atom ("^" exponent)?
    atom       := number | \\pi | symbol | "(" expression ")" | "{" expression "}"
                  | \\frac{expression}{expression} | \\sqrt{expression}

Implicit multiplication is a factor that follows another one with no operator (``2\\pi``,
``2\\gamma``, ``\\pi/4``). ``\\cdot`` and ``\\times`` are ``*``, ``\\div`` is ``/``, ``\\left`` and
``\\right`` before a parenthesis and the spacing commands (``\\,`` ``\\;`` ``\\!`` ``\\ ``
``~``) are ignored. A symbol is a Greek command (``\\theta``) or one ASCII letter, with an optional
subscript (``\\alpha_1``, ``\\alpha_{12}``); it becomes a free parameter. ``e``, ``E``, ``i`` and
``j`` are refused as symbols because they may mean a constant. ``symbol_name`` applies the same
rule to a binding key (``values``, ``--set``): a key is one symbol, with an optional backslash
and outer braces, or it is not a name at all (white space around it is ignored, a line break
inside it is not); ``binding_name`` gives the error message the export and the simulator share
for such a key. ``parse_value`` is the one rule for a binding value given as text: the angle
grammar, except that ``pi`` standing alone may drop its backslash (``pi/4`` is ``\\pi/4``), as
a caller types it on a command line; the export and the simulator (``run``, ``check``, both
``--set`` options) all read values with it.

What is refused, with a reason, rather than guessed: anything left over after the expression;
``1/2\\pi`` (a division followed by an implicit product could be read two ways); two numbers
in a row (``2 3``; also ``2{5}``, ``{2}5`` and ``{2}{5}``, which LaTeX shows as 25: braces
group but do not separate); a sign in braces right after a factor (``2{-5}`` is shown as 2-5); a
number with an exponent (``1e3``) or a comma; ``\\frac`` arguments
without braces (``\\frac12``); an exponent that is not an integer constant of at most 64 in
absolute value, or an unbraced exponent of more than one digit (``2^10`` is ``2^{1}0`` in
LaTeX); a superscript in parentheses (``\\theta^{(2)}``, the usual way to number the layer of
a parameter, is an index and not a power; any parenthesis in a braced superscript is refused,
``\\theta^{1(2)}`` and ``2^{1-(1-3)}`` too); a digit right after an unbraced subscript
(``\\theta_12`` is ``\\theta_1`` and a 2 in LaTeX); a number starting with a point right after
a factor, also inside braces (``\\pi.5``, ``\\pi{.5}``); a brace group that holds a sum, a
difference or a division (``2{\\theta+1}`` is shown as 2\\theta+1, and ``{\\theta/2}x`` as
\\theta/2x: braces are not drawn); a brace group of more than one factor as the base of a power
(``{2\\theta}^2`` is shown as 2\\theta^2, ``{-x}^2`` as -x^2; ``{\\theta}^2`` and ``2{x}^2``
stay); a number right before a fraction of two numbers (``2\\frac{1}{2}`` is two and a half in
LaTeX, a mixed number, also when braces, parentheses or signs wrap the numbers;
``2\\frac{\\pi}{3}`` is still a product); two or more ASCII
letters in a row (``cos``, ``xy``: a function or a product); division by zero; any result that
is not finite; text longer than ``MAX_CHARS``; nesting (parentheses, braces, fractions, repeated
signs) deeper than ``MAX_DEPTH``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from quantum_sim.domain import AngleError, short_text

MAX_CHARS = 500  # enough for any float written out in full (5e-324 is 326 characters)
MAX_DEPTH = 40  # nesting of parentheses, braces, fractions and signs
MAX_NUMBER_CHARS = 400
MAX_POWER = 64

_GREEK_WORDS = (
    "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu "
    "xi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega "
    "Gamma Delta Theta Lambda Xi Sigma Upsilon Phi Psi Omega"
)
GREEK = frozenset(_GREEK_WORDS.split())
_IGNORED = (r"\,", r"\!", r"\;", r"\:", r"\ ", r"\quad", r"\qquad", "~")
_OPERATOR_COMMANDS = {"cdot": "*", "times": "*", "div": "/"}
_FRACTIONS = frozenset({"frac", "dfrac", "tfrac"})
_UNICODE = {"−": "-", "×": "*", "·": "*", "÷": "/", "π": r"\pi"}
_UNICODE_CHAR = re.compile("[" + "".join(_UNICODE) + "]")
_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]*)?|\.[0-9]+")
_COMMAND = re.compile(r"\\([A-Za-z]+)")
# The subscript of a symbol: one letter or digit, or up to 8 in braces. Binding names use it too.
_SUBSCRIPT_TEXT = r"_(?:([A-Za-z0-9])|\{([A-Za-z0-9]{1,8})\})"
_SUBSCRIPT = re.compile(_SUBSCRIPT_TEXT)
_CONSTANT_LETTERS = frozenset("eEij")
_LETTERS = re.compile(r"[A-Za-z]+")
_BINDING_KEY = re.compile(rf"(\\?)([A-Za-z]+)(?:{_SUBSCRIPT_TEXT})?")
# ``pi`` that is not part of a command or a longer word: a binding value may spell ``\pi`` so.
_BARE_PI = re.compile(r"(?<![\\A-Za-z])pi(?![A-Za-z])")

_POINT_AFTER_FACTOR = (
    "a number starting with '.' right after a factor: \\pi.5 is shown as pi.5, not as pi times "
    "0.5; write the product out (\\pi * 0.5)"
)

Node = tuple  # ("num", x) ("pi",) ("sym", name) ("neg", a) ("add"|"sub"|"mul"|"div", a, b) ...


@dataclass(frozen=True)
class Angle:
    """A parsed angle: a number, or an expression with free symbols.

    ``evaluate(values)`` gives the angle in radians for a value of every symbol; ``value`` is
    the number when there are no symbols. Equal expressions parsed from different spellings
    compare equal when they have the same tree.
    """

    source: str = field(compare=False)
    symbols: tuple[str, ...]
    tree: Node = field(repr=False)

    @property
    def is_numeric(self) -> bool:
        return not self.symbols

    @property
    def value(self) -> float:
        """The angle in radians; ``AngleError`` if it has symbols."""
        if self.symbols:
            raise AngleError(f"the angle {self.source!r} has free symbols: {list(self.symbols)}")
        return _evaluate(self.tree, {})

    def evaluate(self, values: Mapping[str, float]) -> float:
        """The angle in radians with ``values`` for its symbols (extra values are ignored)."""
        return _evaluate(self.tree, values)

    def negated(self) -> Angle:
        """The angle with the opposite sign (the inverse of a rotation or a phase gate)."""
        return Angle(f"-({self.source})", self.symbols, ("neg", self.tree))

    @classmethod
    def of(cls, number: float) -> Angle:
        """An angle that is the finite float ``number`` exactly."""
        if not math.isfinite(number):
            raise AngleError(f"{number!r} is not a finite number")
        return cls(format_number(number), (), ("num", float(number)))


def format_number(number: float) -> str:
    """``number`` as plain decimal text (no exponent) that ``parse_angle`` reads back exactly.

    Python's ``repr`` of a float round-trips; this only moves the exponent into digits, because
    the grammar has no exponent notation. The text is at most 326 characters.
    """
    text = repr(float(number))
    if "e" not in text and "E" not in text:
        return text
    mantissa, exponent = text.lower().split("e")
    sign = "-" if mantissa.startswith("-") else ""
    digits = mantissa.lstrip("-").replace(".", "")
    point = (mantissa.lstrip("-").find(".") if "." in mantissa else len(digits)) + int(exponent)
    if point <= 0:
        body = "0." + "0" * -point + digits
    else:  # repr uses an exponent only below 1e-4 and from 1e16: the point is past the digits
        body = digits + "0" * (point - len(digits)) + ".0"
    return sign + body


def symbol_name(key: str) -> str | None:
    """The name of the free parameter that ``key`` spells, or ``None`` when ``key`` is not one
    symbol of the grammar. This is the one rule for binding names (``values`` and ``--set``).

    ``key`` is a symbol as an angle writes it, with an optional backslash before a Greek word,
    an optional subscript, and any number of brace pairs around it; the name is the one the
    parser gives the symbol: ``\\theta``, ``{\\theta}`` and ``theta`` are ``theta``, and
    ``\\theta_{1}`` is ``theta_1``. ``pi``, ``e``, ``E``, ``i`` and ``j`` are not names (they
    may mean a constant), nor is a word that is neither Greek nor one ASCII letter (``thetta``)
    or anything with more in it (``the{ta}``, ``\\\\theta``, ``$\\theta$``, ``x y``), nor a key
    of more than ``MAX_CHARS`` characters.
    """
    if len(key) > MAX_CHARS:  # no symbol is this long; the brace loop stays cheap
        return None
    text = key.strip()
    while len(text) >= 2 and text[0] == "{" and text[-1] == "}":
        text = text[1:-1].strip()
    match = _BINDING_KEY.fullmatch(text)
    if match is None:
        return None
    backslash, word, letter, braced = match.groups()
    if word not in GREEK and (backslash or len(word) > 1 or word in _CONSTANT_LETTERS):
        return None
    subscript = letter or braced
    return f"{word}_{subscript}" if subscript else word


def binding_name(key: str) -> str:
    """:func:`symbol_name`, or an ``AngleError`` whose message the export and the simulator
    both give for a key that is not one symbol."""
    name = symbol_name(key)
    if name is not None:
        return name
    if not key.strip():
        raise AngleError("a parameter binding has an empty name")
    raise AngleError(
        f"{short_text(key)!r} is not a parameter name: write one symbol, such as theta, "
        "\\theta or \\theta_{1}"
    )


# --- Evaluation --------------------------------------------------------------------------------


def _finite(x: float, what: str) -> float:
    if not math.isfinite(x):
        raise AngleError(f"{what} is not a finite number")
    return x


def _leaf(node: Node, values: Mapping[str, float]) -> float:
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "pi":
        return math.pi
    name = node[1]  # kind == "sym"
    if name not in values:
        raise AngleError(f"no value for the symbol {name!r}")
    value = values[name]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AngleError(f"the value of {name!r} is not a real number")
    try:
        return _finite(float(value), f"the value of {name!r}")
    except OverflowError:  # an integer too large for a float
        raise AngleError(f"the value of {name!r} is too large for a float") from None


def _unary(node: Node, values: Mapping[str, float]) -> float:
    kind, inner = node[0], _evaluate(node[1], values)
    if kind == "neg":
        return -inner
    if kind == "sqrt":
        if inner < 0:
            raise AngleError("the square root of a negative number")
        return math.sqrt(inner)
    try:  # kind == "pow"
        return _finite(inner ** node[2], "a power")
    except (OverflowError, ZeroDivisionError):
        raise AngleError("a power overflows or divides by zero") from None


def _binary(node: Node, values: Mapping[str, float]) -> float:
    kind = node[0]
    left, right = _evaluate(node[1], values), _evaluate(node[2], values)
    if kind == "add":
        return _finite(left + right, "a sum")
    if kind == "sub":
        return _finite(left - right, "a difference")
    if kind == "mul":
        return _finite(left * right, "a product")
    if right == 0:  # kind == "div"
        raise AngleError("division by zero")
    return _finite(left / right, "a quotient")


def _evaluate(node: Node, values: Mapping[str, float]) -> float:
    kind = node[0]
    if kind in ("num", "pi", "sym"):
        return _leaf(node, values)
    if kind in ("neg", "sqrt", "pow"):
        return _unary(node, values)
    return _binary(node, values)


# --- Tokens ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Token:
    kind: str  # num, pi, sym, op, ( ) { } frac sqrt ^ end
    text: str = ""


@dataclass(frozen=True)
class _Text:
    """The text the tokenizer reads (``read``) and, for each of its characters, the position in
    the caller's text (``given``), so an error names a position in the caller's text after a
    rewrite (white space and ``$`` stripped, ``pi`` or ``π`` spelled ``\\pi``)."""

    read: str
    given: str
    origin: tuple[int, ...]

    def at(self, i: int) -> int:
        return self.origin[i] if i < len(self.origin) else len(self.given)

    def rewrite(self, pattern: re.Pattern[str], spell: Callable[[str], str]) -> _Text:
        """Replace each match of ``pattern`` by ``spell(match)``; the new characters take the
        position of the first character they replace."""
        parts: list[str] = []
        origin: list[int] = []
        last = 0
        for match in pattern.finditer(self.read):
            new = spell(match.group())
            parts += [self.read[last : match.start()], new]
            origin += [*self.origin[last : match.start()], *[self.origin[match.start()]] * len(new)]
            last = match.end()
        parts.append(self.read[last:])
        origin += self.origin[last:]
        return _Text("".join(parts), self.given, tuple(origin))


def _tokenize(source: _Text) -> list[_Token]:
    source = source.rewrite(_UNICODE_CHAR, _UNICODE.__getitem__)
    text = source.read
    tokens: list[_Token] = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        ignored = next((s for s in _IGNORED if text.startswith(s, i)), None)
        i = i + len(ignored) if ignored is not None else _scan(source, i, tokens)
    tokens.append(_Token("end"))
    return tokens


def _scan(source: _Text, i: int, tokens: list[_Token]) -> int:
    """Read the token at ``i``; return the position after it. Positions in errors are the
    caller's (``source.at``)."""
    text = source.read
    char = text[i]
    if char in "+-*/":
        tokens.append(_Token("op", char))
        return i + 1
    if char in "(){}^":
        tokens.append(_Token(char))
        return i + 1
    if char in "0123456789.":
        match = _NUMBER.match(text, i)
        if match is None:
            raise AngleError(f"a stray '.' at position {source.at(i)}")
        if char == "." and tokens and tokens[-1].kind in ("pi", "sym", ")", "}"):
            raise AngleError(f"{_POINT_AFTER_FACTOR} (position {source.at(i)})")
        if len(match.group()) > MAX_NUMBER_CHARS:
            raise AngleError(f"a number of more than {MAX_NUMBER_CHARS} characters")
        tokens.append(_Token("num", match.group()))
        return match.end()
    if char == "\\":
        return _command(source, i, tokens)
    if char.isascii() and char.isalpha():
        run = _LETTERS.match(text, i)
        if run is not None and len(run.group()) > 1:
            raise AngleError(
                f"the letters {run.group()!r} in a row may be a function or a product: "
                "separate the symbols with a space or write the function out"
            )
        if char in _CONSTANT_LETTERS:
            raise AngleError(f"the letter {char!r} may be a constant, not a free parameter")
        return _symbol(text, i + 1, char, tokens)
    raise AngleError(f"unsupported character {char!r} at position {source.at(i)}")


def _command(source: _Text, i: int, tokens: list[_Token]) -> int:
    text = source.read
    match = _COMMAND.match(text, i)
    if match is None:
        at = source.at(i)
        raise AngleError(f"unsupported command at position {at}: {source.given[at : at + 6]!r}")
    name, end = match.group(1), match.end()
    if name == "pi":
        tokens.append(_Token("pi"))
        return end
    if name in ("left", "right"):
        if end < len(text) and text[end] in "()":
            tokens.append(_Token(text[end]))
            return end + 1
        raise AngleError(f"\\{name} must be followed by a parenthesis")
    if name in _OPERATOR_COMMANDS:
        tokens.append(_Token("op", _OPERATOR_COMMANDS[name]))
        return end
    if name in _FRACTIONS:
        tokens.append(_Token("frac"))
        return end
    if name == "sqrt":
        tokens.append(_Token("sqrt"))
        return end
    if name in GREEK:
        return _symbol(text, end, name, tokens)
    raise AngleError(f"unknown command \\{name}")


def _symbol(text: str, i: int, name: str, tokens: list[_Token]) -> int:
    sub = _SUBSCRIPT.match(text, i)
    if sub is not None:
        if sub.group(1) and text[sub.end() : sub.end() + 1] in tuple("0123456789."):
            raise AngleError(
                f"a digit right after the subscript {sub.group()!r}: in LaTeX \\theta_12 is "
                "\\theta_1 followed by 2; write the subscript in braces (\\theta_{12})"
            )
        name = f"{name}_{sub.group(1) or sub.group(2)}"
        i = sub.end()
    elif i < len(text) and text[i] == "_":
        raise AngleError("a subscript must be one letter or digit, or up to 8 in braces")
    tokens.append(_Token("sym", name))
    return i


# --- Parser ------------------------------------------------------------------------------------

_STARTS_A_FACTOR = frozenset({"num", "pi", "sym", "(", "{", "frac", "sqrt"})
_AFTER_SYMBOL = {
    "(": (
        "a symbol right before a parenthesis may be a function, \\phi(x), or a product: write "
        "the product out (\\phi \\cdot (x))"
    ),
    "num": (
        "a number right after a symbol may be a name (\\theta1) or a product: write the "
        "subscript (\\theta_{1}) or the product out (2 \\theta)"
    ),
}


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self.tokens = tokens
        self.index = 0
        self.depth = 0
        self.symbols: set[str] = set()
        self.edge = ""  # "digit" when the factor parsed last ends in a digit of a number
        self.symbol_edge = False  # whether the factor parsed last is a symbol, with its power

    @property
    def token(self) -> _Token:
        return self.tokens[self.index]

    def advance(self) -> _Token:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def expect(self, kind: str) -> None:
        if self.token.kind != kind:
            raise AngleError(f"expected {kind!r}, found {self._describe()}")
        self.index += 1

    def _describe(self) -> str:
        token = self.token
        return "the end of the text" if token.kind == "end" else repr(token.text or token.kind)

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise AngleError(f"nested more than {MAX_DEPTH} levels deep")

    def leave(self) -> None:
        self.depth -= 1

    def expression(self) -> Node:
        self.enter()
        node = self.term()
        while self.token.kind == "op" and self.token.text in "+-":
            kind = "add" if self.advance().text == "+" else "sub"
            node = (kind, node, self.term())
        self.leave()
        return node

    def term(self) -> Node:
        node = self.factor()
        after_division = False
        while True:
            token = self.token
            if token.kind == "op" and token.text in "*/":
                self.advance()
                right = self.factor()
                after_division = token.text == "/"
                node = ("mul" if token.text == "*" else "div", node, right)
            elif token.kind in _STARTS_A_FACTOR:
                if after_division:
                    raise AngleError(
                        "ambiguous: a division followed by a factor with no operator "
                        "(write \\frac or parentheses)"
                    )
                self._check_run_in()
                node = ("mul", node, self.factor())
            else:
                return node

    def _check_run_in(self) -> None:
        """Refuse a factor that LaTeX sets right against the one before it as one number.

        Braces group but do not separate: ``2{5}`` and ``{2}5`` are shown as 25, ``2{-5}`` as
        2-5 and ``\\pi{.5}`` as pi.5, so the braces are looked through.
        """
        index = self.index
        while self.tokens[index].kind == "{":
            index += 1
        braced, first = index > self.index, self.tokens[index]
        if braced and first.kind == "op" and first.text in "+-":
            raise AngleError(
                "a sign inside braces right after a factor: "
                "LaTeX shows 2{-5} as 2-5, not as 2 times -5; write the product out (2 * (-5))"
            )
        if first.kind == "frac" and self.edge == "digit" and self._is_number_fraction(index):
            raise AngleError(
                "a number right before a fraction of two numbers: LaTeX shows 2\\frac{1}{2} as "
                "two and a half, not as a product; write the product out (2 * \\frac{1}{2})"
            )
        if self.symbol_edge and first.kind in ("(", "num"):
            raise AngleError(_AFTER_SYMBOL[first.kind])
        if first.kind != "num":
            return
        if braced and first.text.startswith("."):
            raise AngleError(_POINT_AFTER_FACTOR)
        if self.edge == "digit":
            raise AngleError(
                "two numbers in a row" + (" (braces do not join them)" if braced else "")
            )

    def _is_number_fraction(self, index: int) -> bool:
        """Is the token at ``index`` a ``\\frac`` whose two arguments are plain numbers
        (``\\frac{1}{2}``), however many signs, braces and parentheses wrap each number?"""
        position = index + 1
        for _ in range(2):
            if self.tokens[position].kind != "{":
                return False
            end = self._number_end(position + 1)
            if end is None or self.tokens[end].kind != "}":
                return False
            position = end + 1
        return True

    def _number_end(self, position: int) -> int | None:
        """Where a number wrapped in signs, braces and parentheses ends (``-(1)``, ``{{1}}``), or
        ``None`` when the tokens from ``position`` are anything else."""
        closers: list[str] = []
        while True:
            token = self.tokens[position]
            if token.kind == "op" and token.text in "+-":
                position += 1
            elif token.kind in ("(", "{"):
                closers.append(")" if token.kind == "(" else "}")
                position += 1
            else:
                break
        if self.tokens[position].kind != "num":
            return None
        position += 1
        while closers:
            if self.tokens[position].kind != closers.pop():
                return None
            position += 1
        return position

    def factor(self) -> Node:
        token = self.token
        if token.kind != "op" or token.text not in "+-":
            return self.power()
        self.advance()
        self.enter()
        inner = self.factor()
        self.leave()
        return ("neg", inner) if token.text == "-" else inner

    def power(self) -> Node:
        start = self.index
        base = self.atom()
        symbol_base = self.symbol_edge  # the atom's own token: (x) is a parenthesis, not a symbol
        if self.token.kind != "^":
            return base
        if self.tokens[start].kind == "{" and not self._one_factor(start, self.index):
            raise AngleError(
                "a brace group of more than one factor as the base of a power: braces are not "
                "drawn, so LaTeX shows {2\\theta}^2 as 2\\theta^2 and {-x}^2 as -x^2; write "
                "parentheses ((2\\theta)^2)"
            )
        self.advance()
        unbraced = self.token.kind != "{"
        exponent = self.exponent()
        if self.token.kind == "^":
            raise AngleError("a double exponent needs braces")
        self.edge = "digit" if unbraced else "other"
        self.symbol_edge = symbol_base
        return ("pow", base, exponent)

    def _group_end(self, index: int) -> int:
        """The position after the group that opens at ``index``, with ``(`` and ``{`` nested
        together; the end of the tokens when it never closes."""
        depth = 0
        while self.tokens[index].kind != "end":
            kind = self.tokens[index].kind
            depth += (kind in ("(", "{")) - (kind in (")", "}"))
            index += 1
            if depth == 0:
                return index
        return index

    def _one_factor(self, start: int, stop: int) -> bool:
        """Do the tokens from ``start`` up to ``stop`` make one atom: a number, ``\\pi`` or a
        symbol, a parenthesis, ``\\frac`` or ``\\sqrt`` with its arguments, or one of these in
        braces? An atom with a power (``\\theta^2``) is not."""
        kind = self.tokens[start].kind
        if stop - start == 1:
            return kind in ("num", "pi", "sym")
        if kind in ("(", "{"):
            if self._group_end(start) != stop:
                return False
            return kind == "(" or self._one_factor(start + 1, stop - 1)
        if kind not in ("frac", "sqrt"):
            return False
        position = start + 1  # the braces of the arguments were checked when they were parsed
        for _ in range(2 if kind == "frac" else 1):
            position = self._group_end(position)
        return position == stop

    def _check_brace_group(self, start: int) -> None:
        """Refuse the group of braces at ``start`` when it holds a sum, a difference or a
        division at its top level: braces are not drawn, so LaTeX shows 2{\\theta+1} as
        2\\theta+1 and a/{b+c} as a/b+c. A sign that opens the group or follows an operator is a
        sign, not a sum."""
        end = self._group_end(start) - 1
        depth = 0
        for index in range(start + 1, end):
            token = self.tokens[index]
            depth += (token.kind in ("(", "{")) - (token.kind in (")", "}"))
            if depth or token.kind != "op" or token.text == "*":
                continue
            before = self.tokens[index - 1]
            if token.text == "/" or (before.kind != "op" and index > start + 1):
                raise AngleError(
                    "a brace group that holds a sum, a difference or a division: braces are "
                    "not drawn, so LaTeX shows 2{\\theta+1} as 2\\theta+1, not as 2 times a "
                    "sum; write parentheses"
                )

    def _holds_a_parenthesis(self) -> bool:
        """Whether the braces at the current position hold a parenthesis anywhere, at any depth:
        ``{(0)}``, ``{{(1)}}``, ``{1(2)}``, ``{\\frac{(2)}{1}}``."""
        position, depth = self.index, 0
        while self.tokens[position].kind != "end":
            kind = self.tokens[position].kind
            depth += (kind == "{") - (kind == "}")
            if kind == "(":
                return True
            if depth == 0:
                return False
            position += 1
        return False

    def exponent(self) -> int:
        if self.token.kind == "{":
            if self._holds_a_parenthesis():
                raise AngleError(
                    "a superscript in parentheses is an index, not a power (a parenthesis "
                    "anywhere in a braced superscript is refused): write \\theta_{2} for a "
                    "parameter, or \\theta^2 for a power"
                )
            node = self.group("{", "}")
        elif self.token.kind == "num" and len(self.token.text) == 1 and self.token.text.isdigit():
            node = ("num", float(self.advance().text))
        else:
            raise AngleError(
                "an exponent is one digit or an integer constant in braces (2^{10}, not 2^10)"
            )
        if _symbols_of(node):
            raise AngleError("an exponent must be a constant")
        value = _evaluate(node, {})
        if not value.is_integer() or abs(value) > MAX_POWER:
            raise AngleError(f"an exponent must be an integer of at most {MAX_POWER}")
        return int(value)

    def group(self, opening: str, closing: str) -> Node:
        self.expect(opening)
        node = self.expression()
        self.expect(closing)
        return node

    def atom(self) -> Node:
        token = self.token
        simple = {"num": self._number, "pi": self._pi, "sym": self._symbol}.get(token.kind)
        if simple is not None:
            self.advance()
            return simple(token)
        if token.kind == "(":
            node = self.group("(", ")")
            self.edge, self.symbol_edge = "other", False
            return node
        if token.kind == "{":
            self._check_brace_group(self.index)
            return self.group("{", "}")  # the edge is the one of the last factor inside
        if token.kind in ("frac", "sqrt"):
            self.advance()
            node = self._braced(token.kind)
            self.edge, self.symbol_edge = "other", False
            return node
        raise AngleError(f"expected a number, \\pi or a symbol, found {self._describe()}")

    def _number(self, token: _Token) -> Node:
        self.edge, self.symbol_edge = "digit", False
        return ("num", _finite(float(token.text), "a number"))

    def _pi(self, _token: _Token) -> Node:
        self.edge, self.symbol_edge = "other", False
        return ("pi",)

    def _symbol(self, token: _Token) -> Node:
        self.edge, self.symbol_edge = "other", True
        self.symbols.add(token.text)
        return ("sym", token.text)

    def _braced(self, kind: str) -> Node:
        """The braced arguments of ``\\frac`` (two) or ``\\sqrt`` (one)."""
        count = 2 if kind == "frac" else 1
        groups = []
        for _ in range(count):
            if self.token.kind != "{":
                raise AngleError(f"\\{kind} needs its arguments in braces")
            groups.append(self.group("{", "}"))
        return ("div", groups[0], groups[1]) if kind == "frac" else ("sqrt", groups[0])


def _symbols_of(node: Node) -> set[str]:
    if node[0] == "sym":
        return {node[1]}
    found: set[str] = set()
    for child in node[1:]:
        if isinstance(child, tuple):
            found |= _symbols_of(child)
    return found


def parse_angle(text: str) -> Angle:
    """Parse ``text`` into an ``Angle``, or raise ``AngleError`` with the reason.

    >>> parse_angle(r"\\frac{\\pi}{2}").value == math.pi / 2
    True
    >>> parse_angle(r"-2\\theta_1").symbols
    ('theta_1',)
    """
    if not isinstance(text, str):
        raise AngleError(f"an angle is text, got {type(text).__name__}")
    if len(text) > MAX_CHARS:
        raise AngleError(f"an angle of more than {MAX_CHARS} characters")
    return _parse(text)


def _parse(text: str, *, bare_pi: bool = False) -> Angle:
    """Parse the caller's ``text``; ``bare_pi`` reads a ``pi`` that stands alone as ``\\pi``.
    A position in an error is a position in ``text``, before any rewrite."""
    stripped = text.strip().strip("$").strip()
    if not stripped:
        raise AngleError("an empty angle")
    lead = len(text) - len(text.lstrip().lstrip("$").lstrip())
    source = _Text(stripped, text, tuple(range(lead, lead + len(stripped))))
    if bare_pi:
        source = source.rewrite(_BARE_PI, lambda _: "\\pi")
    parser = _Parser(_tokenize(source))
    tree = parser.expression()
    if parser.token.kind != "end":
        raise AngleError(f"leftover text: found {parser._describe()} after the expression")
    angle = Angle(source.read, tuple(sorted(parser.symbols)), tree)
    if not angle.symbols:
        _evaluate(tree, {})  # a numeric angle must evaluate now: no division by zero, finite
    return angle


def parse_value(text: str) -> Angle:
    """Parse a binding value given as text: :func:`parse_angle` after ``pi`` that stands alone
    becomes ``\\pi``. This is the one rule for values (``values`` and ``--set``).

    ``pi`` is the constant only when no letter or backslash touches it: ``pi/4``, ``2pi`` and
    ``\\frac{pi}{3}`` are read as ``\\pi/4``, ``2\\pi`` and ``\\frac{\\pi}{3}``, while ``pie``,
    ``spin`` and ``\\pip`` are left as they are, and refused by the grammar. A value with a free
    symbol parses; the caller refuses it. A position in an error is a position in ``text`` as
    given (``pi pi ?`` names the ``?`` at position 6).

    >>> parse_value("pi/4") == parse_angle(r"\\pi/4")
    True
    """
    if not isinstance(text, str):
        raise AngleError(f"an angle is text, got {type(text).__name__}")
    if len(text) > MAX_CHARS:  # the limit is on the text as given, not on the rewritten text
        raise AngleError(f"an angle of more than {MAX_CHARS} characters")
    return _parse(text, bare_pi=True)
