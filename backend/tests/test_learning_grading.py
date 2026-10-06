"""SPEC §6.1 grading: SymPy equivalence, normalization, structures, tolerance, near-miss feedback, safety."""

from __future__ import annotations

import time

import pytest

from app.learning import grading
from app.learning.grading import check_answer, is_checkable, normalize


def grade(answer: str, response: str, kind: str = "expression", tolerance: float | None = None, require_simplified: bool = True):
    return check_answer(
        kind,
        answer=answer,
        correct_choice=None,
        tolerance=tolerance,
        response=response,
        choice=None,
        require_simplified=require_simplified,
    )


ACCEPT = [
    # complex arithmetic
    ("-7+22*i", "-7+22i"),
    ("-7+22*i", "-7 + 22 i"),
    ("-7+22*i", "−7+22i"),
    ("-7+22*i", "-7+22j"),
    ("(a - b*i)/(a^2+b^2)", "1/(a+bi)"),
    ("(a - b*i)/(a^2+b^2)", "1/(a+b*i)"),
    ("(a - b*i)/(a^2+b^2)", "\\frac{a-bi}{a^2+b^2}"),
    ("-1/2 + sqrt(3)/2*i", "\\frac{-1+\\sqrt{3}i}{2}"),
    ("z*conjugate(z)", "|z|^2"),
    ("conjugate(z)", "\\overline{z}"),
    # vectors / tuples / sets / matrices
    ("(1, -2, 3)", "(1, -2, 3)"),
    ("(1, -2, 3)", "(1,-2,3)"),
    ("(1, -2, 3)", "[1, -2, 3]"),
    ("(1, -2, 3)", "\\begin{pmatrix}1\\\\-2\\\\3\\end{pmatrix}"),
    ("(1/2, sqrt(3)/2)", "(\\frac12, \\frac{\\sqrt3}{2})"),
    ("{1, 2}", "{2, 1}"),
    ("{1, 2}", "\\{2, 1\\}"),
    ("{1, 2}", "2, 1"),
    ("[[1, 2], [3, 4]]", "\\begin{bmatrix}1&2\\\\3&4\\end{bmatrix}"),
    # roots, fractions, powers, implicit multiplication
    ("sqrt(2)/2", "sqrt(2)/2"),
    ("sqrt(2)/2", "1/sqrt(2)"),
    ("sqrt(2)/2", "\\frac{\\sqrt{2}}{2}"),
    ("sqrt(2)/2", "√2/2"),
    ("x**2", "x^2"),
    ("x**2", "x²"),
    ("2*x", "2x"),
    ("2*x", "x + x"),
    ("x*y", "yx"),
    ("a*(b+c)", "a(b+c)"),
    ("3*x", "3 \\cdot x"),
    ("x**2+2*x+1", "(x+1)^2"),
    ("2*pi", "2π"),
    ("2*lamda", "2λ"),
    ("x_1 + x_2", "x_{1}+x_{2}"),
    ("1/2", "\\left(\\frac{1}{2}\\right)"),
    ("1/2", "x = 1/2"),
    ("1/2", "$\\dfrac12$"),
    ("1/3", "0.333333333333333"),
]


@pytest.mark.parametrize("answer,response", ACCEPT)
def test_accepts_equivalent_answers(answer: str, response: str) -> None:
    result = grade(answer, response)
    assert result.correct, (answer, response, result.feedback)
    assert not result.invalid


REJECT = [
    ("-7+22*i", "-7-22i", "conjugate"),
    ("-7+22*i", "7-22i", "sign"),
    ("-7+22*i", "22-7i", "swapped"),
    ("4+3*i", "-4+3i", "real part"),
    ("(1, -2, 3)", "(1, 3, -2)", "wrong order"),
    ("(1, -2, 3)", "(1, 2, 3)", "Component 2"),
    ("(1, -2, 3)", "(1, -2)", "Expected 3"),
    ("(1, 2)", "{1, 2}", "Order matters"),
    ("{1, 2}", "{1, 3}", "missing"),
    ("2*x", "4x", "2×"),
    ("x/3", "x", "3×"),
    ("2", "1/2", "reciprocal"),
    ("z*conjugate(z)", "z^2", "Not equivalent"),
    ("sqrt(x^2)", "x", "Not equivalent"),
    ("t", "x", "uses x"),
    ("sqrt(2)/2", "0.7071", "exact value"),
    ("x**2+2*x+1", "(x+1)^2 + 1", "Not equivalent"),
    ("(1, -2, 3)", "6", "Expected 3 components"),
]


@pytest.mark.parametrize("answer,response,hint", REJECT)
def test_rejects_with_specific_feedback(answer: str, response: str, hint: str) -> None:
    result = grade(answer, response)
    assert not result.correct and not result.invalid, (answer, response, result.feedback)
    assert hint.lower() in result.feedback.lower(), result.feedback


