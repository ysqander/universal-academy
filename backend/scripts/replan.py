"""Replan one skill with lesson generation v2 (experimental; docs/lesson-v2.md, SPEC §4.2b).

    python scripts/replan.py --data-dir ../data --course <course_id> --skill S2
                             [--dry-run] [--no-split] [--plan FILE] [--yes] [--no-backup] [--force]

1. Planner (high effort): a teaching spec for the skill — objective, assumed prerequisites, out of scope, starting
   example, reasoning sequence, success check, representations, practice steps — and the split test. It may split
   an overloaded skill into 2-5 narrower ones (keys S2a, S2b, …) and say which of them every dependent needs.
   The plan is printed and saved to <data-dir>/replans/<course>-<skill>-<timestamp>.json; --dry-run stops here
   (one LLM call, nothing written). --plan FILE applies a saved plan instead of planning again.
2. Apply: a split replaces the skill in one transaction (its tasks, questions, progress and edges go; the new skills
   get edges from the plan, the course order and the drawn edges are recomputed, unlocks are recomputed). A split
   is refused while the skill has answered attempts, an FSRS card or review history — then the skill is kept
   (--no-split behaviour). Kept: the skill's lesson is replaced (answered questions are retired, never deleted).
3. For each (new) skill: a v2 lesson is written, reviewed for missing bridges, revised once when the review finds a
   real problem, reviewed again, stored with the final review, and its practice questions are verified.
A report (plan, block outline, checks, knowledge points, reviews, verification, cost) is printed and saved next to
the plan. A consistent copy of the database is written to <data-dir>/backups/ first (unless --no-backup).

Stop the app server first: this tool refuses to run while the app answers on http://127.0.0.1:8000/api/health
(--force if that server uses another data dir) or while the course's build job runs. Ctrl-C is safe: the split is
atomic, and new skills still without a lesson get one (v1) when the course is resumed in the app.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SERVER_HEALTH_URL = "http://127.0.0.1:8000/api/health"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, help="UA_DATA_DIR holding academy.sqlite3 (default: the app's data dir)")
    p.add_argument("--course", help="course id (omit to list the courses in the data dir)")
    p.add_argument("--skill", help="skill key, e.g. S2 (omit to list the course's skills)")
    p.add_argument("--dry-run", action="store_true", help="plan only: print and save the plan, change nothing")
    p.add_argument("--no-split", action="store_true", help="keep the skill (only rewrite its lesson as v2)")
    p.add_argument("--plan", type=Path, help="apply a plan saved by an earlier run instead of planning again")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--no-backup", action="store_true", help="skip the database backup")
    p.add_argument("--force", action="store_true", help="run even though the app server answers (another data dir)")
    p.add_argument("--server-url", default=SERVER_HEALTH_URL, help="health URL of the app server (default: %(default)s)")
    return p.parse_args()


args = parse_args()
if args.data_dir:
    os.environ["UA_DATA_DIR"] = str(args.data_dir.resolve())

from app import db, llm  # noqa: E402  (UA_DATA_DIR must be set before the app is imported)
from app.config import settings  # noqa: E402
from app.llm_schemas import TeachingPlan  # noqa: E402
from app.pipeline import jobs  # noqa: E402
from app.pipeline.lesson_v2 import describe_plan  # noqa: E402
from app.pipeline.replan import (  # noqa: E402
    PlanResult,
    SplitRefused,
    apply_replan,
    check_plan,
    plan_replan,
    report_lines,
    split_blockers,
)


def server_answers(url: str = SERVER_HEALTH_URL, timeout: float = 1.5) -> bool:
    """True when something answers HTTP on the app's port (any status: a busy or erroring server is still running)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def backup() -> Path:
    target = settings.data_dir / "backups" / f"academy-{datetime.now(UTC).strftime('%Y%m%d-%H%M%SZ')}.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(settings.db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)  # consistent even while another process writes (WAL)
    finally:
        dst.close()
        src.close()
    return target


def list_courses() -> None:
    rows = db.query(
        "SELECT c.id, c.title, c.status, b.title AS book, (SELECT COUNT(*) FROM skills s WHERE s.course_id = c.id) AS n "
        "FROM courses c JOIN books b ON b.id = c.book_id ORDER BY c.created_at"
    )
    if not rows:
        print("No courses in", settings.data_dir)
    for r in rows:
        print(f"{r['id']}  [{r['status']}] {r['title']} — {r['book']} ({r['n']} skills)")


