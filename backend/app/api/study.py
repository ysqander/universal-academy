"""Study loop API (SPEC §5, §6): next task menu, task lifecycle, answers, reveal/flag, settings, and the teaching
blocks of v2 lessons (inline checks, "This step isn't clear")."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from ..learning import blocks, engine, selector, stats
from ..schemas import (
    AnswerResult,
    AnswerSubmit,
    BlockCheckRequest,
    BlockCheckResult,
    BlockFeedbackRequest,
    FlagRequest,
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
    return _run(lambda: engine.get_task(tid))


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


@router.post("/api/questions/{qid}/hint", response_model=HintResponse)
def show_hint(qid: str) -> HintResponse:
    return _run(lambda: engine.hint(qid, _now()))


@router.post("/api/questions/{qid}/reveal", response_model=RevealResponse)
def reveal_solution(qid: str, body: RevealRequest | None = None) -> RevealResponse:
    return _run(lambda: engine.reveal(qid, _now(), body.response if body else None))


@router.post("/api/questions/{qid}/flag", response_model=Task)
def flag_question(qid: str, body: FlagRequest) -> Task:
    return _run(lambda: engine.flag(qid, body.reason, _now()))


@router.post("/api/skills/{sid}/blocks/{index}/check", response_model=BlockCheckResult)
def check_block(sid: str, index: int, body: BlockCheckRequest) -> BlockCheckResult:
    """Answer a v2 lesson's inline check (formative: graded, never recorded). 409 when body.version is not the
    stored lesson's version (the lesson was regenerated since the page loaded)."""
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


@router.get("/api/settings", response_model=StudySettings)
def get_settings() -> StudySettings:
    return StudySettings(daily_goal=stats.get_daily_goal())


@router.put("/api/settings", response_model=StudySettings)
def put_settings(body: StudySettings) -> StudySettings:
    value = stats.set_daily_goal(body.daily_goal)
    log.info("daily goal set to %d XP", value)
    return StudySettings(daily_goal=value)