def test_numeric_tolerance() -> None:
    assert grade("2/3", "0.6667", kind="numeric", tolerance=0.001).correct
    loose = grade("2/3", "0.6667", kind="numeric", tolerance=0.001)
    assert "tolerance" in loose.feedback
    strict = grade("2/3", "0.6667", kind="numeric")
    assert not strict.correct and "exact" in strict.feedback
    assert not grade("2/3", "0.67", kind="numeric", tolerance=0.001).correct
    assert "tolerance" in grade("2/3", "0.67", kind="numeric", tolerance=0.001).feedback
    assert grade("2/3", "2/3", kind="numeric").correct
    assert grade("-3.5", "-7/2", kind="numeric").correct
    assert grade("1e-3", "0.001", kind="numeric").correct
    assert grade("1000000", "1000000.0000001", kind="numeric").correct  # relative slack for large values
    assert grade("-7+22*i", "-7+22i", kind="numeric").correct  # complex numerics
    assert not grade("3", "a+1", kind="numeric").correct


def test_exact_form_feedback() -> None:
    assert grade("-7+22*i", "-7+22i").feedback == "Correct."
    assert grade("-7+22*i", "(2+3i)(4+5i)", require_simplified=False).feedback.startswith("Correct — equivalent")


@pytest.mark.parametrize(
    "response",
    ["", "   ", "2(", "__import__('os')", "().__class__", "x if y else z", "3!", "9^9^9", "((2^1000)^1000)^1000", "x^x^x^x", "1 = 2 = 3", "\\foo{3}"],
)
def test_invalid_or_dangerous_input_is_rejected_fast(response: str) -> None:
    start = time.monotonic()
    result = grade("3", response)
    assert time.monotonic() - start < 2.0
    assert not result.correct and result.invalid, result.feedback


def test_long_input_is_capped() -> None:
    result = grade("1", "1+" * 300 + "1")
    assert result.invalid


def test_choice_questions() -> None:
    ok = check_answer("multiple_choice", answer="", correct_choice=2, tolerance=None, response=None, choice=2)
    assert ok.correct
    assert not check_answer("true_false", answer="", correct_choice=0, tolerance=None, response=None, choice=1).correct
    missing = check_answer("multiple_choice", answer="", correct_choice=2, tolerance=None, response=None, choice=None)
    assert missing.invalid


def test_free_response_is_self_graded() -> None:
    result = check_answer("free_response", answer="", correct_choice=None, tolerance=None, response="proof...", choice=None)
    assert not result.correct and not result.invalid


def test_unparseable_answer_key_does_not_blame_the_learner() -> None:
    result = grade("\\weird{1}", "2")
    assert not result.correct and not result.invalid
    assert "can't be checked" in result.feedback


def test_normalize_latex_and_unicode() -> None:
    assert normalize("\\frac{1}{2}") == "((1)/(2))"
    assert normalize("x²") == "x**(2)"
    assert normalize("3·4") == "3*4"
    assert "sqrt(2)" in normalize("√2")


def test_is_checkable() -> None:
    assert is_checkable("expression", "(1, -2, 3)", None)
    assert is_checkable("numeric", "2/3", None)
    assert not is_checkable("expression", "\\weird{1}", None)
    assert is_checkable("multiple_choice", "", 1)
    assert not is_checkable("multiple_choice", "", None)


# ---------------------------------------------------------------------------
# Unsimplified answers (D3): same value as a reduced constant key, but not reduced -> invalid (asked again)
# ---------------------------------------------------------------------------

