"""The angle parser: the grammar, the refusals, adversarial input, and that nothing is evaluated."""

import math
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from quantum_sim import Angle, AngleError, parse_angle
from quantum_sim.angles import (
    GREEK,
    MAX_CHARS,
    MAX_DEPTH,
    binding_name,
    format_number,
    parse_value,
    symbol_name,
)

PI = math.pi


def value(text: str) -> float:
    return parse_angle(text).value


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0.0),
        ("1", 1.0),
        ("1.5", 1.5),
        (".5", 0.5),
        ("3.", 3.0),
        (r"\pi", PI),
        ("π", PI),
        (r"2\pi", 2 * PI),
        (r"2 \pi", 2 * PI),
        (r"\pi/2", PI / 2),
        (r"\pi / 4", PI / 4),
        (r"\frac{\pi}{2}", PI / 2),
        (r"\dfrac{\pi}{4}", PI / 4),
        (r"\tfrac{3\pi}{4}", 3 * PI / 4),
        (r"\frac{1}{2}\pi", PI / 2),
        (r"-\pi", -PI),
        (r"-\pi/2", -PI / 2),
        (r"+\pi", PI),
        ("--1", 1.0),
        ("−1", -1.0),
        ("1+2", 3.0),
        ("1-2", -1.0),
        ("2*3", 6.0),
        (r"2\cdot 3", 6.0),
        (r"2\times 3", 6.0),
        (r"6\div 3", 2.0),
        ("(1+2)*3", 9.0),
        ("{1*2}*3", 6.0),
        (r"\left(1+2\right)3", 9.0),
        ("2(3)", 6.0),
        ("(2)(3)", 6.0),
        ("1/2/2", 0.25),
        ("8/2*2", 8.0),
        ("2^3", 8.0),
        ("2^{3}", 8.0),
        ("2^{-3}", 0.125),
        (r"\pi/2^{3}", PI / 8),
        ("-2^2", -4.0),
        (r"\sqrt{2}", math.sqrt(2)),
        (r"\sqrt{4}\pi", 2 * PI),
        (r"\pi/\sqrt{2}", PI / math.sqrt(2)),
        (r"\frac{\pi}{2} + \frac{\pi}{4}", 3 * PI / 4),
        (r"\, 1 \; + \! 1 ~", 2.0),
        ("$\\pi/2$", PI / 2),
        ("  \\pi  ", PI),
        ("2^0", 1.0),
        ("0.1+0.2", 0.1 + 0.2),
    ],
)
def test_numbers(text, expected):
    assert value(text) == pytest.approx(expected, abs=1e-15, rel=1e-15)


@pytest.mark.parametrize(
    ("text", "symbols"),
    [
        (r"\theta", ("theta",)),
        (r"\alpha_1", ("alpha_1",)),
        (r"\alpha_{12}", ("alpha_12",)),
        (r"\alpha_{ab}", ("alpha_ab",)),
        (r"2\gamma", ("gamma",)),
        (r"-2\gamma", ("gamma",)),
        (r"\theta/2", ("theta",)),
        (r"\frac{\theta}{2}", ("theta",)),
        (r"\theta + \phi", ("phi", "theta")),
        (r"\pi - \theta_1", ("theta_1",)),
        ("t", ("t",)),
        (r"x_0", ("x_0",)),
        (r"\Delta", ("Delta",)),
        (r"\varphi", ("varphi",)),
        (r"a b", ("a", "b")),
        (r"x_1y_2", ("x_1", "y_2")),
        (r"\theta_{12}", ("theta_12",)),
        (r"\alpha_1\beta", ("alpha_1", "beta")),
        (r"\theta\phi", ("phi", "theta")),
        (r"\sqrt{\theta}", ("theta",)),
    ],
)
def test_symbols_become_parameters(text, symbols):
    angle = parse_angle(text)
    assert angle.symbols == symbols
    assert not angle.is_numeric
    with pytest.raises(AngleError, match="free symbols"):
        _ = angle.value


def test_a_symbol_is_evaluated_at_given_values():
    angle = parse_angle(r"\frac{\theta_1 + 2\phi}{4}")
    assert angle.evaluate({"theta_1": 1.0, "phi": 0.5}) == pytest.approx(0.5)
    assert angle.evaluate({"theta_1": 1.0, "phi": 0.5, "unused": 9.0}) == pytest.approx(0.5)
    assert parse_angle(r"\theta^2").evaluate({"theta": 3}) == 9.0


