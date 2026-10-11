"""Reword a stored question (or one step of a multistep question) in place: its prompt, and its choices with the
same count. Keys, answers and the answer display stay as they are, so attempts and open tasks are unaffected — for a
question whose answer is wrong, flag it (scripts/flag_question.py) instead.

    python scripts/edit_question.py --course <course_id> --skill S12a1 --question k1q1 [--step 3] --patch FILE.json
                                    [--data-dir <dir>] [--yes] [--no-backup] [--force]

FILE.json: {"prompt": "…", "choices": ["…", …]} (either key may be left out). --step is 1-based.
A consistent copy of the database is written to <data-dir>/backups/ first (unless --no-backup).

Stop the app server first: the tool refuses while the app answers on http://127.0.0.1:8000/api/health (--force if
that server uses another data dir).
"""

from __future__ import annotations

import argparse
import json
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
    p.add_argument("--skill", required=True, help="skill key, e.g. S12a1")
    p.add_argument("--question", required=True, help="question key, e.g. cap or k2q1")
    p.add_argument("--step", type=int, help="1-based step of a multistep question")
    p.add_argument("--patch", type=Path, required=True, help='JSON file: {"prompt": …, "choices": […]}')
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


def apply_patch(item: dict, patch: dict) -> list[str]:
    """Change item's prompt / choices in place; return what changed. Choices keep their count (the answer key)."""
    changed = []
    if "prompt" in patch:
        item["prompt"] = str(patch["prompt"])
        changed.append("prompt")
    if "choices" in patch:
        new = [str(c) for c in patch["choices"]]
        if len(new) != len(item.get("choices") or []):
            raise SystemExit(f"choices: {len(new)} given, the question has {len(item.get('choices') or [])}")
        item["choices"] = new
        changed.append("choices")
    return changed


def main() -> int:
    patch = json.loads(args.patch.read_text(encoding="utf-8"))
    if not isinstance(patch, dict) or not set(patch) & {"prompt", "choices"} or set(patch) - {"prompt", "choices"}:
        print('The patch must be a JSON object with "prompt" and/or "choices".')
        return 2
    db.init_db()
    skill = db.query_one("SELECT * FROM skills WHERE course_id = ? AND key = ?", (args.course, args.skill))
    if skill is None:
        print(f"Skill {args.skill} not found in course {args.course} at {settings.data_dir}")
        return 1
    q = db.query_one("SELECT * FROM questions WHERE skill_id = ? AND key = ?", (skill["id"], args.question))
    if q is None:
        print(f"Question {args.question} not found in {args.skill}")
        return 1
    if not args.force and server_answers(args.server_url):
        print("The app server is running. Stop it first (or pass --force if it uses another data dir).")
        return 1
    if args.step is not None:
        steps = json.loads(q["steps_json"] or "[]")
        if not 1 <= args.step <= len(steps):
            print(f"{args.skill} {args.question} has {len(steps)} steps")
            return 1
        before = dict(steps[args.step - 1])
        changed = apply_patch(steps[args.step - 1], patch)
        columns = {"steps_json": json.dumps(steps, ensure_ascii=False)}
        after = steps[args.step - 1]
    else:
        before = {"prompt": q["prompt"], "choices": json.loads(q["choices_json"] or "[]")}
        after = dict(before)
        changed = apply_patch(after, patch)
        columns = {"prompt": after["prompt"], "choices_json": json.dumps(after["choices"], ensure_ascii=False)}
    where = f"{args.skill} {args.question}" + (f" step {args.step}" if args.step else "")
    print(f"Reword {where} in {settings.data_dir} (course {args.course})")
    for field in changed:
        print(f"  {field}: {json.dumps(before.get(field), ensure_ascii=False)}\n    → {json.dumps(after[field], ensure_ascii=False)}")
    if not args.yes and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return 1
    if not args.no_backup:
        print("Backup:", backup())
    db.execute(f"UPDATE questions SET {', '.join(f'{c} = ?' for c in columns)} WHERE id = ?", (*columns.values(), q["id"]))
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