# Equal in value but unsimplified: accepted only when the caller does not require the simplified form (pipeline).
UNSIMPLIFIED = [
    ("-7+22*i", "(2+3i)(4+5i)", "a + bi"),
    ("-i", "1/i", "a + bi"),
    ("5", "|3+4i|", "simplify"),
    ("-1", "e^{i\\pi}", "simplify"),
    ("2", "\\sqrt[3]{8}", "simplify"),
    ("6", "2 × 3", "simplify"),
    ("1", "sin(x)^2 + cos(x)^2", "simplify"),
    # QA: retyping the prompt expression must not pass computational practice
    ("6+8*i", "(4+3i)+(2+5i)", "a + bi"),
    ("(1+i, -1-i)", "(sqrt(2i), -sqrt(2i))", "each component"),
    ("(3-2*i, -3+2*i)", "(sqrt(5-12i), -sqrt(5-12i))", "each component"),
    ("-8/5-1/5*i", "(2-3i)/(-1+2i)", "a + bi"),
    ("1", "((-1-sqrt(3)i)/2)^3", "simplify"),
    ("(6, -12, 15)", "(3*2, 3*(-4), 3*5)", "each component"),
    ("(2+i, 1+i)", "((3+4i)/(2+i), (1+3i)/(2+i))", "each component"),
    ("7-2*i", "3+4-2i", "a + bi"),
    ("(3, 3)", "(1+2, 3)", "each component"),
    ("11-10*i", "3-12i+2i-8i^2", "a + bi"),
    ("5/2-3*i/2", "(4+i)(1-i)/2", "a + bi"),
    ("1/2", "2/4", "reduce fractions"),
    ("2*sqrt(2)", "sqrt(8)", "take factors out of a root"),
    ("sqrt(6)", "sqrt(2)*sqrt(3)", "combine roots in the same term"),
    ("pi/4", "atan(1)", "simplify"),
    # radicals: a root index that shrinks or a factor that can be pulled out is not reduced
    ("sqrt(2)", "\\sqrt[4]{4}", "simplify"),
    ("2*2^(1/3)", "∛16", "take factors out of a root"),
    ("2*2^(1/3)", "4^(2/3)", "simplify"),
    ("sqrt(6)", "36^(1/4)", "simplify"),
    ("2*3^(2/3)", "∛72", "simplify"),
    ("3*sqrt(3)", "9^(3/4)", "simplify"),
    ("2^(2/3)", "2^(1/3)*2^(1/3)", "simplify"),
    # QA (lesson v2, S2d): a root that is the only thing left to simplify is named, not "to the form a + bi"
    ("sqrt(2)/2", "sqrt(1/2)", "now simplify the roots: no fraction inside a root (√(1/2) = √2/2)"),
    ("sqrt(2)/2", "√½", "no fraction inside a root"),
    ("sqrt(2)/2", "\\sqrt{\\frac12}", "no fraction inside a root"),
    ("sqrt(6)/2", "sqrt(3/2)", "no fraction inside a root"),
    ("sqrt(6)/2", "sqrt(1.5)", "no fraction inside a root"),
    ("sqrt(6)/2", "sqrt(3)/sqrt(2)", "combine roots in the same term into one (√3/√2 = √6/2)"),
    ("sqrt(2)/2+sqrt(2)/2*i", "sqrt(1/2)+sqrt(1/2)i", "Same value — now simplify the roots: no fraction inside a root"),
    ("sqrt(2)/2+sqrt(2)/2*i", "√(1/2) + √(1/2) i", "no fraction inside a root"),
    ("sqrt(6)/2-sqrt(2)/2*i", "sqrt(3)/sqrt(2) - i/sqrt(2)", "combine roots in the same term"),
    ("{sqrt(2)/2+sqrt(2)/2*i, -sqrt(2)/2-sqrt(2)/2*i}", "sqrt(1/2)+sqrt(1/2)i, -sqrt(1/2)-sqrt(1/2)i",
     "Same values — now simplify the roots in each component: no fraction inside a root"),
    ("{sqrt(2)/2+sqrt(2)/2*i, -sqrt(2)/2-sqrt(2)/2*i}", "√½+√½i, -√½-√½i", "roots in each component: no fraction inside"),
    ("{sqrt(6)/2+sqrt(2)/2*i, -sqrt(6)/2-sqrt(2)/2*i}", "sqrt(3/2)+sqrt(1/2)i, -sqrt(3/2)-sqrt(1/2)i", "no fraction inside a root"),
    ("{sqrt(6)/2+sqrt(2)/2*i, -sqrt(6)/2-sqrt(2)/2*i}", "sqrt(3)/sqrt(2)+1/sqrt(2)i, -sqrt(3)/sqrt(2)-1/sqrt(2)i",
     "in each component: combine roots in the same term"),
    ("(sqrt(2), sqrt(2)/2)", "(sqrt(8)/2, sqrt(1/2))", "in each component: no fraction inside a root (√(1/2) = √2/2); take factors"),
    # ... while a form that is not a + bi yet keeps that message, roots or not
    ("sqrt(2)/2+sqrt(2)/2*i", "(1+i)/sqrt(2)", "a + bi"),
    ("sqrt(2)/2+sqrt(2)/2*i", "sqrt(1/2)(1+i)", "a + bi"),
    ("{sqrt(2)/2+sqrt(2)/2*i, -sqrt(2)/2-sqrt(2)/2*i}", "(1+i)/sqrt(2), -(1+i)/sqrt(2)", "each component to the form a + bi"),
    ("1", "sqrt(1/4)*2", "simplify it fully"),  # a rational root is not "a fraction inside a root" to keep
    ("sqrt(2)", "sqrt(2.0)", "simplify it fully"),  # nor is a whole number typed with a decimal point
    # QA: arithmetic on lists (the question's own expression) gets the simplify message, not a parse error
    ("(-1, 25, -14)", "(-5, 5, 2)+4(1, 5, -4)", "each component"),
    ("(4, 6)", "[1, 2] + [3, 4]", "each component"),
    ("(1/2, 1)", "(1, 2)/2", "each component"),
    # a product with -1 left to evaluate (lesson v2 inline check "what does 6i^2 become?")
    ("-6", "6(-1)", "simplify"),
    ("-6", "6*(-1)", "simplify"),
    ("-6", "-1*6", "simplify"),
    ("-2.5", "2.5*(-1)", "simplify"),
    ("(-6, 2)", "(6(-1), 2)", "each component"),
]


