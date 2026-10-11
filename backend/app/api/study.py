"""Study loop API (SPEC §5, §6): next task menu, task lifecycle, answers, reveal/flag, settings, and the teaching
blocks of v2 lessons (inline checks, "This step isn't clear"). Quizzes (§6.7) use the same task endpoints: POST
/practice starts the clock, answers return status "recorded", hints / reveal / easier / abandon are 409 during a quiz;
a manual start with type 'quiz' starts the due quiz or the optional retake (skill_id is ignored)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, model_validator

from ..learning import blocks, engine, quiz, selector, stats
from ..schemas import (
    AnswerResult,
    AnswerSubmit,
    BlockCheckRequest,
    BlockCheckResult,
    BlockFeedbackRequest,
    FlagRequest,
    GuidedCheckResult,
    GuidedRevealRequest,
    HintResponse,
    NextTaskResponse,
    RevealRequest,
    RevealResponse,
    StartTaskRequest,
    Task,
)

log = logging.getLogger("ua.api.study")

router = APIRouter()
T = TypeVar("T")


class StudySettings(BaseModel):
    daily_goal: int = Field(ge=1, le=1000, description="Daily XP goal (1 XP ≈ 1 focused minute).")
    quiz_time_factor: float = Field(
        default=quiz.DEFAULT_TIME_FACTOR, ge=quiz.MIN_TIME_FACTOR, le=quiz.MAX_TIME_FACTOR,
        description="Multiplies a quiz's time allowance (60 s per choice question, 90 s otherwise).",
    )  # fmt: skip


class StudySettingsUpdate(BaseModel):
    """PUT /api/settings: only the fields sent are changed (at least one)."""

    daily_goal: int | None = Field(default=None, ge=1, le=1000)
    quiz_time_factor: float | None = Field(default=None, ge=quiz.MIN_TIME_FACTOR, le=quiz.MAX_TIME_FACTOR)

    @model_validator(mode="after")
    def _something_to_set(self) -> StudySettingsUpdate:
        if self.daily_goal is None and self.quiz_time_factor is None:
            raise ValueError("Send daily_goal and/or quiz_time_factor.")
        return self


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except engine.StudyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/api/courses/{cid}/next", response_model=NextTaskResponse)
def next_task(cid: str) -> NextTaskResponse:
    return _run(lambda: selector.next_response(cid, _now()))


@router.post("/api/courses/{cid}/continue", response_model=Task)
def continue_learning(cid: str) -> Task:
    return _run(lambda: selector.continue_task(cid, _now()))


@router.post("/api/courses/{cid}/tasks", response_model=Task)
def start_task(cid: str, body: StartTaskRequest) -> Task:
    return _run(
        lambda: selector.start_task(cid, body.skill_id, body.type, _now(), replace_active=body.replace_active, force=body.force)
    )


@router.get("/api/tasks/{tid}", response_model=Task)
def get_task(tid: str) -> Task:
    """A quiz whose time ran out is finished first (its results are then in Task.quiz)."""
    return _run(lambda: engine.get_task(tid, _now()))


@router.post("/api/tasks/{tid}/practice", response_model=Task)
def begin_practice(tid: str) -> Task:
    return _run(lambda: engine.begin_practice(tid, _now()))


@router.post("/api/tasks/{tid}/answer", response_model=AnswerResult)
def submit_answer(tid: str, body: AnswerSubmit) -> AnswerResult:
    return _run(lambda: engine.submit_answer(tid, body, _now()))


@router.post("/api/tasks/{tid}/easier", response_model=Task)
def easier_step(tid: str) -> Task:
    return _run(lambda: engine.easier_step(tid, _now()))


@router.post("/api/tasks/{tid}/abandon", response_model=Task)
def abandon_task(tid: str) -> Task:
    return _run(lambda: engine.abandon(tid, _now()))


@router.post("/api/tasks/{tid}/lesson-look", response_model=Task)
def look_at_lesson(tid: str) -> Task:
    """The learner opens the lesson during a task: a review records it as a hint on the current question."""
    return _run(lambda: engine.lesson_look(tid, _now()))


@router.post("/api/questions/{qid}/hint", response_model=HintResponse)
def show_hint(qid: str, step: int | None = None) -> HintResponse:
    """Next hint for the current attempt (recorded server-side). Multistep: `?step=i` (the current step) returns
    that step's single hint; using it halves the step's point."""
    return _run(lambda: engine.hint(qid, _now(), step))


@router.post("/api/questions/{qid}/reveal", response_model=RevealResponse)
def reveal_solution(qid: str, body: RevealRequest | None = None) -> RevealResponse:
    return _run(lambda: engine.reveal(qid, _now(), body.response if body else None))


@router.post("/api/questions/{qid}/flag", response_model=Task)
def flag_question(qid: str, body: FlagRequest) -> Task:
    return _run(lambda: engine.flag(qid, body.reason, _now()))


@router.post("/api/tasks/{tid}/blocks/{index}/check", response_model=GuidedCheckResult)
def check_guided_step(tid: str, index: int, body: BlockCheckRequest) -> GuidedCheckResult:
    """Answer a guided proof lesson's scored step inside the lesson task (SPEC §6.3b): 1 / ½ on a second try / 0.
    body.version is required (409 when missing or stale); 422 for an unreadable answer (not a try)."""
    return _run(lambda: engine.check_guided(tid, index, body, _now()))


@router.post("/api/tasks/{tid}/blocks/{index}/reveal", response_model=GuidedCheckResult)
def reveal_guided_step(tid: str, index: int, body: GuidedRevealRequest | None = None) -> GuidedCheckResult:
    """Show a guided step's answer: the step is done with 0 points."""
    return _run(lambda: engine.reveal_guided(tid, index, body.version if body else None, _now()))


