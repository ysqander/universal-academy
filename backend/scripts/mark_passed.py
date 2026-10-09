"""Record a skill's lesson as passed by the learner's decision — a lesson they worked through but whose scored practice
they don't want to repeat.

    python scripts/mark_passed.py --course <course_id> --skill S4a [--data-dir <dir>] [--yes] [--no-backup] [--force]

It has the effects of a lesson pass, with the weakest pass's numbers: the skill becomes learned with an FSRS card rated
Hard (its first review comes sooner than after a normal pass), the lesson's cooldown ends, the XP is what a pass at the
80 % mark earns, plateau marks for this lesson are cleared and unlocks are recomputed. An active lesson task on the
skill is closed as passed (its result marked "manual"). Prerequisites get no implicit credit — there is no scored
evidence for them. A consistent copy of the database is written to <data-dir>/backups/ first (unless --no-backup).

Stop the app server first: the tool refuses while the app answers on http://127.0.0.1:8000/api/health (--force if
that server uses another data dir).
"""

from __future__ import annotations

import argparse
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
    p.add_argument("--data-dir", type=Path, help="target UA_DATA_DIR (default: the app's data dir)")
    p.add_argument("--course", required=True)
    p.add_argument("--skill", required=True, help="skill key, e.g. S4a")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--no-backup", action="store_true", help="skip the database backup")
    p.add_argument("--force", action="store_true", help="run even though the app server answers (another data dir)")
    p.add_argument("--server-url", default=SERVER_HEALTH_URL, help="health URL of the app server (default: %(default)s)")
    return p.parse_args()


args = parse_args()
if args.data_dir:
    os.environ["UA_DATA_DIR"] = str(args.data_dir.resolve())

from fsrs import Rating  # noqa: E402

from app import db  # noqa: E402  (UA_DATA_DIR must be set before the app is imported)
from app.config import settings  # noqa: E402
from app.learning import engine, scheduler, state, xp  # noqa: E402
from app.pipeline import jobs  # noqa: E402


def server_answers(url: str = SERVER_HEALTH_URL, timeout: float = 1.5) -> bool:
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
    src, dst = sqlite3.connect(settings.db_path), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def main() -> int:
    db.init_db()
    skill = db.query_one("SELECT * FROM skills WHERE course_id = ? AND key = ?", (args.course, args.skill))
    if skill is None:
        print(f"Skill {args.skill} not found in course {args.course} at {settings.data_dir}")
        return 1
    progress = db.query_one("SELECT * FROM progress WHERE skill_id = ?", (skill["id"],))
    if progress["status"] in scheduler.LEARNED:
        print(f"{args.skill} is already {progress['status']} — nothing to do.")
        return 1
    if progress["status"] == "locked":
        print(f"{args.skill} is locked (a prerequisite is not learned yet) — pass that first.")
        return 1
    if not args.force and server_answers(args.server_url):
        print("The app server is running. Stop it first (or pass --force if it uses another data dir).")
        return 1
    if jobs.is_building(args.course):
        print("The course's build job is running; let it finish first.")
        return 1
    tasks = db.query("SELECT * FROM tasks WHERE skill_id = ? AND type = 'lesson' AND status = 'active'", (skill["id"],))
    amount = xp.lesson_xp(skill["est_minutes"], engine.PASS_MARK)
    print(f"Mark {args.skill} · {skill['title']} as passed")
    print(f"  in {settings.data_dir} (course {args.course}); now {progress['status']}")
    print(f"  FSRS rating Hard · {amount} XP · active lesson tasks closed as passed: {len(tasks)}")
    if not args.yes and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return 1
    if not args.no_backup:
        print("Backup:", backup())
    now = datetime.now(UTC)
    _, log_id = scheduler.apply_rating(args.course, skill["id"], Rating.Hard, now)
    db.execute(
        "UPDATE progress SET lesson_passed_at = ?, cooldown_until = NULL, updated_at = ? WHERE skill_id = ?",
        (scheduler.iso(now), scheduler.iso(now), skill["id"]),
    )
    task_id = None
    for task in tasks:
        st = db.loads(task["state_json"], {})
        st["stage"] = "done"
        st["result"] = {"review_log_id": log_id, "manual": True, "rating": int(Rating.Hard), "proof_waived": False}
        task_xp = amount if task_id is None else 0  # one pass, one award
        engine._complete(task, st, "passed", task_xp, now)
        task_id = task_id or task["id"]
    if task_id is None:  # no task to hang the XP on: a closed one stands for the pass
        task_id = db.new_id()
        db.execute(
            "INSERT INTO tasks(id, course_id, skill_id, type, status, phase, reason, state_json, outcome, xp_awarded, "
            "created_at, completed_at) VALUES (?, ?, ?, 'lesson', 'completed', 'done', 'marked passed', ?, 'passed', ?, ?, ?)",
            (task_id, args.course, skill["id"], db.dumps({"stage": "done", "result": {"review_log_id": log_id, "manual": True}}),
             amount, scheduler.iso(now), scheduler.iso(now)),
        )  # fmt: skip
    xp.award(args.course, task_id, amount, now)
    scheduler.clear_remedial(target=skill["id"])
    state.recompute_unlocks(args.course)
    after = db.query_one("SELECT status, card_json FROM progress WHERE skill_id = ?", (skill["id"],))
    card = scheduler.load_card(after["card_json"])
    print(f"Done: {args.skill} is {after['status']}; first review due {card.due.astimezone().strftime('%Y-%m-%d %H:%M')}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