@pytest.mark.parametrize("answer,response,hint", UNSIMPLIFIED)
def test_unsimplified_constant_is_invalid_not_wrong(answer: str, response: str, hint: str) -> None:
    result = grade(answer, response)
    assert not result.correct and result.invalid, (answer, response, result.feedback)
    assert result.feedback.startswith("Same value") and hint in result.feedback, result.feedback
    assert grade(answer, response, require_simplified=False).correct  # the pipeline's verification still agrees


@pytest.mark.parametrize("response", ["((2+i)(2-i))^3", "5^3", "100+25"])
def test_unsimplified_numeric_is_invalid(response: str) -> None:
    result = grade("125", response, kind="numeric")
    assert result.invalid and not result.correct, result.feedback


REDUCED = [
    ("6+8*i", "6+8i"),
    ("6+8*i", "8i+6"),
    ("1/5-2*i/5", "1/5-2i/5"),
    ("1/5-2*i/5", "0.2-0.4i"),
    ("1/5-2*i/5", "(1-2i)/5"),
    ("-1/2 + sqrt(3)/2*i", "\\frac{-1+\\sqrt{3}i}{2}"),
    ("sqrt(2)/2-sqrt(2)/2*i", "sqrt(2)/2-sqrt(2)/2*i"),
    ("sqrt(2)/2", "1/sqrt(2)"),
    # policy: a root left in a denominator is reduced (rationalising it is not required), both ways round
    ("sqrt(2)/2+sqrt(2)/2*i", "1/sqrt(2)+1/sqrt(2)*i"),
    ("sqrt(2)/2+sqrt(2)/2*i", "1/√2+i/√2"),
    ("sqrt(2)/2+sqrt(2)/2*i", "\\frac{1}{\\sqrt{2}}+\\frac{1}{\\sqrt{2}}i"),
    ("sqrt(2)/2+sqrt(2)/2*i", "1/sqrt2 + 1/sqrt2 i"),
    ("{sqrt(2)/2+sqrt(2)/2*i, -sqrt(2)/2-sqrt(2)/2*i}", "1/sqrt(2)+i/sqrt(2), -1/sqrt(2)-i/sqrt(2)"),
    ("{sqrt(6)/2+sqrt(2)/2*i, -sqrt(6)/2-sqrt(2)/2*i}", "3/sqrt(6)+i/sqrt(2), -3/sqrt(6)-i/sqrt(2)"),
    ("2*sqrt(3)/3", "2/sqrt(3)"),
    ("1/sqrt(2)", "sqrt(2)/2"),
    ("1/sqrt(2)+i/sqrt(2)", "sqrt(2)/2+sqrt(2)/2 i"),
    ("{1/sqrt(2)+i/sqrt(2), -1/sqrt(2)-i/sqrt(2)}", "√2/2+√2/2i, -√2/2-√2/2i"),
    ("(sqrt(2), sqrt(2)/2)", "(sqrt(2), 1/sqrt(2))"),
    ("1+sqrt(2)", "sqrt(2)+1"),
    ("29", "29"),
    ("3/2", "1.5"),
    ("-3.5", "-7/2"),
    ("pi/4", "π/4"),
    ("log(2)", "ln 2"),
    ("2^(1/3)", "∛2"),
    ("(6, -12, 15)", "(6, -12, 15)"),
    ("{1+i, 1-i}", "{1-i, 1+i}"),
    ("e^(i*pi/4)", "sqrt(2)/2 + sqrt(2)/2 i"),  # the key itself is not reduced: any equivalent form is fine
    ("exp(i*pi/2)", "i"),
    # QA: SymPy moves an irreducible radical to a smaller base (4^(1/3) -> 2^(2/3)); the textbook form is reduced
    ("2^(2/3)", "∛4"),
    ("2^(2/3)", "\\sqrt[3]{4}"),
    ("2^(2/3)", "4^(1/3)"),
    ("2^(2/3)", "cbrt(4)"),
    ("3^(2/3)", "∛9"),
    ("5^(2/3)", "∛25"),
    ("2^(3/4)", "\\sqrt[4]{8}"),
    ("2^(3/4)", "8^(1/4)"),
    ("1/2^(2/3)", "1/∛4"),
    ("12^(1/3)", "∛12"),
    # a typed minus sign is not a product with -1
    ("-6", "-6"),
    ("-6", "-(6)"),
    ("-1", "-1"),
    ("-3/4", "-3/4"),
    ("-2*sqrt(3)", "-2√3"),
    ("-6*i", "-6i"),
    ("-3-6*i", "-3-6i"),
]