def test_evaluation_refuses_what_it_cannot_do():
    angle = parse_angle(r"1/\theta")
    with pytest.raises(AngleError, match="no value for the symbol 'theta'"):
        angle.evaluate({})
    with pytest.raises(AngleError, match="division by zero"):
        angle.evaluate({"theta": 0.0})
    with pytest.raises(AngleError, match="not a real number"):
        angle.evaluate({"theta": "1"})  # type: ignore[dict-item]
    with pytest.raises(AngleError, match="not a real number"):
        angle.evaluate({"theta": True})
    with pytest.raises(AngleError, match="finite"):
        angle.evaluate({"theta": math.inf})
    with pytest.raises(AngleError, match="overflows"):
        parse_angle(r"\theta^{64}").evaluate({"theta": 1e300})
    with pytest.raises(AngleError, match="overflows or divides"):
        parse_angle(r"\theta^{-1}").evaluate({"theta": 0.0})
    with pytest.raises(AngleError, match="negative"):
        parse_angle(r"\sqrt{\theta}").evaluate({"theta": -1.0})
    with pytest.raises(AngleError, match="finite"):
        parse_angle(r"\theta*\theta").evaluate({"theta": 1e300})
    with pytest.raises(AngleError, match="finite"):
        parse_angle(r"\theta+\theta").evaluate({"theta": 1.7e308})
    with pytest.raises(AngleError, match="finite"):
        parse_angle(r"\theta-\phi").evaluate({"theta": -1.7e308, "phi": 1.7e308})


def test_a_value_too_large_for_a_float_is_an_angle_error():
    with pytest.raises(AngleError, match="too large for a float"):
        parse_angle(r"\theta").evaluate({"theta": 10**400})


def test_negation_and_construction():
    angle = parse_angle(r"\theta/2").negated()
    assert angle.evaluate({"theta": 2.0}) == -1.0
    assert angle.symbols == ("theta",)
    assert Angle.of(0.25).value == 0.25
    assert Angle.of(0.25) == parse_angle("0.25")
    with pytest.raises(AngleError, match="not a finite"):
        Angle.of(math.nan)


