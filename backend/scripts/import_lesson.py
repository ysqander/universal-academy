"""Import one skill's v2 lesson from another data dir (a sandbox copy where it was generated and reviewed).

    python scripts/import_lesson.py --from-data-dir <sandbox> --course <course_id> --skill S4a
                                    [--data-dir <target>] [--yes] [--no-backup] [--force]

Use it after trying a generator change on a copy of the database: the lesson the learner has seen in the sandbox is
stored as it is — not generated again — in the target course (default: the app's data dir). It replaces the skill's
current lesson exactly like a regeneration: active tasks on the skill are abandoned, answered questions are retired
(kept as history), the new questions get the verification verdicts they had in the sandbox. Progress, FSRS cards and
XP are untouched. A consistent copy of the target database is written to <data-dir>/backups/ first (unless
--no-backup).

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
    p.add_argument("--from-data-dir", type=Path, required=True, help="the data dir holding the lesson to import")
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

from app import db  # noqa: E402  (UA_DATA_DIR must be set before the app is imported)
from app.config import settings  # noqa: E402
from app.llm_schemas import TeachingSpec  # noqa: E402
from app.pipeline import jobs  # noqa: E402
from app.pipeline.lesson_v2 import persist_v2_lesson, question_draft  # noqa: E402


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


def load_source(source_dir: Path, course_id: str, key: str) -> tuple[dict, list[dict]]:
    """The skill row and its current (not retired) questions in the source data dir, read-only."""
    path = source_dir / "academy.sqlite3"
    if not path.exists():
        raise SystemExit(f"No database at {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        skill = conn.execute("SELECT * FROM skills WHERE course_id = ? AND key = ?", (course_id, key)).fetchone()
        if skill is None:
            raise SystemExit(f"Skill {key} not found in course {course_id} at {source_dir}")
        rows = conn.execute(
            "SELECT * FROM questions WHERE skill_id = ? AND key NOT LIKE 'old-%' ORDER BY kp_index, rowid", (skill["id"],)
        ).fetchall()
        return dict(skill), [dict(r) for r in rows]
    finally:
        conn.close()


def main() -> int:
    db.init_db()
    source = args.from_data_dir.resolve()
    if source == settings.data_dir.resolve():
        print("The source and the target data dir are the same.")
        return 2
    src_skill, src_rows = load_source(source, args.course, args.skill)
    lesson = json.loads(src_skill["lesson_json"] or "{}")
    if lesson.get("format") != "v2" or not lesson.get("plan"):
        print(f"{args.skill} in {source} is not a v2 lesson with a stored plan — nothing to import.")
        return 1
    target = db.query_one("SELECT * FROM skills WHERE course_id = ? AND key = ?", (args.course, args.skill))
    if target is None:
        print(f"Skill {args.skill} not found in course {args.course} at {settings.data_dir}")
        return 1
    if not args.force and server_answers(args.server_url):
        print("The app server is running. Stop it first (or pass --force if it uses another data dir).")
        return 1
    if jobs.is_building(args.course):
        print("The course's build job is running; let it finish first.")
        return 1
    n_kps = len(lesson.get("knowledge_points") or [])
    pools: list[list] = [[] for _ in range(n_kps)]
    capstone = None
    for row in src_rows:
        if row.get("role") == "capstone":
            capstone = question_draft(row)
        elif 0 <= row["kp_index"] < n_kps:
            pools[row["kp_index"]].append(question_draft(row))
    attempts = db.query_one("SELECT COUNT(*) AS n FROM attempts WHERE skill_id = ?", (target["id"],))["n"]
    print(f"Import {args.skill} · {lesson.get('title', '')}")
    print(f"  from {source}\n  into {settings.data_dir} (course {args.course})")
    print(f"  {len(lesson.get('blocks') or [])} blocks, {sum(len(p) for p in pools)} practice questions, "
          f"{'a capstone' if capstone else 'no capstone'}; the target skill has {attempts} answered attempts (kept)")
    if not args.yes and input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return 1
    if not args.no_backup:
        print("Backup:", backup())
    spec = TeachingSpec.model_validate(lesson["plan"])
    figure_ids = json.loads(src_skill.get("figure_ids_json") or "[]")
    abandoned = persist_v2_lesson(target["id"], lesson, pools, spec=spec, figure_ids=figure_ids, capstone=capstone)
    # the questions are the ones verified in the sandbox: carry their verdicts over (same key, point and role)
    verdicts = {(r["kp_index"], r["key"], r.get("role") or "practice"): r for r in src_rows}
    carried = 0
    for row in db.query("SELECT id, kp_index, key, role FROM questions WHERE skill_id = ? AND key NOT LIKE 'old-%'", (target["id"],)):
        src = verdicts.get((row["kp_index"], row["key"], row["role"] or "practice"))
        if src is not None:
            db.execute(
                "UPDATE questions SET verify_json = ?, flagged = ?, flag_reason = ? WHERE id = ?",
                (src["verify_json"], src["flagged"], src["flag_reason"], row["id"]),
            )
            carried += 1
    print(f"Imported. Abandoned active tasks: {abandoned} · verification verdicts carried over: {carried}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