@pytest.mark.parametrize("answer,response", REDUCED)
def test_reduced_forms_are_accepted(answer: str, response: str) -> None:
    result = grade(answer, response)
    assert result.correct and not result.invalid, (answer, response, result.feedback)


# ---------------------------------------------------------------------------
# QA regressions: notation that used to be misread
# ---------------------------------------------------------------------------

READS = [
    # √, \sqrt, \bar, |…| right after a letter
    ("(-1+sqrt(3)*i)/2", "(-1+i√3)/2"),
    ("sqrt(3)*i", "i√3"),
    ("2*sqrt(3)*i", "2i√3"),
    ("x*sqrt(2)", "x√2"),
    ("sqrt(3)*i", "i\\sqrt{3}"),
    ("a*sqrt(b)", "a\\sqrt{b}"),
    ("(1+sqrt(3)*i)/2", "\\frac{1+i\\sqrt{3}}{2}"),
    ("|z|^2", "z\\bar{z}"),
    ("|z|^2", "z\\overline{z}"),
    ("x*abs(y)", "x|y|"),
    ("sqrt(3)*i", "isqrt(3)"),
    # QA: a root of a number typed without parentheses is that root, like √2 ('sqrt2 i' is not sqrt(2i))
    ("sqrt(2)", "sqrt2"),
    ("sqrt(2)*i", "sqrt2 i"),
    ("sqrt(2)*i", "sqrt 2 i"),
    ("sqrt(2)/2*i", "i sqrt 2/2"),
    ("2*sqrt(2)*i", "2sqrt2 i"),
    ("x*sqrt(2)", "xsqrt2"),
    ("2^(1/3)*i", "cbrt2 i"),
    ("sqrt(x)", "sqrt x"),
    # vulgar fractions are one number: √½ is √(1/2), 2½ is 2 + 1/2
    ("sqrt(2)/2", "1/√2"),
    ("1/2", "½"),
    ("-5/2", "-2½"),
    ("5/2", "2 ½"),
    ("3*x/4", "¾x"),
    ("x*abs(y)", "xabs(y)"),
    ("asin(x)", "arcsin(x)"),
    ("sinh(x)", "sinh(x)"),
    # ordinary exponentials
    ("e^(i*pi/4)", "e^(i pi/4)"),
    ("exp(-x^2/2)", "e^(-x^2/2)"),
    ("exp(x^2)", "e^(x^2)"),
    ("(x^2+y^2)^(3/2)", "(x^2+y^2)^(3/2)"),
    ("e^(-k*t^2)", "e^(-kt^2)"),
    ("e^(2*pi*i*k/n)", "e^(2 pi i k/n)"),
    ("x^(1/1000000)", "x^(1/1000000)"),
    ("exp(i*pi/2)", "i"),
    ("e^(i pi/4)", "sqrt(2)/2 + i sqrt(2)/2"),
    # subscripts
    ("a_1*v_1+a_2*v_2", "a_1v_1+a_2v_2"),
    ("a_1*v_1+a_2*v_2", "a_{1}v_{1}+a_{2}v_{2}"),
    ("a_1*v_1+a_2*v_2", "a1 v1 + a2 v2"),
    ("x_1*x_2", "x_1x_2"),
    ("x_1+x_2", "x₁+x₂"),
    ("2*x_1+x_2", "2x1+x2"),
    # glued function names, Greek names, polar form, capitalized names
    ("sin(x)", "sinx"),
    ("ln(x)", "lnx"),
    ("x*sin(x)", "xsin(x)"),
    ("r*cos(t)", "rcos(t)"),
    ("r*cos(t)", "rcos t"),
    ("(x+i)^2", "x^2-1+2xi"),
    ("r*e^(i*t)", "re^(it)"),
    ("r*e^(i*theta)", "re^(iθ)"),
    ("cos(theta)+i*sin(theta)", "cos θ + isinθ"),
    ("cos(theta)+i*sin(theta)", "costheta + isintheta"),
    ("sqrt(2)", "Sqrt(2)"),
    ("pi", "Pi"),
    ("sin(x)", "Sin(x)"),
    ("x*y", "xy"),
    ("2*lamda", "2λ"),
    ("2*xi", "2ξ"),
    ("2*xi", "2xi"),  # the key uses ξ, so the learner's 'xi' is ξ too
    ("sin(x)^2", "sin^2(x)"),
    ("re(z)", "re(z)"),
    ("re(z)", "Re(z)"),
    # QA: re / im / arg applied without parentheses are functions ('re^(it)' above is still r·e^(it))
    ("arg(z)", "arg z"),
    ("arg(z)", "Arg z"),
    ("arg(z)", "\\arg z"),
    ("re(z)", "re z"),
    ("re(z)", "\\Re z"),
    ("im(z)", "im z"),
    ("im(z)", "\\Im z"),
    ("arg(z_1)+arg(z_2)", "arg z_1 + arg z_2"),
    ("abs(z)*cos(arg(z))", "|z|cos(arg z)"),
    ("abs(z)*exp(i*arg(z))", "|z|e^(i arg z)"),
    ("abs(z)*exp(i*arg(z))", "|z|e^{i\\arg z}"),
    # ... and on the answer-key side
    ("\\Re z", "re(z)"),
    ("\\arg z", "arg(z)"),
    ("\\Im(z)", "im z"),
]