REFUSALS = [
    ("", "empty"),
    ("   ", "empty"),
    ("$$", "empty"),
    (r"\pi/2 + ", "found the end"),
    ("+", "found the end"),
    ("*2", "found '*'"),
    ("2**3", "found '*'"),
    ("2 3", "two numbers in a row"),
    ("1.5 2", "two numbers in a row"),
    ("1/2\\pi", "ambiguous"),
    ("1/2 3", "ambiguous"),
    ("1/\\pi\\theta", "ambiguous"),
    ("1e3", "may be a constant"),
    ("2E3", "may be a constant"),
    ("e", "may be a constant"),
    ("i", "may be a constant"),
    ("j", "may be a constant"),
    ("2i", "may be a constant"),
    ("1,5", "unsupported character ','"),
    ("1_000", "unsupported character '_'"),
    ("1..2", "two numbers in a row"),
    (".", "stray"),
    ("(1", "expected ')'"),
    ("1)", "leftover"),
    ("{1", "expected '}'"),
    ("1}", "leftover"),
    ("()", "found ')'"),
    (r"\frac12", "braces"),
    (r"\frac{1}2", "braces"),
    (r"\frac{1}", "braces"),
    (r"\frac{1}{2", "expected '}'"),
    (r"\frac", "braces"),
    (r"\sqrt2", "braces"),
    (r"\sqrt[3]{8}", "unsupported character '['"),
    (r"\sqrt{-1}", "negative"),
    ("2^10", "2^{10}"),
    ("2^x", "an exponent"),
    ("2^{\\theta}", "constant"),
    ("2^{0.5}", "integer"),
    ("2^{65}", "at most 64"),
    ("2^{-65}", "at most 64"),
    ("2^3^4", "double exponent"),
    ("2^", "an exponent"),
    # a superscript in parentheses numbers a layer: it is an index, not a power
    (r"\theta^{(0)}", "a superscript in parentheses is an index"),
    (r"\theta^{(1)}", "a superscript in parentheses is an index"),
    (r"\theta^{(2)}", "a superscript in parentheses is an index"),
    (r"\theta^{(-1)}", "a superscript in parentheses is an index"),
    (r"\theta_1^{(2)}", "a superscript in parentheses is an index"),
    (r"\gamma^{(1)}", "a superscript in parentheses is an index"),
    (r"{\theta}^{(2)}", "a superscript in parentheses is an index"),
    (r"\theta^{{(1)}}", "a superscript in parentheses is an index"),
    (r"\theta^{ ( 1 ) }", "a superscript in parentheses is an index"),
    (r"\theta^{\left(1\right)}", "a superscript in parentheses is an index"),
    (r"2^{(3)}", "a superscript in parentheses is an index"),
    (r"(\theta)^{(2)}", "a superscript in parentheses is an index"),
    (r"\theta^{(l)}", "a superscript in parentheses is an index"),
    # a parenthesis later in the braces is refused too, whatever stands before it
    (r"\theta^{1(2)}", "a superscript in parentheses is an index"),
    (r"\theta^{0+(2)}", "a superscript in parentheses is an index"),
    (r"\theta^{2\cdot(1)}", "a superscript in parentheses is an index"),
    (r"\theta^{\frac{(2)}{1}}", "a superscript in parentheses is an index"),
    (r"\theta^{\sqrt{(4)}}", "a superscript in parentheses is an index"),
    (r"\theta^{+(1)}", "a superscript in parentheses is an index"),
    (r"2^{1-(1-3)}", "a superscript in parentheses is an index"),
    (r"2^{-(1)}", "a superscript in parentheses is an index"),
    (r"\theta^(1)", "an exponent is one digit"),
    ("9^{9^{9}}", "at most 64"),
    ("1/0", "division by zero"),
    (r"1/(\pi-\pi)", "division by zero"),
    ("0^{-1}", "overflows or divides"),
    (r"10^{64}10^{64}10^{64}10^{64}10^{64}", "finite"),
    ("1" + "0" * 400, "more than"),
    ("1" * 330, "finite"),
    ("2 = 3", "unsupported character '='"),
    (r"\theta = \pi", "unsupported character '='"),
    (r"\theta, \phi", "unsupported character ','"),
    (r"\theta'", "unsupported character"),
    (r"\theta_12", "digit right after the subscript"),
    (r"\pi.5", "starting with '.' right after a factor"),
    (r"\theta.5", "starting with '.' right after a factor"),
    (r"(1).5", "starting with '.' right after a factor"),
    (r"{1}.5", "starting with '.' right after a factor"),
    (r"2\pi.5", "starting with '.' right after a factor"),
    (r"\theta_1.5", "digit right after the subscript"),
    (r"x_12", "digit right after the subscript"),
    (r"2\alpha_10", "digit right after the subscript"),
    # braces group but do not separate: LaTeX shows 2{5} as 25
    ("2{5}", "two numbers in a row"),
    ("{2}5", "two numbers in a row"),
    ("{2}{5}", "two numbers in a row"),
    ("1{0}", "two numbers in a row"),
    ("{{2}}{{5}}", "two numbers in a row"),
    ("{2+3}5", "a brace group that holds a sum"),
    ("{-2}3", "two numbers in a row"),
    ("2^3 4", "two numbers in a row"),
    # LaTeX shows 2\frac{1}{2} as two and a half, a mixed number
    (r"2\frac{1}{2}", "a number right before a fraction of two numbers"),
    (r"1\frac{1}{2}\pi", "a number right before a fraction of two numbers"),
    (r"2 \frac{ 1 }{ 2 }", "a number right before a fraction of two numbers"),
    (r"2{\frac{1}{2}}", "a number right before a fraction of two numbers"),
    (r"{2}\frac{1}{2}", "a number right before a fraction of two numbers"),
    (r"2\frac{-1}{2}", "a number right before a fraction of two numbers"),
    (r"2\dfrac{1}{.5}", "a number right before a fraction of two numbers"),
    (r"2^3\frac{1}{2}", "a number right before a fraction of two numbers"),
    # the same, with braces, parentheses or signs around the numbers
    (r"2\frac{{1}}{2}", "a number right before a fraction of two numbers"),
    (r"2\frac{1}{{2}}", "a number right before a fraction of two numbers"),
    (r"2\frac{(1)}{2}", "a number right before a fraction of two numbers"),
    (r"2\frac{1}{(2)}", "a number right before a fraction of two numbers"),
    (r"2\frac{--1}{2}", "a number right before a fraction of two numbers"),
    (r"2\frac{-(1)}{{(2)}}", "a number right before a fraction of two numbers"),
    (r"2\frac{{{1}}}{+2}", "a number right before a fraction of two numbers"),
    ("2{-5}", "a sign inside braces right after a factor"),
    ("2{+5}", "a sign inside braces right after a factor"),
    ("{2}{-5}", "a sign inside braces right after a factor"),
    (r"\pi{-5}", "a sign inside braces right after a factor"),
    (r"(1){-5}", "a sign inside braces right after a factor"),
    ("2{.5}", "starting with '.' right after a factor"),
    (r"\pi{.5}", "starting with '.' right after a factor"),
    (r"\theta{.5}", "a number right after a symbol"),
    (r"(1){.5}", "starting with '.' right after a factor"),
    (r"\frac{1}{2}{.5}", "starting with '.' right after a factor"),
    ("2{{.5}}", "starting with '.' right after a factor"),
    # a symbol right before a parenthesis is a function or a product: not read
    (r"\phi(x)", "may be a function"),
    (r"f(x)", "may be a function"),
    (r"\theta(t)", "may be a function"),
    (r"\gamma(t)", "may be a function"),
    (r"2\phi(x)", "may be a function"),
    (r"\phi (x)", "may be a function"),
    (r"\phi_{1}(x)", "may be a function"),
    (r"\phi^2(x)", "may be a function"),
    (r"\phi^{2}(x)", "may be a function"),
    (r"{\phi}(x)", "may be a function"),
    (r"\phi{(x)}", "may be a function"),
    (r"\theta\left(t\right)", "may be a function"),
    (r"-\theta(1)", "may be a function"),
    (r"1 + \theta(1)", "may be a function"),
    (r"\pi \theta(1)", "may be a function"),
    # braces are not drawn: LaTeX shows 2{\theta+1} as 2\theta+1
    (r"2{\theta+1}", "a brace group that holds a sum"),
    (r"{\theta+1}\phi", "a brace group that holds a sum"),
    (r"\frac{1}{2}{\theta+\pi}", "a brace group that holds a sum"),
    (r"{x+1}^2", "a brace group that holds a sum"),
    (r"{\theta-1}", "a brace group that holds a sum"),
    (r"1+{\theta+1}", "a brace group that holds a sum"),
    (r"a/{b+c}", "a brace group that holds a sum"),
    (r"-{\theta+1}", "a brace group that holds a sum"),
    (r"2*{\theta-1}", "a brace group that holds a sum"),
    (r"{{\theta+1}}", "a brace group that holds a sum"),
    (r"x^{2*{1+1}}", "a brace group that holds a sum"),
    (r"{\theta/2}x", "a brace group that holds a sum"),
    ("{1/2}x", "a brace group that holds a sum"),
    (r"2{\theta/2}", "a brace group that holds a sum"),
    (r"{\theta*2/3}", "a brace group that holds a sum"),
    (r"{2*-\theta+1}", "a brace group that holds a sum"),
    (r"{2\theta}^2", "a brace group of more than one factor as the base of a power"),
    (r"{-x}^2", "a brace group of more than one factor as the base of a power"),
    (r"{-2}^2", "a brace group of more than one factor as the base of a power"),
    (r"{{-x}}^2", "a brace group of more than one factor as the base of a power"),
    (r"{\theta\phi}^{2}", "a brace group of more than one factor as the base of a power"),
    (r"{\theta^2}^2", "a brace group of more than one factor as the base of a power"),
    (r"2{x y}^2", "a brace group of more than one factor as the base of a power"),
    (r"{2\pi}^2", "a brace group of more than one factor as the base of a power"),
    (r"{2*3}^2", "a brace group of more than one factor as the base of a power"),
    (r"{(x)(y)}^2", "a brace group of more than one factor as the base of a power"),
    (r"{\frac{1}{2}\pi}^2", "a brace group of more than one factor as the base of a power"),
    (r"{\sqrt{2}\sqrt{2}}^2", "a brace group of more than one factor as the base of a power"),
    # a number right after a symbol may be a name (\theta1)
    (r"\theta1", "a number right after a symbol"),
    (r"\theta 2", "a number right after a symbol"),
    (r"x1", "a number right after a symbol"),
    (r"x 2", "a number right after a symbol"),
    (r"\theta{2}", "a number right after a symbol"),
    (r"{\theta}2", "a number right after a symbol"),
    (r"\theta_1 2", "a number right after a symbol"),
    (r"\theta_{1}2", "a number right after a symbol"),
    (r"\theta^2 3", "a number right after a symbol"),
    (r"\theta^{2}3", "a number right after a symbol"),
    ("ab", "'ab' in a row"),
    ("cos", "'cos' in a row"),
    (r"cos\theta", "'cos' in a row"),
    (r"\theta xy", "'xy' in a row"),
    (r"2\pi xyz", "'xyz' in a row"),
    ("exp", "'exp' in a row"),
    (r"\theta_", "subscript"),
    (r"\theta_{}", "subscript"),
    (r"\theta_{123456789}", "subscript"),
    (r"\theta_{a b}", "subscript"),
    (r"\unknown", "unknown command"),
    (r"\Pi", "unknown command"),
    (r"\infty", "unknown command"),
    (r"\\", "unsupported command"),
    ("\\", "unsupported command"),
    (r"\left", "parenthesis"),
    (r"\left[1\right]", "parenthesis"),
    ("__import__('os').system('true')", "unsupported character"),
    ("[1]", "unsupported character"),
    ("1; 2", "unsupported character"),
    ("1\n2", "two numbers"),
    ("a.b", "stray"),
    ("²", "unsupported character"),
    ("٣", "unsupported character"),  # an Arabic-Indic digit is not a digit here
    ("１", "unsupported character"),  # a full-width digit
    ("θ", "unsupported character"),  # unicode Greek is not read; use \theta
]


