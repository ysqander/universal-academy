"""Replace one part of a stored lesson in place — a worked example, a knowledge point's explanation, a teaching block's
body — by a JSON path into skills.lesson_json. Questions are edited with scripts/edit_question.py.

    python scripts/edit_lesson.py --course <course_id> --skill S12a1 --path knowledge_points.1.worked_example \\
                                  --value FILE.json [--data-dir <dir>] [--yes] [--no-backup] [--force]

--path: dot-separated keys and list indexes (0-based), e.g. blocks.3.body or knowledge_points.0.explanation; the
part must already exist. FILE.json holds the new value (a JSON object, list or string) and must have the same JSON
type as the old one. A consistent copy of the database is written to <data-dir>/backups/ first (unless --no-backup).

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
    p.add_argument("--path", required=True, help="dot path into lesson_json, e.g. knowledge_points.1.worked_example")
    p.add_argument("--value", type=Path, required=True, help="JSON file with the new value")
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


def locate(doc, path: str):
    """(container, key) of the part at `path`; SystemExit when a step does not exist."""
    parts = path.split(".")
    node = doc
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node.get(part)
        if node is None:
            raise SystemExit(f"{path}: {part} not found")
    last = parts[-1]
    key = int(last) if isinstance(node, list) else last
    if isinstance(node, list) and not 0 <= key < len(node) or isinstance(node, dict) and key not in node:
        raise SystemExit(f"{path}: {last} not found")
    return node, key


def main() -> int:
    value = json.loads(args.value.read_text(encoding="utf-8"))
    db.init_db()
    skill = db.query_one("SELECT * FROM skills WHERE course_id = ? AND key = ?", (args.course, args.skill))
    if skill is None:
        print(f"Skill {args.skill} not found in course {args.course} at {settings.data_dir}")
        return 1
    if not args.force and server_answers(args.server_url):
        print("The app server is running. Stop it first (or pass --force if it uses another data dir).")
        return 1
    lesson = json.loads(skill["lesson_json"] or "{}")
    container, key = locate(lesson, args.path)
    old = container[key]
    if type(old) is not type(value):
        print(f"{args.path} is a {type(old).__name__}; the new value is a {type(value).__name__}")
        return 1
    print(f"Replace {args.skill} {args.path} in {settings.data_dir} (course {args.course})")
    print("  before:", json.dumps(old, ensure_ascii=False)[:600])
    print("  after: ", json.dumps(value, ensure_ascii=False)[:600])
    if not args.yes and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return 1
    if not args.no_backup:
        print("Backup:", backup())
    container[key] = value
    db.execute("UPDATE skills SET lesson_json = ? WHERE id = ?", (json.dumps(lesson, ensure_ascii=False), skill["id"]))
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
