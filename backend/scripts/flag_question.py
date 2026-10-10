"""Flag a question on the learner's behalf: the app's Flag button, run from the command line (for a question the
learner reported in conversation, or one found faulty after their attempt).

    python scripts/flag_question.py --course <course_id> --skill S10 --question cap --reason "…"
                                    [--data-dir <dir>] [--yes] [--no-backup] [--force]

It runs engine.flag, so the attempt is re-scored exactly as the app re-scores it. The question leaves serving
(flag_reason "learner: …") and its attempts in the task that served it are voided. A finished lesson that failed is
reopened and ends without the question; a flagged capstone is never the learner's failure, so that failure and its
cooldown are taken back. A passed lesson gets the XP of its new score, and a finished review is re-scored. No question
top-up is requested: rewrite the question with scripts/regenerate.py --only capstone,questions --note instead.
A consistent copy of the database is written to <data-dir>/backups/ first (unless --no-backup).

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
    p.add_argument("--skill", required=True, help="skill key, e.g. S10")
    p.add_argument("--question", required=True, help="question key, e.g. cap or k2q1")
    p.add_argument("--reason", required=True, help="what is wrong with it (stored after 'learner: ')")
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
from app.learning import engine  # noqa: E402
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
    q = db.query_one("SELECT * FROM questions WHERE skill_id = ? AND key = ?", (skill["id"], args.question))
    if q is None:
        print(f"Question {args.question} not found in {args.skill} (retired questions are named old-…)")
        return 1
    if q["flagged"]:
        print(f"{args.skill} {args.question} is already flagged: {q['flag_reason']}")
        return 1
    if not args.force and server_answers(args.server_url):
        print("The app server is running. Stop it first (or pass --force if it uses another data dir).")
        return 1
    if jobs.is_building(args.course):
        print("The course's build job is running; let it finish first.")
        return 1
    task = engine._task_serving(q["id"], skill["id"])
    if task is None:
        print(f"{args.skill} {args.question} hasn't been served in a task yet — nothing to re-score.")
        return 1
    print(f"Flag {args.skill} {args.question} ({q['role'] or 'practice'}, {q['kind']}) · {skill['title']}")
    print(f"  in {settings.data_dir} (course {args.course})")
    print(f"  task {task['id']} ({task['type']}, {task['status']}, {task['outcome'] or 'open'}, {task['created_at']})")
    print(f"  reason: {args.reason}")
    if not args.yes and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return 1
    if not args.no_backup:
        print("Backup:", backup())
    engine.request_top_up = lambda *a, **k: None  # rewrite the question with regenerate.py instead
    engine.flag(q["id"], args.reason, datetime.now(UTC))
    after = engine._task_row(task["id"])
    st = db.loads(after["state_json"], {}) or {}
    score = (st.get("result") or {}).get("score", (st.get("fail") or {}).get("score"))
    progress = db.query_one("SELECT status, kp_failures_json, cooldown_until FROM progress WHERE skill_id = ?", (skill["id"],))
    print(f"Done: task {after['status']}, {after['outcome'] or 'open'}"
          + (f", score {engine.score_percent(score)}%" if isinstance(score, (int, float)) else "")
          + f", {after['xp_awarded']} XP · {args.skill} is {progress['status']}"
          + f" (failures {progress['kp_failures_json']}, cooldown {progress['cooldown_until'] or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