@pytest.mark.parametrize(("text", "reason"), REFUSALS)
def test_refusals_name_their_reason(text, reason):
    with pytest.raises(AngleError, match=re.escape(reason)):
        parse_angle(text)


@pytest.mark.parametrize(
    "text",
    [
        r"2(\pi - 1)",
        r"\pi(1)",
        r"\pi (\theta)",
        r"(\pi - \theta)(\pi - \phi)",
        r"\frac{1}{2}(\theta)",
        r"\sqrt{2}(\theta)",
        r"2\theta",
        r"\theta * (1)",
        r"\theta \cdot (1)",
        r"\theta^2 \phi",
        r"\theta * 2",
        r"\theta + 2",
        r"\theta - 2(\phi)",
        r"\theta_{12}",
        r"{2}\theta",
    ],
)
def test_a_number_pi_or_closing_parenthesis_before_a_parenthesis_is_still_a_product(text):
    parse_angle(text)


def test_text_that_is_not_text_is_refused():
    for bad in (None, 1, 1.5, b"1", ["1"]):
        with pytest.raises(AngleError, match="is text"):
            parse_angle(bad)  # type: ignore[arg-type]


def test_nothing_is_evaluated_as_code():
    sentinel: list[str] = []
    for source in (
        "__import__('os')",
        "exec('x')",
        "(lambda: 1)()",
        "().__class__",
        "1 if 1 else 2",
        "[i for i in range(9)]",
        "open('/etc/passwd')",
        "pi",
        "math.pi",
        "np.pi",
    ):
        with pytest.raises(AngleError):
            parse_angle(source)
    assert not sentinel


