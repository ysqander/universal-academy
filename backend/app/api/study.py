"""Study loop API (SPEC §5, §6): next task menu, task lifecycle, answers, reveal/flag, settings."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..learning import engine, selector, stats
from ..schemas import (
    AnswerResult,
    AnswerSubmit,
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


@router.get("/api/settings", response_model=StudySettings)
def get_settings() -> StudySettings:
    return StudySettings(daily_goal=stats.get_daily_goal())


@router.put("/api/settings", response_model=StudySettings)
def put_settings(body: StudySettings) -> StudySettings:
    value = stats.set_daily_goal(body.daily_goal)
    log.info("daily goal set to %d XP", value)
    return StudySettings(daily_goal=value)