@pytest.mark.parametrize("answer,response", READS)
def test_notation_is_read_as_intended(answer: str, response: str) -> None:
    result = grade(answer, response)
    assert result.correct and not result.invalid, (answer, response, result.feedback)


def test_trailing_re_is_still_r_times_e() -> None:
    assert grade("r*e", "re").correct
    assert not grade("re(z)", "re").correct


@pytest.mark.parametrize(
    "response,expected",
    [("(1, 2)(3, 4)", "(3, 8)"), ("1 + (2, 3)", "(3, 4)"), ("|(1, 2)|", "(1, 2)"), ("(1, 2) + (1, 1, 1)", "(2, 3)")],
)
def test_other_list_arithmetic_asks_for_the_resulting_list(response: str, expected: str) -> None:
    result = grade(expected, response)
    assert result.invalid and not result.correct
    assert result.feedback.startswith(grading.LIST_ARITHMETIC), result.feedback


def test_list_arithmetic_is_graded_by_value() -> None:
    wrong = grade("(-1, 25, -14)", "(-5, 5, 2)+4(1, 5, 4)")
    assert not wrong.correct and not wrong.invalid and "Component 3" in wrong.feedback
    assert grade("(2*a, 2*b)", "a(2, 0) + b(0, 2)").correct
    assert grade("(-1, 25, -14)", "(-5, 5, 2)+4(1, 5, -4)", require_simplified=False).correct
    assert "Unexpected character" in grade("2", "root(8, 3)").feedback  # a function's arguments are not a list


def test_indexed_word_matching_is_linear() -> None:
    """QA: the old pattern (?:[A-Za-z]+(?:_?\\d+)?)+ backtracked exponentially on a letter run ending in '_'."""
    for token in ("a" * 60 + "_", "ab" * 30 + "_", "a1" * 30 + "_"):
        start = time.perf_counter()
        assert grading._INDEXED_WORD.fullmatch(token) is None
        assert time.perf_counter() - start < 0.05
    start = time.perf_counter()
    normalize("x" * 60 + "_")
    assert time.perf_counter() - start < 0.05
    for ok in ("a_1v_1", "x1x2", "theta1", "ab1", "sin2x", "x_12y_3", "sqrt2"):
        assert grading._INDEXED_WORD.fullmatch(ok), ok
    for bad in ("x_1_2", "v_k", "x_", "x1_", "x__1", "_x"):
        assert not grading._INDEXED_WORD.fullmatch(bad), bad


def test_still_wrong_after_canonicalization() -> None:
    assert not grade("x_1*x_2", "x_12").correct
    assert not grade("x_1*x_2", "x_12").invalid
    assert not grade("2*x*i", "2ξ").correct  # an explicit ξ stays ξ


@pytest.mark.parametrize("key", ["e^(i pi/4)", "exp(-x**2/2)", "(x^2+y^2)^(3/2)", "e^(-t/2)", "exp(2*pi*i/3)", "x^(1/1000000)"])
def test_exponential_keys_are_checkable(key: str) -> None:
    assert is_checkable("expression", key, None)


@pytest.mark.parametrize(
    "answer,response",
    [("1000", "1,000"), ("1.5", "1,5"), ("1000", "1 000"), ("1000000.5", "1,000,000.5"), ("5", "5 cm"), ("90", "90 deg"), ("9.8", "9.8 m/s^2")],
)
def test_separators_and_units_are_unreadable_not_wrong(answer: str, response: str) -> None:
    result = grade(answer, response, kind="numeric")
    assert result.invalid and not result.correct, result.feedback