def test_limits_on_size_and_depth():
    deep = "(" * (MAX_DEPTH + 1) + "1" + ")" * (MAX_DEPTH + 1)
    with pytest.raises(AngleError, match="nested more than"):
        parse_angle(deep)
    assert value("(" * 20 + "1" + ")" * 20) == 1.0
    assert value("(" * (MAX_DEPTH - 1) + "1" + ")" * (MAX_DEPTH - 1)) == 1.0  # the deepest allowed
    with pytest.raises(AngleError, match="nested more than"):
        parse_angle("(" * MAX_DEPTH + "1" + ")" * MAX_DEPTH)
    with pytest.raises(AngleError, match="nested more than"):
        parse_angle("-" * 100 + "1")
    with pytest.raises(AngleError, match="nested more than"):
        parse_angle(r"\frac{" * 45 + "1" + "}{1}" * 45)
    with pytest.raises(AngleError, match=f"more than {MAX_CHARS} characters"):
        parse_angle("1+" * (MAX_CHARS // 2) + "1")
    assert value("1+" * 100 + "1") == 101.0  # a long flat sum is fine
    assert value("1" + "0" * 300) == pytest.approx(1e300)


def test_format_number_writes_what_the_parser_reads_back_exactly():
    for number in (0.0, -0.0, 1.0, 0.1, 1e-05, 1.5e-07, 5e-324, 2.2250738585072014e-308, 1e16):
        assert float(format_number(number)) == number
        assert parse_angle(format_number(number)).value == number
    for number in (1.7976931348623157e308, -2.5e-10, 1.2345e20, 3.0, 123456789.123):
        text = format_number(number)
        assert "e" not in text.lower()
        assert parse_angle(text).value == number
    assert len(format_number(5e-324)) == 326
    assert format_number(1e-05) == "0.00001"
    assert format_number(1.5e-07) == "0.00000015"
    assert format_number(1e16) == "10000000000000000.0"


@settings(max_examples=300, deadline=None)
@given(st.floats(allow_nan=False, allow_infinity=False))
def test_every_finite_float_round_trips(number):
    assert parse_angle(format_number(number)).value == number


@settings(max_examples=500, deadline=None)
@given(st.text(max_size=80))
def test_arbitrary_text_gives_an_angle_or_an_angle_error(text):
    try:
        angle = parse_angle(text)
    except AngleError:
        return
    if angle.is_numeric:
        assert math.isfinite(angle.value)


GRAMMAR_PIECES = [
    "1", "2", "3.5", r"\pi", r"\theta", r"\phi_1", "+", "-", "*", "/", "^", "(", ")", "{", "}",
    r"\frac", r"\sqrt", " ", "x",
]  # fmt: skip


@settings(max_examples=500, deadline=None)
@given(st.lists(st.sampled_from(GRAMMAR_PIECES), max_size=25))
def test_random_token_soup_gives_an_angle_or_an_angle_error(pieces):
    try:
        angle = parse_angle("".join(pieces))
    except AngleError:
        return
    assert isinstance(angle.symbols, tuple)
    if angle.is_numeric:
        assert math.isfinite(angle.value)
    else:
        try:
            result = angle.evaluate(dict.fromkeys(angle.symbols, 0.7))
        except AngleError:
            return
        assert math.isfinite(result)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        (r"\frac{1}{2}", 0.5),
        (r"\frac{1}{.5}", 2.0),  # an argument is not a factor that follows another
        (r"\frac{1}{2}3", 1.5),
        (r"\frac{1}{2}{3}", 1.5),
        (r"{\frac{1}{2}}3", 1.5),
        (r"\sqrt{4}3", 6.0),
        (r"{2}\pi", 2 * math.pi),
        (r"2{\pi}", 2 * math.pi),
        (r"{2}{\pi}", 2 * math.pi),
        (r"(2){3}", 6.0),
        (r"(2)3", 6.0),
        ("{2}(3)", 6.0),
        ("2*{5}", 10.0),
        ("{2}*{5}", 10.0),
        ("2*{-5}", -10.0),
        ("{-5}", -5.0),
        ("{{5}}", 5.0),
        # a brace group that is one factor, or whose sign opens it, is the same with no braces
        ("{2}^2", 4.0),
        (r"{\frac{1}{2}}^2", 0.25),
        (r"{\frac{1-3}{2}}^2", 1.0),
        (r"{\sqrt{4}}^2", 4.0),
        ("{(1+1)}^2", 4.0),
        ("{{2}}^2", 4.0),
        ("2*{2}^2", 8.0),
        ("{-2}", -2.0),
        ("{+2}", 2.0),
        ("-{2}", -2.0),
        ("3-{2}", 1.0),
        ("2*{-3}", -6.0),
        ("2/{-4}", -0.5),
        ("{2*-3}", -6.0),
        (r"\frac{1}{2}{\pi}", math.pi / 2),
        (r"{(1+1)}3", 6.0),
        (r"{\frac{1-3}{2}}3", -3.0),
        ("2^{3}4", 32.0),
        ("2^{3}{4}", 32.0),
        # a fraction that is not two plain numbers, or not right after a number, is a product
        (r"2\frac{\pi}{3}", 2 * math.pi / 3),
        (r"2\frac{1}{1+1}", 1.0),
        (r"2\frac{1+1}{2}", 2.0),
        (r"\pi\frac{1}{2}", math.pi / 2),
        (r"(2)\frac{1}{2}", 1.0),
        (r"\frac{1}{2}\frac{1}{2}", 0.25),
        (r"2*\frac{1}{2}", 1.0),
    ],
)
def test_braces_that_do_not_run_two_numbers_together_are_read(text, value):
    assert parse_angle(text).value == pytest.approx(value, abs=0, rel=1e-15)