def list_skills(course_id: str) -> None:
    for s in db.query("SELECT key, title, lesson_json FROM skills WHERE course_id = ? ORDER BY order_index", (course_id,)):
        fmt = (db.loads(s["lesson_json"], None) or {}).get("format", "v1") if s["lesson_json"] else "-"
        print(f"  {s['key']:<6} [{fmt}] {s['title']}")


def load_plan(path: Path) -> TeachingPlan:
    data = json.loads(path.read_text())
    return TeachingPlan.model_validate(data.get("plan", data) if isinstance(data, dict) else data)


def save(name: str, content: str) -> Path:
    target = settings.data_dir / "replans" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    db.init_db()
    print(f"data dir: {settings.data_dir} · {'FAKE LLM' if settings.fake_llm else 'live OpenAI calls'}")
    if not args.course:
        list_courses()
        return 0
    course = db.query_one("SELECT * FROM courses WHERE id = ?", (args.course,))
    if course is None:
        print(f"Course {args.course} not found. Courses:")
        list_courses()
        return 1
    skill = db.query_one("SELECT * FROM skills WHERE course_id = ? AND key = ?", (args.course, args.skill or ""))
    if skill is None:
        print(f"{'Skill ' + args.skill + ' not found. ' if args.skill else ''}Skills of {course['title']}:")
        list_skills(args.course)
        return 1 if args.skill else 2
    key = skill["key"]
    if not args.dry_run and not args.force and server_answers(args.server_url):
        print(
            f"The app server is running ({args.server_url} answers). Stop it first: this tool rewrites the graph, "
            "lessons and questions the app may be serving. (Use --force only if that server uses a different data dir.)"
        )
        return 1
    if jobs.is_building(args.course):
        print("The course's build job is running; let it finish (or restart the server) first.")
        return 1
    blockers = split_blockers(skill["id"])
    print(f"course {course['id']} · {course['title']} · skill {key}: {skill['title']}")
    print("learner history on this skill: " + ("; ".join(blockers) if blockers else "none (a split is allowed)"))

    usage = llm.Usage()
    token = llm.current_usage.set(usage)
    try:
        planned: PlanResult
        if args.plan:
            try:
                planned = check_plan(args.course, key, load_plan(args.plan), no_split=args.no_split)
                print(f"Using the saved plan {args.plan}")
            except SplitRefused as e:
                print(f"{e} — planning the skill as kept instead.")
                planned = await plan_replan(args.course, key, no_split=True)
        else:
            print("Planning (one high-effort call)…")
            planned = await plan_replan(args.course, key, no_split=args.no_split)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
        print("\nPLAN")
        print("\n".join(describe_plan(planned.plan, key)))
        if planned.notes:
            print("\nPlan checks:\n" + "\n".join(f"- {n}" for n in planned.notes))
        saved = save(
            f"{args.course}-{key}-{stamp}.json",
            json.dumps(
                {"course": args.course, "skill": key, "created_at": stamp, "plan": planned.plan.model_dump(), "notes": planned.notes},
                ensure_ascii=False,
                indent=2,
            ),
        )
        print(f"\nPlan saved: {saved} · planning cost ${usage.cost_usd:.2f}")
        if args.dry_run:
            return 0
        action = (
            f"split {key} into {len(planned.plan.skills)} skills"
            if planned.plan.decision == "split"
            else f"replace the lesson of {key} with a v2 lesson"
        )
        if not args.yes and input(f"\nApply: {action}? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 1
        if not args.no_backup:
            print("Backup:", backup())
        report = await apply_replan(args.course, key, planned, usage=usage)
    finally:
        llm.current_usage.reset(token)
    text = "\n".join(report_lines(report))
    print("\n" + text)
    print(f"\nReport saved: {save(f'{args.course}-{key}-{stamp}-report.txt', text + chr(10))}")
    return 0 if report.results and all(r.ok for r in report.results) else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:  # Ctrl-C cancels apply_replan(), which records the interruption in the job row
        print(
            "\nInterrupted. The course stays servable: a split is all-or-nothing, a kept skill keeps its old lesson "
            "until the new one is stored, and new skills still without a lesson get one when you resume the course."
        )
        sys.exit(130)
