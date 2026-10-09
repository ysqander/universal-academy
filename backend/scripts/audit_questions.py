"""Find the course's servable questions whose answer the learner can read instead of work out, and (with --apply) take
them out of serving.

    python scripts/audit_questions.py --course <course_id> [--data-dir <dir>] [--apply] [--yes] [--no-backup] [--force]

The check is generation's own (questions.answer_known_problems via verify.readable_answer): the answer printed in the
question's prompt, typed back from it, or — in a v2 lesson — a line of the lesson. Questions written before that check
existed (2026-10-07) can still have it; a review on 2026-10-09 served "Compute −7+5i as the sum of the real number −7
and the purely imaginary number 5i". Verification now runs the same check, so new and re-verified questions are
covered; this tool cleans up what is already stored, with no model call.

--apply flags each one like a verification reject (flag_reason "verification: the answer can be read — …", verify_json
status 'readable', which keep_kps_servable never releases). Questions the learner flagged and retired questions are
left alone. A knowledge point left with fewer servable questions than its target is reported; the app tops pools up
when reviews run low. A consistent copy of the database is written to <data-dir>/backups/ first (unless --no-backup).

Stop the app server first: --apply refuses while the app answers on http://127.0.0.1:8000/api/health (--force if that
server uses another data dir).
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
    p.add_argument("--apply", action="store_true", help="flag the questions found (default: only list them)")
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
from app.pipeline.lessons import kp_targets  # noqa: E402
from app.pipeline.verify import VERIFICATION_PREFIX, readable_answer  # noqa: E402


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
    rows = db.query(
        "SELECT q.*, s.key AS skill_key FROM questions q JOIN skills s ON s.id = q.skill_id "
        "WHERE s.course_id = ? AND q.flagged = 0 AND q.key NOT LIKE 'old-%' ORDER BY s.order_index, q.kp_index, q.key",
        (args.course,),
    )
    found = [(r, why) for r in rows if (why := readable_answer(r))]
    print(f"{len(rows)} servable questions in course {args.course}; {len(found)} with an answer the learner can read:")
    for r, why in found:
        print(f"  {r['skill_key']} {r['key']} ({r['role'] or 'practice'}, {r['kind']}): {why}")
    if not found or not args.apply:
        return 0
    if not args.force and server_answers(args.server_url):
        print("The app server is running. Stop it first (or pass --force if it uses another data dir).")
        return 1
    if not args.yes and input(f"Flag these {len(found)} questions? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return 1
    if not args.no_backup:
        print("Backup:", backup())
    for r, why in found:
        db.execute(
            "UPDATE questions SET flagged = 1, flag_reason = ?, verify_json = ? WHERE id = ? AND flagged = 0",
            (f"{VERIFICATION_PREFIX} the answer can be read — {why}"[:500], db.dumps({"status": "readable", "problem": why}), r["id"]),
        )
    for skill_id in sorted({r["skill_id"] for r, _ in found}):
        skill = db.query_one("SELECT key, lesson_json FROM skills WHERE id = ?", (skill_id,))
        targets = kp_targets(db.loads(skill["lesson_json"], None) or {})
        for kp, target in enumerate(targets):
            left = db.query_one(
                "SELECT COUNT(*) AS n FROM questions WHERE skill_id = ? AND kp_index = ? AND role != 'capstone' AND flagged = 0",
                (skill_id, kp),
            )["n"]
            if left < target:
                print(f"  note: {skill['key']} knowledge point {kp + 1} keeps {left} servable question(s), target {target}")
    print(f"Flagged {len(found)} question(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