@pytest.mark.parametrize(
    "text", [r"2\frac{(1}{2}", r"2\frac{{1)}{2}", r"2\frac{((1)}{2}", r"2\frac{(", r"2\frac{-"]
)
def test_a_number_wrapped_in_unbalanced_brackets_is_a_syntax_error(text):
    with pytest.raises(AngleError):
        parse_angle(text)


@pytest.mark.parametrize(
    ("text", "value"),
    [(r"\theta^2", 4.0), (r"\theta^{2}", 4.0), (r"\theta^{-1}", 0.5), (r"2^{1+1}", 4.0)],
)
def test_a_power_with_no_parenthesis_in_the_braces_is_still_a_power(text, value):
    assert parse_angle(text).evaluate({"theta": 2.0}) == pytest.approx(value, abs=0, rel=1e-15)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        (r"{\theta}", 3.0),
        (r"2{\theta}", 6.0),
        (r"2{\theta}^2", 18.0),
        (r"{\theta}^{2}", 9.0),
        (r"{\theta}^2", 9.0),
        (r"{-\theta}", -3.0),
        (r"2*{-\theta}", -6.0),
        (r"{\theta}{\phi}", 6.0),
        (r"{(\theta+1)}^2", 16.0),
        (r"{\frac{\theta}{2}}\phi", 3.0),
        (r"{\theta_{12}}^2", 9.0),
    ],
)
def test_a_brace_group_of_one_factor_is_read_as_that_factor(text, value):
    values = {"theta": 3.0, "phi": 2.0, "theta_12": 3.0}
    assert parse_angle(text).evaluate(values) == pytest.approx(value, abs=0, rel=1e-15)


