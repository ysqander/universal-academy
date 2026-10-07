"""Regenerate an existing course's lessons, visuals and/or verification in place, keeping the learner's progress.

    python scripts/regenerate.py --course <course_id> [--skills S1,S4]
                                 [--only lessons,visuals,verify] [--dry-run] [--yes] [--no-backup] [--force]

Use it after prompt or pipeline fixes to refresh a course that is already being studied:
- lessons: each selected lesson is rewritten with the current prompts; the new version replaces the old one only
  if it validates (otherwise the old lesson stays). Questions the learner answered are retired (kept as flagged
  history), never deleted; an active task on a regenerated skill is abandoned when its new lesson is stored.
  Regenerated lessons also get a new visual and verification.
- visuals / verify: redo only the visual plans / the independent verification of machine-checkable questions
  (learner flags are never cleared). Questions stay servable while they are re-checked, and a visual is replaced
  only by a successful new plan.
Skill status, FSRS cards and XP are untouched. A consistent copy of the database is written to
<data-dir>/backups/ first (unless --no-backup). Costs real API calls unless UA_FAKE_LLM=1.

This is a maintenance tool: stop the app server first. It refuses to run while the app answers on
http://127.0.0.1:8000/api/health (pass --force if that server uses another data dir). Ctrl-C is safe: every lesson
keeps its old or its new version and the job is marked interrupted (Resume in the app finishes visuals/verification).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PARTS = ("lessons", "visuals", "verify")
SERVER_HEALTH_URL = "http://127.0.0.1:8000/api/health"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, help="UA_DATA_DIR holding academy.sqlite3 (default: the app's data dir)")
    p.add_argument("--course", help="course id (omit to list the courses in the data dir)")
    p.add_argument("--skills", default="", help="comma-separated skill keys (default: every skill)")
    p.add_argument("--only", default=",".join(PARTS), help="comma-separated parts: lessons,visuals,verify")
    p.add_argument("--dry-run", action="store_true", help="show what would be regenerated and the current quality")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--no-backup", action="store_true", help="skip the database backup")
    p.add_argument("--force", action="store_true", help="run even though the app server answers (another data dir)")
    p.add_argument("--server-url", default=SERVER_HEALTH_URL, help="health URL of the app server (default: %(default)s)")
    return p.parse_args()


args = parse_args()
if args.data_dir:
    os.environ["UA_DATA_DIR"] = str(args.data_dir.resolve())

from app import db  # noqa: E402  (UA_DATA_DIR must be set before the app is imported)
from app.config import settings  # noqa: E402
from app.pipeline import jobs  # noqa: E402
from app.pipeline.lessons import is_proof_skill, kp_targets  # noqa: E402
from app.pipeline.questions import visible_length  # noqa: E402
from app.pipeline.regenerate import regenerate  # noqa: E402
from app.views import lesson_view  # noqa: E402


def list_courses() -> None:
    rows = db.query(
        "SELECT c.id, c.title, c.status, b.title AS book, (SELECT COUNT(*) FROM skills s WHERE s.course_id = c.id) AS n "
        "FROM courses c JOIN books b ON b.id = c.book_id ORDER BY c.created_at"
    )
    if not rows:
        print("No courses in", settings.data_dir)
    for r in rows:
        print(f"{r['id']}  [{r['status']}] {r['title']} — {r['book']} ({r['n']} skills)")


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
        src.backup(dst)  # consistent even while the server writes (WAL)
    finally:
        dst.close()
        src.close()
    return target


def quality(course_id: str, keys: list[str]) -> None:
    """A compact check of the content rules the prompts now enforce."""
    skills = [s for s in db.query("SELECT * FROM skills WHERE course_id = ? ORDER BY order_index", (course_id,))]
    skills = [s for s in skills if not keys or s["key"] in keys]
    print(f"{'skill':<5} {'kind':<14} {'lesson':<7} {'Qs':>3} {'MC%':>4} {'longest✓':>8} {'targets':<12} {'visual':<15} refs / figures")
    for s in skills:
        rows = db.query("SELECT * FROM questions WHERE skill_id = ? AND flagged = 0 ORDER BY rowid", (s["id"],))
        mc = [r for r in rows if r["kind"] == "multiple_choice"]
        longest = 0
        for r in mc:
            lengths = [visible_length(c) for c in db.loads(r["choices_json"], [])]
            cc = r["correct_choice"]
            if cc is not None and 0 <= cc < len(lengths) and all(lengths[cc] > n for i, n in enumerate(lengths) if i != cc):
                longest += 1
        # questions served per knowledge point (legacy: 2), + the multistep capstone, and legacy written questions
        targets = "/".join(map(str, kp_targets(db.loads(s["lesson_json"], None) or {}))) or "-"
        targets += "+cap" if any(r.get("role") == "capstone" for r in rows) else ""
        fr = sum(1 for r in rows if r["kind"] == "free_response")
        targets += f" FR{fr}" if fr else ""
        viz = db.loads(s["viz_json"], None) or {}
        lesson = lesson_view(s["id"])
        refs = ", ".join(lesson.source_refs) if lesson else "-"
        figs = ", ".join(f.id for f in lesson.figures) if lesson else "-"
        share = f"{100 * len(mc) // len(rows)}" if rows else "-"
        marker = " (proof)" if is_proof_skill(s) else ""
        print(
            f"{s['key']:<5} {s['kind'][:14]:<14} {s['lesson_status']:<7} {len(rows):>3} {share:>4} "
            f"{f'{longest}/{len(mc)}':>8} {targets + marker:<12} {viz.get('format', '-'):<15} {refs} / {figs or '—'}"
        )


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
    keys = [k.strip() for k in args.skills.split(",") if k.strip()]
    parts = {p.strip() for p in args.only.split(",") if p.strip()}
    if not parts or parts - set(PARTS):
        print(f"--only takes a subset of {','.join(PARTS)}")
        return 2
    job = jobs.get_job(args.course)
    if job and job.state in ("running", "queued"):
        print(f"The course's build job is {job.state}; let it finish (or restart the server) first.")
        return 1
    skills = db.query("SELECT id, key FROM skills WHERE course_id = ? ORDER BY order_index", (args.course,))
    ids = [s["id"] for s in skills if not keys or s["key"] in keys]
    marks = ",".join("?" * len(ids)) or "''"
    attempts = db.query_one(f"SELECT COUNT(*) AS n FROM attempts WHERE skill_id IN ({marks})", tuple(ids))["n"]
    active = db.query_one(
        f"SELECT COUNT(*) AS n FROM tasks WHERE status = 'active' AND skill_id IN ({marks})", tuple(ids)
    )["n"]
    print(f"course {course['id']} · {course['title']} · {course['status']}")
    print(f"regenerate {', '.join(sorted(parts))} for {', '.join(keys) if keys else f'all {len(skills)} skills'}")
    print(f"learner data on these skills: {attempts} attempts (kept), {active} active task(s) (abandoned when their lesson is replaced)")
    print("\nCurrent content:")
    quality(args.course, keys)
    if args.dry_run:
        return 0
    if not args.force and server_answers(args.server_url):
        print(
            f"\nThe app server is running ({args.server_url} answers). Stop it first: this tool rewrites lessons and "
            "questions the app may be serving. (Use --force only if that server uses a different data dir.)"
        )
        return 1
    if not args.yes and input("\nProceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return 1
    if not args.no_backup:
        print("Backup:", backup())
    report = await regenerate(
        args.course, keys or None, lessons="lessons" in parts, visuals="visuals" in parts, verify="verify" in parts
    )
    print(f"\nRegenerated lessons: {', '.join(report.lessons_regenerated) or '—'}")
    if report.lessons_kept:
        print(f"Kept the old lesson (regeneration failed): {', '.join(report.lessons_kept)}")
    print(f"Abandoned active tasks: {report.abandoned_tasks} · cost ${report.cost_usd:.2f}")
    statuses = Counter(
        db.loads(r["verify_json"], {}).get("status", "unverified")
        for r in db.query(
            f"SELECT verify_json FROM questions WHERE skill_id IN ({marks}) AND flagged = 0 AND kind != 'free_response'",
            tuple(ids),
        )
    )
    print(f"Verification of servable questions: {dict(statuses)}")
    print("\nNew content:")
    quality(args.course, keys)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:  # Ctrl-C cancels regenerate(), which records the interruption in the job row
        print(
            "\nInterrupted. Every lesson keeps its old or its new version and its questions stay servable. "
            "Resume the course in the app (or run this again) to finish visuals and verification."
        )
        sys.exit(130)