def test_tuple_of_decimals_gets_exact_value_feedback() -> None:
    result = grade(
        "(sqrt(2)/2-sqrt(2)/2*i, -sqrt(2)/2+sqrt(2)/2*i)", "(0.70710678-0.70710678i, -0.70710678+0.70710678i)"
    )
    assert not result.correct and not result.invalid
    assert "numerically close, but exact values are expected" in result.feedback


# ---------------------------------------------------------------------------
# Grading never blocks: hard time budget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer,response,kind",
    [
        ("3/4", "cos(" * 8 + "2" + ")" * 8, "numeric"),
        ("1", "sin(" * 11 + "1" + ")" * 11, "expression"),
        ("0.5", "cos(cos(cos(cos(cos(cos(2))))))", "numeric"),
        ("x", "tan(tan(tan(tan(tan(tan(x))))))", "expression"),
    ],
)
def test_function_towers_are_fast(answer: str, response: str, kind: str) -> None:
    start = time.monotonic()
    grade(answer, response, kind=kind)
    assert time.monotonic() - start < 2.0


def test_grading_has_a_hard_time_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    if not grading._use_worker():
        pytest.skip("grading worker processes are disabled here")
    assert grade("x^2+2*x+1", "(x+1)^2").correct  # a live worker
    monkeypatch.setattr(grading, "GRADE_TIMEOUT_SECONDS", 0.0005)
    start = time.monotonic()
    result = grade("x^2+2*x+1", "(x+1)^2 + sin(x)^2 + cos(x)^2 - 1")
    assert result.invalid and not result.correct and "too complex" in result.feedback
    assert time.monotonic() - start < 2.0
    monkeypatch.setattr(grading, "GRADE_TIMEOUT_SECONDS", 5.0)
    assert grade("x^2+2*x+1", "(x+1)^2").correct  # the killed worker was replaced


# ---------------------------------------------------------------------------
# true_false_set: "mark each statement true or false" (one letter per statement, e.g. 'FTT')
# ---------------------------------------------------------------------------


def tf_set(answer: str, response: str | None):
    return check_answer("true_false_set", answer=answer, correct_choice=None, tolerance=None, response=response, choice=None)


@pytest.mark.parametrize(
    "response",
    [
        "FTT", "ftt", "F T T", "f, t, t", "F,T,T", "F/T/T", "F; T; T", "F-T-T", "false true true", "False, True, True",
        "FALSE TRUE TRUE", "falsetruetrue", "1. F 2. T 3. T", "1) F, 2) T, 3) T", "1: false 2: true 3: true", "1F 2T 3T",
        "(1) F (2) T (3) T", "  F T T.  ", "F T T",
    ],
)  # fmt: skip
def test_true_false_set_accepts_every_way_of_writing_the_marks(response: str) -> None:
    result = tf_set("FTT", response)
    assert result.correct and not result.invalid and result.feedback == "Correct."


@pytest.mark.parametrize("answer", ["FTT", "F T T", "f,t,t", "false, true, true", "1. F 2. T 3. T"])
def test_true_false_set_answer_key_is_read_the_same_way(answer: str) -> None:
    assert grading.parse_true_false_set(answer) == "FTT"
    assert tf_set(answer, "FTT").correct


@pytest.mark.parametrize(
    "response",
    ["1 0 1", "101", "yes no yes", "Y N N", "FTX", "F T maybe", "✓ ✗ ✓", "1. F 3. T 2. T", "2. F 3. T 4. T", "1. FT 2. T", "", "   ", None],
)
def test_true_false_set_unreadable_marks_are_invalid_not_wrong(response: str | None) -> None:
    result = tf_set("FTT", response)
    assert result.invalid and not result.correct
    assert result.feedback.startswith("Mark every statement true or false (3 statements).")
    assert "FTT" not in result.feedback and "false" not in result.feedback.replace("true or false", "")


@pytest.mark.parametrize(("response", "marked"), [("FT", 2), ("F T T F", 4), ("T", 1), ("true false true true false", 5)])
def test_true_false_set_wrong_number_of_marks_is_invalid(response: str, marked: int) -> None:
    result = tf_set("FTT", response)
    assert result.invalid and not result.correct
    assert result.feedback == f"Mark every statement true or false (3 statements). You marked {marked}."


def test_true_false_set_feedback_names_the_statements_marked_wrong() -> None:
    assert tf_set("FTT", "TFT").feedback == "Statement 1 is actually false; statement 2 is actually true."
    assert tf_set("FTT", "FTF").feedback == "Statement 3 is actually true."
    assert tf_set("FTT", "TFF").feedback == (
        "Statement 1 is actually false; statement 2 is actually true; statement 3 is actually true."
    )
    one = tf_set("TFTTF", "TFTTT")
    assert not one.correct and not one.invalid and one.feedback == "Statement 5 is actually false."
    assert tf_set("T", "F").feedback == "Statement 1 is actually true."
    singular = tf_set("T", "TF")
    assert singular.invalid and singular.feedback == "Mark every statement true or false (1 statement). You marked 2."


