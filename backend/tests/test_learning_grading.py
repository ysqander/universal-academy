"""SPEC §6.1 grading: SymPy equivalence, normalization, structures, tolerance, near-miss feedback, safety."""

from __future__ import annotations

import time

import pytest

from app.learning.grading import check_answer, is_checkable, normalize


def grade(answer: str, response: str, kind: str = "expression", tolerance: float | None = None):
    return check_answer(kind, answer=answer, correct_choice=None, tolerance=tolerance, response=response, choice=None)


ACCEPT = [
    # complex arithmetic
    ("-7+22*i", "-7+22i"),
    ("-7+22*i", "(2+3i)(4+5i)"),
    ("-7+22*i", "-7 + 22 i"),
    ("-7+22*i", "−7+22i"),
    ("-7+22*i", "-7+22j"),
    ("-i", "1/i"),
    ("(a - b*i)/(a^2+b^2)", "1/(a+bi)"),
    ("(a - b*i)/(a^2+b^2)", "1/(a+b*i)"),
    ("(a - b*i)/(a^2+b^2)", "\\frac{a-bi}{a^2+b^2}"),
    ("-1/2 + sqrt(3)/2*i", "\\frac{-1+\\sqrt{3}i}{2}"),
    ("z*conjugate(z)", "|z|^2"),
    ("conjugate(z)", "\\overline{z}"),
    ("5", "|3+4i|"),
    ("-1", "e^{i\\pi}"),
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
    ("2", "\\sqrt[3]{8}"),
    ("x**2", "x^2"),
    ("x**2", "x²"),
    ("2*x", "2x"),
    ("2*x", "x + x"),
    ("x*y", "yx"),
    ("a*(b+c)", "a(b+c)"),
    ("3*x", "3 \\cdot x"),
    ("6", "2 × 3"),
    ("x**2+2*x+1", "(x+1)^2"),
    ("1", "sin(x)^2 + cos(x)^2"),
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
    assert grade("-7+22*i", "(2+3i)(4+5i)").feedback.startswith("Correct — equivalent")


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
