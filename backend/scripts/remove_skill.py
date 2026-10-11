"""Remove a skill another skill already covers (e.g. one the learner found trivial): the skill, its edges, questions,
progress, tasks, attempts and kept v2 spec. The learner's answers on it go with it.

    python scripts/remove_skill.py --course <course_id> --skill S2c --reason "…" [--data-dir <dir>] [--yes]
                                   [--no-backup] [--force]

Refused when other skills depend on it (rewire them first, or replan the dependents), or when a task of another
skill (a quiz, a review) holds one of its questions. Unlocks are recomputed afterwards. A consistent copy of the
database is written to <data-dir>/backups/ first (unless --no-backup).

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
    p.add_argument("--skill", required=True, help="skill key, e.g. S2c")
    p.add_argument("--reason", required=True, help="why the skill goes (logged in the course's build log)")
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
from app.learning import state  # noqa: E402
from app.pipeline.lesson_v2 import SPEC_KEY  # noqa: E402
from app.pipeline.tracker import JobTracker  # noqa: E402


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
    sid = skill["id"]
    if not args.force and server_answers(args.server_url):
        print("The app server is running. Stop it first (or pass --force if it uses another data dir).")
        return 1
    dependents = db.query("SELECT s.key FROM edges e JOIN skills s ON s.id = e.dst WHERE e.src = ?", (sid,))
    if dependents:
        print(f"{args.skill} has dependents ({', '.join(d['key'] for d in dependents)}): rewire or replan them first.")
        return 1
    qids = [r["id"] for r in db.query("SELECT id FROM questions WHERE skill_id = ?", (sid,))]
    holders = [
        t for t in db.query("SELECT id, type, state_json FROM tasks WHERE course_id = ? AND skill_id IS NOT ?", (args.course, sid))
        if any(q in (t["state_json"] or "") for q in qids)
    ]
    if holders:
        print(f"Tasks of other skills hold {args.skill}'s questions ({', '.join(t['type'] for t in holders)}): not removed.")
        return 1
    counts = {
        t: db.query_one(f"SELECT COUNT(*) AS n FROM {t} WHERE skill_id = ?", (sid,))["n"]
        for t in ("questions", "attempts", "tasks", "progress", "review_log", "block_feedback")
    }
    prereqs = [r["key"] for r in db.query("SELECT s.key FROM edges e JOIN skills s ON s.id = e.src WHERE e.dst = ?", (sid,))]
    print(f"Remove {args.skill} · {skill['title']} (prerequisites: {', '.join(prereqs) or 'none'}) from course {args.course}")
    print("  rows: " + ", ".join(f"{n} {t}" for t, n in counts.items()))
    print(f"  reason: {args.reason}")
    if not args.yes and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return 1
    if not args.no_backup:
        print("Backup:", backup())
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM attempts WHERE skill_id = ? OR task_id IN (SELECT id FROM tasks WHERE skill_id = ?)", (sid, sid))
        for table in ("tasks", "progress", "review_log", "block_feedback", "questions"):
            conn.execute(f"DELETE FROM {table} WHERE skill_id = ?", (sid,))
        conn.execute("DELETE FROM edges WHERE src = ? OR dst = ?", (sid, sid))
        conn.execute("DELETE FROM kv WHERE key = ?", (SPEC_KEY.format(sid),))
        conn.execute("DELETE FROM skills WHERE id = ?", (sid,))
    state.recompute_unlocks(args.course)
    tracker = JobTracker.load(args.course)
    tracker.log(f"Removed {args.skill} ({skill['title']}): {args.reason}")
    tracker.save()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
