"""Export a finished course as a bundle anyone can import and study with no API key (app/bundle.py) — the example
course in examples/ is made with it.

    python scripts/export_course.py --course <course_id> --out ../examples/<name> [--data-dir <dir>]
        [--description "..."] [--license "CC BY-NC 4.0"] [--license-url URL] [--notice "..."]

Writes <out>/course.json (the course's content: graph, lessons, visuals, servable questions, parsed pages, figures),
<out>/book.pdf and <out>/assets/ (figures, page renders, marks); other files in <out> are kept. No learner data goes
into the bundle: progress, tasks, attempts, reviews, XP, quiz and goal settings, "This step isn't clear" notes, the
foundation checks' answers, flag reasons and build jobs (log, usage, cost) stay out, and so do flagged and retired
questions. The source database is only read (opened read-only), so this can run while the app is running.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, help="source UA_DATA_DIR (default: the app's data dir)")
    p.add_argument("--course", required=True, help="course id")
    p.add_argument("--out", type=Path, required=True, help="bundle folder to write (created if missing)")
    p.add_argument("--description", default="", help="one line shown on the Library's example card")
    p.add_argument("--license", default="", help="the book's license, short name (e.g. 'CC BY-NC 4.0')")
    p.add_argument("--license-url", default="", help="link to the license text")
    p.add_argument("--notice", default="", help="attribution / changes notice kept with the bundle")
    return p.parse_args()


args = parse_args()
if args.data_dir:
    os.environ["UA_DATA_DIR"] = str(args.data_dir.resolve())

from app import bundle  # noqa: E402  (UA_DATA_DIR must be set before the app is imported)
from app.config import settings  # noqa: E402


def main() -> int:
    print(f"source: {settings.db_path} (read-only) · books: {settings.books_dir}")
    try:
        summary = bundle.export_course(
            args.course,
            args.out.resolve(),
            description=args.description,
            license_name=args.license,
            license_url=args.license_url,
            notice=args.notice,
        )
    except bundle.BundleError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    counts = ", ".join(f"{summary[t]} {t}" for t in ("skills", "edges", "questions", "pages", "figures", "foundations"))
    print(f"exported “{summary['title']}” ({args.course}) to {args.out.resolve()}: {counts}")
    if summary["missing_figures"]:
        print(f"warning: no image for figure(s) {', '.join(summary['missing_figures'])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