def test_true_false_set_unreadable_key_does_not_blame_the_learner() -> None:
    result = tf_set("3", "TFT")
    assert not result.correct and not result.invalid and "no answer key" in result.feedback
    assert grading.true_false_set_request("") == "Mark every statement true or false."


def test_true_false_set_is_checkable_and_displayed() -> None:
    assert is_checkable("true_false_set", "FTT", None)
    assert is_checkable("true_false_set", "F T T", None, ["a", "b", "c"])
    assert not is_checkable("true_false_set", "FTT", None, ["a", "b"])  # one mark per statement
    assert not is_checkable("true_false_set", "", None)
    assert not is_checkable("true_false_set", "3", None)
    assert not is_checkable("true_false_set", "1 0 1", None)
    assert grading.true_false_set_display("FTT") == "1 False · 2 True · 3 True"
    assert grading.true_false_set_display("not a key") == "not a key"
    assert grading.true_false_set_request("TFTF") == "Mark every statement true or false (4 statements)."


def test_true_false_set_does_not_change_the_other_kinds() -> None:
    assert not check_answer("true_false", answer="", correct_choice=0, tolerance=None, response="T", choice=None).correct
    assert check_answer("numeric", answer="2", correct_choice=None, tolerance=None, response="2", choice=None).correct


# ---------------------------------------------------------------------------
# A complex number as a + bi vs (real, imaginary): a form slip, asked again — never graded wrong
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer,response,message",
    [
        ("(1/5, 2/5)", "1/5+2/5i", grading.PAIR_EXPECTED),
        ("(1, 0)", "1+0i", grading.PAIR_EXPECTED),
        ("(4, -3)", "4+3i", grading.PAIR_EXPECTED),  # wrong value: same message, so it can't be used as an oracle
        ("(1/5, 2/5)", "7", grading.PAIR_EXPECTED),
        ("[[1, 2]]", "1+2i", grading.PAIR_EXPECTED),
        ("3/13+2*i/13", "(3/13, 2/13)", grading.SINGLE_EXPECTED),
        ("3/13+2*i/13", "(3/13, -2/13)", grading.SINGLE_EXPECTED),
        ("5", "(5, 0)", grading.SINGLE_EXPECTED),
    ],
)
def test_pair_vs_single_value_is_invalid_not_wrong(answer: str, response: str, message: str) -> None:
    result = grade(answer, response)
    assert result.invalid and not result.correct, result.feedback
    assert result.feedback == message


def test_pair_messages_never_say_whether_the_value_is_right() -> None:
    assert grade("(1/5, 2/5)", "1/5+2/5i").feedback == grade("(1/5, 2/5)", "9+9i").feedback
    assert grade("3/13+2*i/13", "(3/13, 2/13)").feedback == grade("3/13+2*i/13", "(1, 1)").feedback


def test_other_shape_mismatches_stay_wrong_with_the_right_example() -> None:
    three = grade("(1, -2, 3)", "6")
    assert not three.correct and not three.invalid and "e.g. (a, b, c)" in three.feedback
    four = grade("(1, 2, 3, 4)", "6")
    assert not four.invalid and "Expected 4 components, e.g. (a, b, c, d)" in four.feedback
    assert not grade("1+2*i", "(1, 2, 3)").invalid  # three components for one value: wrong, as before
    assert grade("(1/5, 2/5)", "(1/5, 2/5)").correct and grade("1/5+2*i/5", "1/5+2/5i").correct
    assert grade("1", "1+0i").correct


@pytest.mark.parametrize("typed", ["(h+k+m, j+ell+n)", "(h+k+m, j+\\ell+n)", "(h+k+m, j+ℓ+n)", "(h+k+m, j+l+n)"])
def test_ell_is_the_variable_l(typed):
    """ℓ typed as 'ell' or '\\ell' is the variable l (verifier comparison 2026-10-06: 'ell' was read as e·l·l)."""
    result = grading.check_answer(
        "expression", answer="(h+k+m, j+l+n)", correct_choice=None, tolerance=None, response=typed, choice=None,
        require_simplified=False,
    )
    assert result.correct


def test_explicit_e_l_l_product_is_not_ell():
    result = grading.check_answer(
        "expression", answer="(h+k+m, j+l+n)", correct_choice=None, tolerance=None, response="(h+k+m, j+e*l*l+n)",
        choice=None, require_simplified=False,
    )
    assert not result.correct
