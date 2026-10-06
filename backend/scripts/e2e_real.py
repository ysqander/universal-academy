"""Real end-to-end build of a page range against the live APIs, with a readable report.

    python scripts/e2e_real.py --pdf <book.pdf> --start 15 --end 24 [--data-dir DIR] [--out report.txt]
                               [--reset graph|lessons|visuals|verify] [--skills S1,S4] [--report-only]

The book and course are reused when the data dir already has them (the build resumes and skips finished work), so
prompt iterations can re-run single stages: --reset deletes that stage's results (optionally only for --skills)
before building. Costs are printed from the job log.

To refresh a course somebody is already studying (keeps the old lesson if regeneration fails, keeps progress and
attempt history), use scripts/regenerate.py instead.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import os
import sys
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pdf", required=True, type=Path)
    p.add_argument("--start", required=True, type=int, help="first PDF page index (0-based)")
    p.add_argument("--end", required=True, type=int, help="last PDF page index (inclusive)")
    p.add_argument("--data-dir", type=Path, help="UA_DATA_DIR for this run (default: the app's data dir)")
    p.add_argument("--title", default="", help="course title (default: from the outline)")
    p.add_argument("--out", type=Path, help="also write the report to this file")
    p.add_argument("--reset", choices=["graph", "lessons", "visuals", "verify"], help="redo this stage")
    p.add_argument("--skills", default="", help="comma-separated skill keys limiting --reset lessons|visuals|verify")
    p.add_argument("--report-only", action="store_true", help="print the report without building")
    p.add_argument("--lessons", default="", help="comma-separated skill keys of the lessons to print in full")
    return p.parse_args()


args = parse_args()
if args.data_dir:
    os.environ["UA_DATA_DIR"] = str(args.data_dir.resolve())

from app import db  # noqa: E402  (UA_DATA_DIR must be set before the app is imported)
from app.ingest.pdf import read_book_meta  # noqa: E402
from app.pipeline import jobs  # noqa: E402
from app.pipeline.lessons import retire_questions  # noqa: E402
from app.schemas import CourseCreate, OutlineNode  # noqa: E402
from app.views import lesson_view  # noqa: E402

log = logging.getLogger("ua.e2e")


# ---------------------------------------------------------------------------
# Setup: book + course (reused when present)
# ---------------------------------------------------------------------------


def ensure_book(pdf: Path) -> str:
    row = db.query_one("SELECT id FROM books WHERE pdf_path = ?", (str(pdf.resolve()),))
    if row:
        return row["id"]
    meta = read_book_meta(pdf)
    book_id = db.new_id()
    db.execute(
        "INSERT INTO books(id, title, author, filename, pdf_path, page_count, outline_json, outline_source, "
        "page_labels_json, has_text_layer, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            book_id,
            meta.title,
            meta.author,
            pdf.name,
            str(pdf.resolve()),
            meta.page_count,
            db.dumps([n.model_dump() for n in meta.outline]),
            meta.outline_source,
            db.dumps(meta.page_labels),
            int(meta.has_text_layer),
            db.now_iso(),
        ),
    )
    return book_id


def sections_in_range(book_id: str, start: int, end: int) -> list[str]:
    outline = [OutlineNode(**n) for n in db.loads(db.query_one("SELECT outline_json FROM books WHERE id = ?", (book_id,))["outline_json"], [])]
    found: list[str] = []

    def walk(nodes: list[OutlineNode]) -> None:
        for n in nodes:
            if start <= n.start_page and n.end_page <= end and n.level <= 2:
                found.append(n.title)
            elif n.children:
                walk(n.children)

    walk(outline)
    return found


def ensure_course(book_id: str, start: int, end: int, title: str) -> str:
    row = db.query_one(
        "SELECT id FROM courses WHERE book_id = ? AND start_page = ? AND end_page = ? ORDER BY created_at DESC",
        (book_id, start, end),
    )
    if row:
        return row["id"]
    sections = sections_in_range(book_id, start, end)
    return jobs.create_course(
        CourseCreate(book_id=book_id, title=title or ", ".join(sections) or "", start_page=start, end_page=end, sections=sections)
    )


def reset_stage(course_id: str, stage: str, keys: list[str]) -> None:
    skills = db.query("SELECT id, key FROM skills WHERE course_id = ?", (course_id,))
    ids = [s["id"] for s in skills if not keys or s["key"] in keys]
    marks = ",".join("?" * len(ids))
    with db.connect() as conn:
        if stage == "graph":
            sub = "(SELECT id FROM skills WHERE course_id = ?)"
            conn.execute(f"DELETE FROM questions WHERE skill_id IN {sub}", (course_id,))
            conn.execute(f"DELETE FROM progress WHERE skill_id IN {sub}", (course_id,))
            for table in ("edges", "skills", "foundations"):
                conn.execute(f"DELETE FROM {table} WHERE course_id = ?", (course_id,))
        elif ids and stage == "lessons":
            for skill_id in ids:  # answered questions are retired, not deleted (attempts reference them)
                retire_questions(conn, skill_id)
            conn.execute(
                f"UPDATE skills SET lesson_status = 'pending', lesson_json = NULL, viz_json = NULL WHERE id IN ({marks})",
                ids,
            )
        elif ids and stage == "visuals":
            conn.execute(f"UPDATE skills SET viz_json = NULL WHERE id IN ({marks})", ids)
        elif ids and stage == "verify":  # learner flags and retired questions are left alone
            conn.execute(
                f"UPDATE questions SET verify_json = '{{}}', flagged = 0, flag_reason = '' "
                f"WHERE skill_id IN ({marks}) AND kind != 'free_response' "
                "AND NOT (flagged = 1 AND flag_reason NOT LIKE 'verification:%')",
                ids,
            )
    print(f"Reset {stage} for {'all skills' if not keys else ', '.join(keys)}")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def rule(title: str) -> None:
    print()
    print("=" * 100)
    print(title)
    print("=" * 100)


def print_graph(course_id: str) -> None:
    course = db.query_one("SELECT * FROM courses WHERE id = ?", (course_id,))
    rule(f"COURSE {course['title']} — status {course['status']}")
    print("Big picture:", course["big_picture"])
    for item in db.loads(course["excluded_json"], []):
        print("Excluded:", item)
    rule("FOUNDATIONS")
    for f in db.query("SELECT * FROM foundations WHERE course_id = ? ORDER BY key", (course_id,)):
        print(f"{f['key']} {f['title']}: {f['description']}\n   check: {f['check_question']} → {f['check_answer']}")
    rule("KNOWLEDGE GRAPH")
    skills = db.query("SELECT * FROM skills WHERE course_id = ? ORDER BY order_index", (course_id,))
    by_id = {s["id"]: s for s in skills}
    for s in skills:
        prereqs = db.query("SELECT src, weight, reduced FROM edges WHERE dst = ?", (s["id"],))
        needs = ", ".join(
            f"{by_id[e['src']]['key']}({e['weight']:g}{'' if e['reduced'] else ', implied'})" for e in prereqs
        )
        print(
            f"{s['key']:>4} [{s['kind']}, d{s['difficulty']}, {s['est_minutes']}min, pages {db.loads(s['source_pages_json'])}, "
            f"refs {db.loads(s['source_refs_json'])}, figs {len(db.loads(s['figure_ids_json']))}] {s['title']}"
        )
        print(f"       objective: {s['objective']}")
        print(f"       needs: {needs or '—'}   foundations: {db.loads(s['foundations_json'])}   lesson: {s['lesson_status']}")
        for m in db.loads(s["misconceptions_json"], []):
            print(f"       misconception: {m}")


def print_lesson(skill: dict[str, Any]) -> None:
    lesson = lesson_view(skill["id"])
    rule(f"LESSON {skill['key']} — {skill['title']}")
    if lesson is None:
        print(f"(lesson {skill['lesson_status']})")
        return
    print("Objective:", lesson.objective)
    print("\n## Why it matters\n" + lesson.why_it_matters)
    print("\n## The idea\n" + lesson.idea)
    for n in lesson.notation:
        print(f"  notation {n.symbol}: {n.meaning}")
    questions = db.query("SELECT * FROM questions WHERE skill_id = ? ORDER BY kp_index, key", (skill["id"],))
    for kp in lesson.knowledge_points:
        print(f"\n## KP {kp.index + 1}: {kp.title} (target {kp.target_questions})\n{kp.explanation}")
        we = kp.worked_example
        print(f"\nWorked example: {we.problem}")
        for step in we.steps:
            print(f"   {step.math}    — {step.why}")
        print(f"Takeaway: {we.takeaway}")
        for q in (q for q in questions if q["kp_index"] == kp.index):
            verify = db.loads(q["verify_json"], {})
            print(f"\n  [{q['key']} · tier {q['tier']} · {q['kind']}] {q['prompt']}")
            for i, c in enumerate(db.loads(q["choices_json"], [])):
                print(f"      {'*' if i == q['correct_choice'] else ' '}{i}. {c}")
            if q["answer"]:
                print(f"      answer: {q['answer']}  ({q['answer_display']})")
            for item in db.loads(q["checklist_json"], []):
                print(f"      ☐ {item}")
            for n, step in enumerate(db.loads(q.get("steps_json"), []) or [], start=1):
                print(f"      step {n} [{step['kind']}] {step['prompt']} → {step.get('answer') or step.get('correct_choice')} ({step['answer_display']})")
            print(f"      hints: {db.loads(q['hints_json'])}")
            print(f"      solution: {q['solution']}")
            if q["source_exercise"]:
                print(f"      adapted from: {q['source_exercise']}")
            independent = verify.get("independent", {})
            print(
                f"      verify: {verify.get('status')} {('· ' + q['flag_reason']) if q['flag_reason'] else ''}"
                + (f" · independent {independent.get('choice')}/{independent.get('answer')!r} conf {independent.get('confidence')}" if independent else "")
            )
    print("\nShow the missing steps:", lesson.missing_steps)
    print("Connection:", lesson.connection)
    print("Source refs:", lesson.source_refs, "pages", lesson.page_labels)
    viz = db.loads(skill["viz_json"], None)
    print("\n## Visualization plan")
    if viz is None:
        print("(none yet)")
        return
    shown = {k: v for k, v in viz.items() if k != "plot_curves" and v not in (None, [], "")}
    for k, v in shown.items():
        print(f"   {k}: {v}")
    if viz.get("plot_curves"):
        curves = viz["plot_curves"]
        print(f"   plot_curves: {len(curves)} curves, {sum(len(c['points']) for c in curves)} points")


def print_verification(course_id: str) -> None:
    rule("VERIFICATION")
    rows = db.query(
        "SELECT q.*, s.key AS skill_key FROM questions q JOIN skills s ON s.id = q.skill_id WHERE s.course_id = ?",
        (course_id,),
    )
    statuses = Counter(db.loads(r["verify_json"], {}).get("status", "unverified") for r in rows)
    kinds = Counter(r["kind"] for r in rows)
    machine = sum(n for k, n in kinds.items() if k != "free_response")
    print(f"questions: {len(rows)} ({dict(kinds)})")
    print(f"statuses: {dict(statuses)}")
    if machine:
        print(f"agree rate (auto-checked): {statuses['agree'] / machine:.0%}")
    for r in rows:
        status = db.loads(r["verify_json"], {}).get("status")
        if r["flagged"] or status == "disagree_kept":
            print(f"- {r['skill_key']} {r['key']} [{status}] {r['flag_reason'] or db.loads(r['verify_json']).get('reason', '')}")
            print(f"    prompt: {r['prompt'][:300]}")
            print(f"    stored: choice {r['correct_choice']} / {r['answer']!r}")


def report(course_id: str, lesson_keys: list[str]) -> None:
    job = jobs.get_job(course_id)
    rule("JOB")
    if job:
        print(f"state {job.state} · error {job.error}")
        for s in job.stages:
            print(f"  {s.key:<9} {s.status:<8} {s.done}/{s.total}  {s.message}")
        full_log = db.loads(db.query_one("SELECT log_json FROM jobs WHERE course_id = ?", (course_id,))["log_json"], [])
        print("\nLog:")
        for line in full_log:
            print("  " + line)
    print_graph(course_id)
    skills = db.query("SELECT * FROM skills WHERE course_id = ? ORDER BY order_index", (course_id,))
    chosen = [s for s in skills if s["key"] in lesson_keys] if lesson_keys else _two_lessons(skills)
    for s in chosen:
        print_lesson(s)
    print_verification(course_id)


def _two_lessons(skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A computational and a conceptual/proof lesson, when both exist."""
    ready = [s for s in skills if s["lesson_status"] == "ready"]
    computational = next((s for s in ready if s["kind"] in ("computation", "procedure")), None)
    conceptual = next((s for s in ready if s["kind"] in ("proof", "distinction", "concept")), None)
    picked = [s for s in (computational, conceptual) if s]
    return picked or ready[:2]


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    db.init_db()
    jobs.mark_interrupted_jobs()
    book_id = ensure_book(args.pdf)
    course_id = ensure_course(book_id, args.start, args.end, args.title)
    print(f"data dir: {os.environ.get('UA_DATA_DIR', '(default)')} · book {book_id} · course {course_id}")
    if args.reset:
        reset_stage(course_id, args.reset, [k.strip() for k in args.skills.split(",") if k.strip()])
    if not args.report_only:
        await jobs.run_build(course_id)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        report(course_id, [k.strip() for k in args.lessons.split(",") if k.strip()])
    text = buffer.getvalue()
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)


if __name__ == "__main__":
    asyncio.run(main())