@pytest.mark.parametrize(
    ("text", "value"),
    [("(x)^2(y)", 18.0), ("(x)^{2}(y)", 18.0), ("2^2(y)", 8.0), ("(x)^2*(y)", 18.0)],
)
def test_a_parenthesis_with_a_power_before_a_parenthesis_is_a_product(text, value):
    # the closing parenthesis is drawn, so (x)^2 is not a symbol that may be a function
    assert parse_angle(text).evaluate({"x": 3.0, "y": 2.0}) == pytest.approx(value)


@pytest.mark.parametrize("text", [r"\theta^{1", r"\theta^{{1}", r"2^{"])
def test_an_exponent_that_is_never_closed_is_a_syntax_error(text):
    with pytest.raises(AngleError, match="expected"):
        parse_angle(text)


def test_a_parenthesis_outside_the_braces_of_an_exponent_is_still_a_group():
    assert parse_angle(r"2^{1}(3)").value == pytest.approx(6.0)
    assert parse_angle(r"(1-(1-3))^{2}").value == pytest.approx(9.0)


# --- Binding names -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "name"),
    [
        ("theta", "theta"),
        (r"\theta", "theta"),
        (r"{\theta}", "theta"),
        (r"{ \theta }", "theta"),
        (r" {{\theta}} ", "theta"),
        (r"\theta_1", "theta_1"),
        (r"\theta_{1}", "theta_1"),
        ("theta_{1}", "theta_1"),
        (r"{\theta_{1}}", "theta_1"),
        ("x", "x"),
        ("x_1", "x_1"),
        (r"\alpha_{12}", "alpha_12"),
        (r"\Gamma", "Gamma"),
        ("phi_{ab}", "phi_ab"),
        # white space around the name is ignored, a line break at either end too; a line break
        # inside the name ("a\nb") is refused below
        ("theta\n", "theta"),
        (" theta ", "theta"),
        ("\n\\theta_{1}\t", "theta_1"),
    ],
)
def test_a_binding_key_that_is_one_symbol_gets_the_name_of_the_grammar(key, name):
    assert symbol_name(key) == name


@pytest.mark.parametrize(
    "key",
    [
        "",
        "   ",
        "{}",
        "pi",
        r"\pi",
        "e",
        "E",
        "i",
        "j",
        "thetta",
        r"\x",
        "a\nb",
        "x y",
        "the{ta}",
        r"\\theta",
        r"$\theta$",
        "theta_12",
        "theta_{123456789}",
        r"\theta _1",
        "x\nOPENQASM 2.0;\n//",
        "θ",
    ],
)
def test_a_binding_key_that_is_not_one_symbol_has_no_name(key):
    assert symbol_name(key) is None


