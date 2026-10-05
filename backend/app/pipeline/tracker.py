"""Build-job bookkeeping: stages, counters and a human-readable log, persisted in the `jobs` table on every change."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .. import db
from ..schemas import JobStage, JobStageKey, JobState, JobStatus, StageStatus

log = logging.getLogger("ua.pipeline")

STAGES: list[tuple[JobStageKey, str]] = [
    ("render", "Render pages"),
    ("parse", "Read pages"),
    ("graph", "Build knowledge graph"),
    ("lessons", "Write lessons"),
    ("visuals", "Plan visuals"),
    ("verify", "Verify answers"),
    ("finalize", "Finalize"),
]
LOG_CAP = 200  # lines kept in the DB
API_LOG_LINES = 50  # lines returned by the API


def fresh_stages() -> list[JobStage]:
    return [JobStage(key=key, label=label, status="pending") for key, label in STAGES]


def status_from_row(row: dict[str, Any]) -> JobStatus:
    return JobStatus(
        course_id=row["course_id"],
        state=row["state"],
        stages=[JobStage(**s) for s in db.loads(row["stages_json"], [])],
        error=row["error"],
        log=db.loads(row["log_json"], [])[-API_LOG_LINES:],
        updated_at=row["updated_at"],
    )


class JobTracker:
    """In-memory view of one job row; every mutation is written through to SQLite."""

    def __init__(self, course_id: str, state: JobState, stages: list[JobStage], lines: list[str], error: str | None):
        self.course_id = course_id
        self.state: JobState = state
        self.stages = {s.key: s for s in stages}
        self.lines = lines
        self.error = error

    @classmethod
    def create(cls, course_id: str) -> JobTracker:
        tracker = cls(course_id, "queued", fresh_stages(), [], None)
        tracker.save()
        return tracker

    @classmethod
    def load(cls, course_id: str) -> JobTracker:
        row = db.query_one("SELECT * FROM jobs WHERE course_id = ?", (course_id,))
        if row is None:
            return cls.create(course_id)
        stages = [JobStage(**s) for s in db.loads(row["stages_json"], [])]
        known = {s.key for s in stages}
        stages += [s for s in fresh_stages() if s.key not in known]
        return cls(course_id, row["state"], stages, db.loads(row["log_json"], []), row["error"])

    def save(self) -> None:
        ordered = [self.stages[key].model_dump() for key, _ in STAGES if key in self.stages]
        db.execute(
            "INSERT INTO jobs(course_id, state, stages_json, log_json, error, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(course_id) DO UPDATE SET state = excluded.state, stages_json = excluded.stages_json, "
            "log_json = excluded.log_json, error = excluded.error, updated_at = excluded.updated_at",
            (self.course_id, self.state, db.dumps(ordered), db.dumps(self.lines[-LOG_CAP:]), self.error, db.now_iso()),
        )

    # -- log -----------------------------------------------------------------

    def log(self, message: str, *, level: int = logging.INFO) -> None:
        log.log(level, "[%s] %s", self.course_id, message)
        self.lines.append(f"{datetime.now().strftime('%H:%M:%S')} {message}")
        del self.lines[:-LOG_CAP]
        self.save()

    # -- state ---------------------------------------------------------------

    def begin_run(self) -> None:
        """Start (or resume) a run: unfinished stages go back to pending, the previous error is cleared."""
        self.state = "running"
        self.error = None
        for stage in self.stages.values():
            if stage.status in ("running", "failed"):
                stage.status = "pending"
        self.save()

    def finish(self, state: JobState, error: str | None = None) -> None:
        self.state = state
        self.error = error
        self.save()

    # -- stages --------------------------------------------------------------

    def stage(self, key: JobStageKey) -> JobStage:
        return self.stages[key]

    def update(
        self,
        key: JobStageKey,
        *,
        status: StageStatus | None = None,
        done: int | None = None,
        total: int | None = None,
        message: str | None = None,
        save: bool = True,
    ) -> None:
        stage = self.stages[key]
        if status is not None:
            stage.status = status
        if done is not None:
            stage.done = done
        if total is not None:
            stage.total = total
        if message is not None:
            stage.message = message
        if save:
            self.save()

    def to_status(self) -> JobStatus:
        row = db.query_one("SELECT * FROM jobs WHERE course_id = ?", (self.course_id,))
        assert row is not None
        return status_from_row(row)
