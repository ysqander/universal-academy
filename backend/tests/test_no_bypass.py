"""No bypassing the exercise (the learner, 2026-10-08: "Make the student do proper work and make it not possible to
bypass the intended exercise. this is not just for proofs"). The cases come from a bypass audit of the real course:
every expression printed in a question was typed back as the answer, and 18 of 411 typed answers passed."""

from __future__ import annotations

from app.learning.grading import LIST_NOT_COMPUTED, SYMBOL_NOT_SIMPLIFIED, UNCOMPUTED_FEEDBACK, check_answer
from app.llm_schemas import QuestionDraft
from app.pipeline.questions import answer_known_problems, typed_back


def grade(answer: str, response: str, *, learner: bool = True):
    return check_answer("expression", answer=answer, correct_choice=None, choice=None, tolerance=None, response=response,
                        require_simplified=learner)


def question(prompt: str, answer: str) -> QuestionDraft:
    return QuestionDraft(key="k1q1", tier=1, kind="expression", prompt=prompt, choices=[], correct_choice=None,
                         answer=answer, answer_display="", tolerance=None, hints=[], solution="", checklist=[], targets="",
                         source_exercise=None, steps=[])


def test_vector_arithmetic_typed_back_is_not_a_computed_list():
    # S19 k3q1 "compute i(2,-1)" accepted "i(2,-1)": distributing the scalar left reduced-looking components
    for key, typed in (("(2*i, -i)", "i(2,-1)"), ("(2, 7)", "2(1,3)+(0,1)"), ("(a+c, b+d)", "(a,b)+(c,d)")):
        result = grade(key, typed)
        assert not result.correct and result.invalid and result.feedback == LIST_NOT_COMPUTED, typed
    assert grade("(2*i, -i)", "(2i,-i)").correct and grade("(a+c, b+d)", "(c+a, d+b)").correct
    assert grade("(2*i, -i)", "i(2,-1)", learner=False).correct  # the verifier may answer in any equivalent form


def test_an_abstract_simplification_typed_back_is_asked_again():
    # S16 k2q3 "simplify -(-v)" accepted "-(-v)" (the parser folds the double sign)
    for key, typed in (("v", "-(-v)"), ("v", "v+0"), ("2*v", "v+v"), ("-v", "(-1)v")):
        result = grade(key, typed)
        assert not result.correct and result.invalid and result.feedback == SYMBOL_NOT_SIMPLIFIED, typed
    for key, typed in (("v", "v"), ("2*v", "2v"), ("-v", "-v"), ("-a*d", "a(-d)"), ("a*c-b*d", "ac-bd")):
        assert grade(key, typed).correct, typed


def test_a_complex_product_typed_back_is_not_computed_in_any_lesson():
    assert grade("5+i", "(2+3i)(1-i)").invalid  # numbers: the reduced-form rule
    assert grade("2*a-b+(a+2*b)*i", "(a+bi)(2+i)").feedback == UNCOMPUTED_FEEDBACK  # letters: the new rule
    assert grade("2*a-b+(a+2*b)*i", "(2a-b)+(a+2b)i").correct
    # the summands typed back unadded (S4a k2q2: β+λ answered 'c+di+g+hi')
    assert grade("c+g+(d+h)*i", "c+d*i+g+h*i").feedback == UNCOMPUTED_FEEDBACK
    assert grade("c+g+(d+h)*i", "(c+g)+(d+h)i").correct and grade("(a+c)+g+((b+d)+h)*i", "a+c+g+(b+d+h)i").correct


def test_generation_finds_an_answer_the_prompt_lets_the_learner_type_back():
    # S8b k3q3: the prompt states "λ = −1+2i" for the key {−1+2i}
    q = question("For $\\lambda(1,2-i)=(-1+2i,5i)$ the first coordinate forces $\\lambda=-1+2i$. Enter the corrected "
                 "solution set in braces, or enter $\\varnothing$.", "{-1+2*i}")
    assert any("typing $-1+2i$ from its prompt back" in p for p in answer_known_problems(q))
    # a format instruction naming the empty set is not a give-away, nor a computation the grader refuses
    assert typed_back("expression", "EmptySet", ["Enter the set in braces, or enter $\\varnothing$."]) is None
    assert typed_back("expression", "(2*i, -i)", ["To test it, compute $i(2,-1)$."]) is None
    assert typed_back("expression", "5+i", ["Compute $(2+3i)(1-i)$."]) is None
