"""Verification quality fixes from the verifier-model comparison (2026-10-06):
- a key that contradicts the question's own displayed answer is caught without a model call;
- the independent solver gets the course's notation (e.g. F means R or C) and is told to work inside it;
- the grader reads 'ell' / '\\ell' as the variable ℓ (tested in test_learning_grading.py)."""

from __future__ import annotations

import asyncio

import pytest
from test_pipeline_build import build, llm_calls, tiny_course  # noqa: F401  (fixture)

from app import db, fake_llm
from app.llm_schemas import IndependentAnswer, QuestionDraft
from app.pipeline import verify
from app.pipeline.prompts import VERIFY_INSTRUCTIONS
from app.pipeline.questions import InvalidQuestion, clean_question, display_mismatch


def mismatch(kind: str, answer: str = "", shown: str = "", choices: list[str] | None = None, correct: int | None = None):
    return display_mismatch(kind, answer=answer, answer_display=shown, choices=choices or [], correct_choice=correct, tolerance=None)


@pytest.mark.parametrize(
    ("kind", "answer", "shown"),
    [
        ("expression", "-7+22*i", "$-7+22i$"),
        ("expression", "-x-1+i*(x+2)", "$h(x)=(-x-1)+(x+2)i$."),
        ("numeric", "-3/2", "$\\lambda=-\\frac{3}{2}$"),
        ("expression", "(1, -2, 3)", "$\\left(1,-2,3\\right)$"),
        ("expression", "1+i", "$z=1+i$ or $z=-1-i$"),  # prose: not compared
        ("numeric", "5", "$n=5$ coordinates"),  # prose: not compared
        ("expression", "l*u+l*v", "$\\lambda x_k+\\lambda y_k$"),  # renamed variables for typing: not compared
        ("expression", "{sqrt(2)/2+sqrt(2)/2*i, -sqrt(2)/2-sqrt(2)/2*i}", "$\\pm\\left(\\frac{\\sqrt2}{2}+\\frac{\\sqrt2}{2}i\\right)$"),
        ("true_false_set", "FTT", "1 False · 2 True · 3 True"),
        ("expression", "3", ""),
    ],
)
def test_consistent_or_incomparable_displays_pass(kind, answer, shown):
    assert mismatch(kind, answer, shown) is None


def test_contradicting_displays_are_reported():
    assert "does not equal the key" in mismatch("expression", "-7+22*i", "$-7-22i$")
    assert "does not equal the key" in mismatch("numeric", "-3/2", "$\\lambda=\\frac{3}{2}$")
    assert "does not equal the key" in mismatch("expression", "40*i", "$20i$")
    assert mismatch("true_false_set", "FFT", "1. False; 2. True; 3. True") == "the answer shown reads FTT but the key is FFT"
    assert "the key says False" in mismatch("true_false", shown="True", correct=1)
    assert mismatch("true_false", shown="$\\text{False}$", correct=1) is None
    options = ["$(2,1)$", "$(1,2)$", "$(1,-2)$"]
    assert mismatch("multiple_choice", shown="$(1,2)$", choices=options, correct=0) == (
        "the answer shown is option 1 but the key is option 0"
    )
    assert mismatch("multiple_choice", shown="$(1,2)$", choices=options, correct=1) is None


def _question(**fields) -> QuestionDraft:
    base = dict(
        key="k1q1", tier=1, kind="expression", prompt="Evaluate $(3+i)(2+6i)$. Enter it in the form $a+bi$.",
        choices=[], correct_choice=None, answer="20*i", answer_display="$20i$", tolerance=None,
        hints=["a", "b", "c"], solution="…", checklist=[], targets="t", source_exercise=None, steps=[],
    )
    return QuestionDraft(**(base | fields))


def test_generation_rejects_a_key_that_contradicts_its_display():
    assert clean_question(_question()).answer == "20*i"
    with pytest.raises(InvalidQuestion, match="answer key and displayed answer disagree"):
        clean_question(_question(answer="40*i"))


def _verifiable_row(cid: str) -> dict:
    row = db.query_one(
        "SELECT q.* FROM questions q JOIN skills s ON s.id = q.skill_id WHERE s.course_id = ? AND q.kind = 'expression' "
        "AND q.answer_display != '' LIMIT 1",
        (cid,),
    )
    assert row is not None
    db.execute("UPDATE questions SET verify_json = '{}', flagged = 0, flag_reason = '' WHERE id = ?", (row["id"],))
    return db.query_one("SELECT * FROM questions WHERE id = ?", (row["id"],))


def test_verification_flags_a_key_mismatch_without_calling_a_model(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    build(cid)
    row = _verifiable_row(cid)
    db.execute("UPDATE questions SET answer_display = ? WHERE id = ?", ("$123456+i$", row["id"]))
    row = db.query_one("SELECT * FROM questions WHERE id = ?", (row["id"],))
    calls: list = []
    monkeypatch.setattr(fake_llm, "respond", lambda **kw: calls.append(kw) or pytest.fail("no model call expected"))
    verdict = asyncio.run(verify.verify_question(row))
    assert calls == [] and verdict is not None and verdict.flagged
    stored = db.query_one("SELECT * FROM questions WHERE id = ?", (row["id"],))
    assert stored["flagged"] == 1 and stored["flag_reason"].startswith("verification: answer key and displayed answer")
    assert db.loads(stored["verify_json"])["status"] == "key_mismatch"


def test_the_solver_works_inside_the_course_setting(tmp_path, monkeypatch):
    cid = tiny_course(tmp_path)
    build(cid)
    sid = db.query_one("SELECT id FROM skills WHERE course_id = ? ORDER BY order_index LIMIT 1", (cid,))["id"]
    lesson = db.loads(db.query_one("SELECT lesson_json FROM skills WHERE id = ?", (sid,))["lesson_json"])
    lesson["notation"] = [{"symbol": "$\\mathbf{F}$", "meaning": "Either the real numbers or the complex numbers."}]
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (db.dumps(lesson), sid))
    verify._setting_cache.clear()
    row = _verifiable_row(cid)
    seen: list = []
    original = fake_llm.respond

    def respond(**kwargs):
        if kwargs["schema"] is IndependentAnswer:
            seen.append(kwargs.get("shared_prefix") or "")
        return original(**kwargs)

    monkeypatch.setattr(fake_llm, "respond", respond)
    asyncio.run(verify.verify_question(row))
    assert seen and "COURSE SETTING" in seen[0] and "$\\mathbf{F}$: Either the real numbers or the complex numbers." in seen[0]
    assert "characteristic 2" in VERIFY_INSTRUCTIONS and "course's setting" in VERIFY_INSTRUCTIONS