@pytest.mark.parametrize("word", sorted(GREEK))
def test_the_binding_name_is_the_symbol_the_parser_gives(word):
    for text in (rf"\{word}", rf"\{word}_1", rf"\{word}_{{a1}}"):
        assert symbol_name(text) == parse_angle(text).symbols[0]
    for letter in ("x", "k", "Z"):
        assert symbol_name(f"{letter}_2") == parse_angle(f"{letter}_2").symbols[0]


def test_a_binding_key_longer_than_an_angle_has_no_name():
    # the brace loop is bounded: a request body can hold a key of a megabyte
    assert symbol_name("{" * 10 + r"\theta" + "}" * 10) == "theta"
    assert symbol_name("{" * 300 + r"\theta" + "}" * 300) is None
    assert symbol_name("{" * 500_000 + "x" + "}" * 500_000) is None


@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("", "a parameter binding has an empty name"),
        (" \t", "a parameter binding has an empty name"),
        ("thetta", "'thetta' is not a parameter name: write one symbol, such as theta, "),
        ("x" * 60, "'" + "x" * 37 + "...' is not a parameter name"),
    ],
)
def test_binding_name_says_why_a_key_is_not_a_name(key, message):
    with pytest.raises(AngleError) as refused:
        binding_name(key)
    assert str(refused.value).startswith(message)
    assert binding_name(r"{\theta_{1}}") == "theta_1"


# --- Binding values ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "spelled"),
    [
        ("pi/4", r"\pi/4"),
        ("2pi", r"2\pi"),
        ("2 pi", r"2\pi"),
        ("-pi", r"-\pi"),
        (r"\pi/4", r"\pi/4"),
        (r"\frac{pi}{3}", r"\frac{\pi}{3}"),
        ("(pi)/2", r"\pi/2"),
        ("0.5", "0.5"),
    ],
)
def test_a_binding_value_may_write_pi_without_the_backslash(text, spelled):
    assert parse_value(text) == parse_angle(spelled)
    assert parse_value(text).value == parse_angle(spelled).value


@pytest.mark.parametrize("text", ["pie", "spin", "pix", "api/4", r"\pip"])
def test_pi_inside_a_longer_word_is_not_the_constant_in_a_binding_value(text):
    with pytest.raises(AngleError):
        parse_value(text)


def test_a_binding_value_with_a_free_symbol_keeps_it_for_the_caller_to_refuse():
    assert parse_value(r"pi\theta").symbols == ("theta",)


@pytest.mark.parametrize(
    ("parse", "text", "message"),
    [
        (parse_value, "pi pi ?", "unsupported character '?' at position 6"),
        (parse_value, "pi+pi+pi+#", "unsupported character '#' at position 9"),
        (parse_value, "pi?", "unsupported character '?' at position 2"),
        (parse_value, " $pi pi.5$", "(position 7)"),
        (parse_value, r"pi\%pi", r"unsupported command at position 2: '\\%pi'"),
        (parse_value, "pi 1 . ", "a stray '.' at position 5"),
        (parse_angle, "  ?", "unsupported character '?' at position 2"),
        (parse_angle, "π π ?", "unsupported character '?' at position 4"),
        (parse_angle, r"\pi\pi\pi#", "unsupported character '#' at position 9"),
    ],
)
def test_a_position_in_an_angle_error_is_a_position_in_the_text_as_given(parse, text, message):
    # pi is spelled \pi, π is spelled \pi and the white space is stripped before the text is
    # read; the caller's text has none of those rewrites
    with pytest.raises(AngleError) as refused:
        parse(text)
    assert message in str(refused.value)


def test_the_length_limit_of_a_binding_value_is_on_the_text_as_given():
    many = "+".join(["pi"] * 166)  # 497 characters, longer than MAX_CHARS once spelled \pi
    assert len(many) <= MAX_CHARS < len(many.replace("pi", r"\pi"))
    assert parse_value(many).value == pytest.approx(166 * PI)
    with pytest.raises(AngleError, match="more than 500 characters"):
        parse_value("1" + " " * MAX_CHARS)
    with pytest.raises(AngleError, match="an angle is text"):
        parse_value(0.5)  # type: ignore[arg-type]
