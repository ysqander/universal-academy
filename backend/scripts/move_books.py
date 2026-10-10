"""Put every book's PDF in the books folder (UA_BOOKS_DIR, default <project>/books) and record its portable path.

    python scripts/move_books.py [--data-dir <dir>] [--books-dir <dir>] [--yes] [--no-backup] [--force]

Books imported before 2026-10-10 live in <data-dir>/books/<id>/book.pdf and their rows record that path (some as an
absolute path into a folder that has since moved). For each book this tool finds its PDF the way the app does
(pdf.book_pdf_path), COPIES it to <books-dir>/<id>/book.pdf — checked against the sha256 the import recorded — and sets
books.pdf_path to '<id>/book.pdf'. It also records each book's page signatures, so a re-saved copy of the book is
recognised on upload. Nothing is deleted: the old copy stays where it was — keep it while an older checkout of the app
(or one without this books folder) may still run; it reads only <data-dir>/books. Page renders are not copied (they
are rebuilt in <data-dir>/page-cache on demand). Running it again changes nothing. It exits non-zero when a book was
skipped or its PDF was not found. A consistent copy of the database is written to <data-dir>/backups/ first (unless
--no-backup).

--books-dir is read like UA_BOOKS_DIR (a relative path is taken from the project root). With --data-dir and no
--books-dir, the books folder is <data-dir>/books (a sandbox never writes to the project's books/).

Stop the app server first: the tool refuses while the app answers on http://127.0.0.1:8000/api/health (--force if
that server uses another data dir).
"""

from __future__ import annotations

import argparse
import hashlib
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
    p.add_argument("--books-dir", type=Path, help="target UA_BOOKS_DIR (default: <project>/books for the app's own data "
                   "dir, <data-dir>/books with another --data-dir; a relative path is taken from the project root)")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--no-backup", action="store_true", help="skip the database backup")
    p.add_argument("--force", action="store_true", help="run even though the app server answers (another data dir)")
    p.add_argument("--server-url", default=SERVER_HEALTH_URL, help="health URL of the app server (default: %(default)s)")
    return p.parse_args()


args = parse_args()
if args.data_dir:
    os.environ["UA_DATA_DIR"] = str(args.data_dir.resolve())
if args.books_dir:
    os.environ["UA_BOOKS_DIR"] = str(args.books_dir)  # resolved by the app's config, from the project root
elif args.data_dir:  # a sandbox keeps its books (whatever .env says): never the project's books/
    os.environ["UA_BOOKS_DIR"] = str(args.data_dir.resolve() / "books")

from app import db  # noqa: E402  (UA_DATA_DIR / UA_BOOKS_DIR must be set before the app is imported)
from app.api import books  # noqa: E402
from app.config import settings  # noqa: E402
from app.ingest import pdf  # noqa: E402


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def plan() -> list[dict]:
    """What each book needs: 'ok' (in place, path recorded), 'path' (in place, old path recorded), 'copy' (from an
    intact copy elsewhere — also over a damaged file in place), 'damaged' (no intact copy anywhere), 'missing';
    'sign' marks a book whose page signatures are not recorded yet."""
    steps = []
    for row in db.query("SELECT id, title, pdf_path, page_count FROM books ORDER BY created_at"):
        target = settings.books_dir / row["id"] / "book.pdf"
        source, intact = pdf.locate_book_pdf(row["id"], row["pdf_path"], row["page_count"])
        portable = row["pdf_path"] == pdf.stored_book_path(row["id"])
        if intact and source == target:
            action = "ok" if portable else "path"
        elif intact:
            action = "copy"
        elif source.is_file():
            action = "damaged"
        else:
            action = "missing"
        known = [r["key"].removeprefix("book_sha256:") for r in db.query(
            "SELECT key FROM kv WHERE key LIKE 'book_sha256:%' AND value = ?", (row["id"],))]
        unsigned = db.kv_get(f"book_pages:{row['id']}") is None
        steps.append({**row, "action": action, "source": source, "target": target, "known_sha": known, "sign": unsigned})
    return steps


def main() -> int:
    if not settings.db_path.exists():
        print(f"No database at {settings.db_path}")
        return 1
    if not args.force and server_answers(args.server_url):
        print("The app server is running. Stop it first (or pass --force if it uses another data dir).")
        return 1
    steps = plan()
    print(f"Books in {settings.data_dir} → books folder {settings.books_dir}")
    for s in steps:
        where = {"ok": "already in place", "path": "in place; record its portable path",
                 "copy": f"copy from {s['source']}", "missing": "PDF NOT FOUND anywhere — upload it again in the app",
                 "damaged": f"PDF DAMAGED ({s['source']}) and no intact copy — upload it again in the app"}[s["action"]]
        print(f"  {s['id']} {s['title']}: {where}" + ("; record its page signatures" if s["sign"] and s["action"] in ("ok", "path", "copy") else ""))
    problems = [s for s in steps if s["action"] in ("missing", "damaged")]
    todo = [s for s in steps if s["action"] in ("copy", "path") or (s["sign"] and s["action"] == "ok")]
    if not todo:
        print("Nothing to move." if not problems else f"Nothing to move; {len(problems)} book(s) have no PDF.")
        return 1 if problems else 0
    if not args.yes and input(f"Update {len(todo)} book(s)? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Cancelled.")
        return 1
    if not args.no_backup:
        print("Backup:", backup())
    for s in todo:
        if s["action"] in ("copy", "path"):
            checked = s["source"] if s["action"] == "copy" else s["target"]
            digest = sha256(checked)
            if s["known_sha"] and digest not in s["known_sha"]:
                print(f"  {s['id']}: {checked} is not the file that was imported (sha256 differs) — skipped")
                problems.append(s)
                continue
            if s["action"] == "copy":
                books._store(s["source"], s["id"])
                if sha256(s["target"]) != digest:
                    s["target"].unlink(missing_ok=True)
                    print(f"  {s['id']}: the copy did not match its source and was removed — skipped")
                    problems.append(s)
                    continue
            db.execute("UPDATE books SET pdf_path = ? WHERE id = ?", (pdf.stored_book_path(s["id"]), s["id"]))
            print(f"  {s['id']}: {s['target']}")
    books.sign_library()
    if problems:
        print(f"Done, except {len(problems)} book(s): " + ", ".join(f"{s['id']} {s['title']}" for s in problems))
        return 1
    print("Done. The old copies were left in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