@router.post("/api/tasks/{tid}/blocks/{index}/flag", response_model=Task)
def flag_guided_step(tid: str, index: int, body: FlagRequest) -> Task:
    """Report a done guided step's answer key as wrong: its points no longer count."""
    return _run(lambda: engine.flag_guided(tid, index, body.reason, _now()))


@router.post("/api/skills/{sid}/blocks/{index}/check", response_model=BlockCheckResult)
def check_block(sid: str, index: int, body: BlockCheckRequest) -> BlockCheckResult:
    """Answer a v2 lesson's inline check (formative: graded, never recorded). 409 when body.version is not the
    stored lesson's version (the lesson was regenerated since the page loaded), and for a guided proof lesson's
    scored step before the skill is learned (it is answered in the lesson task)."""
    return _run(lambda: blocks.check_block(sid, index, body))


@router.post("/api/skills/{sid}/blocks/{index}/feedback", status_code=204, response_class=Response)
def block_feedback(sid: str, index: int, body: BlockFeedbackRequest | None = None) -> Response:
    """'This step isn't clear' for one teaching block (409, nothing stored, when body.version is outdated)."""
    _run(lambda: blocks.record_feedback(sid, index, body or BlockFeedbackRequest(), _now()))
    return Response(status_code=204)


@router.get("/api/skills/{sid}/feedback")
def list_block_feedback(sid: str) -> list[dict[str, Any]]:
    """Block feedback of a skill for later analysis: [{block_index, kind, note, created_at, lesson_hash}]."""
    return _run(lambda: blocks.list_feedback(sid))


def _settings() -> StudySettings:
    return StudySettings(daily_goal=stats.get_daily_goal(), quiz_time_factor=quiz.get_time_factor())


@router.get("/api/settings", response_model=StudySettings)
def get_settings() -> StudySettings:
    return _settings()


@router.put("/api/settings", response_model=StudySettings)
def put_settings(body: StudySettingsUpdate) -> StudySettings:
    """Partial update; the quiz time factor applies to quizzes whose clock has not started yet."""
    if body.daily_goal is not None:
        log.info("daily goal set to %d XP", stats.set_daily_goal(body.daily_goal))
    if body.quiz_time_factor is not None:
        log.info("quiz time factor set to %.2f", quiz.set_time_factor(body.quiz_time_factor))
    return _settings()
