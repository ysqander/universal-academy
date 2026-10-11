"""Course bundles: a built course and its book as plain files, importable with no API key (the example courses in the
repo's examples/ folder).

A bundle is a folder:
    course.json   format + version, the book (metadata, sha256, page signatures), the course and its content rows
    book.pdf      the book
    assets/       the course's figures (<data>/courses/<course_id>/figures/, PNGs only)

It carries course content only, never learner data: progress, tasks, attempts, review_log, xp_log, block_feedback, kv
(quiz, goal and remediation state), the learner's answers to foundation checks, flag reasons, and build jobs (log,
usage, cost) stay out; flagged and retired ('old-…') questions are left out. pages.raw_json (engine debug output) too.
The reader's own marks stay out as well: book.pdf is written without annotations (highlights, notes, ink), and the
build's page renders (pages/, marks/: parse intermediates, which show those marks) are not shipped — the book page view
renders from the PDF, and a rebuild renders them again.

export_course reads the source database read-only (never through db.connect, which would migrate it).
import_bundle registers the book through the books API (_import: a book already in the library, by its bytes or its
pages, is reused, never duplicated), inserts the rows with their ids (figure ids, asset paths and lesson references keep
resolving) and starts fresh progress as a newly built course does (state.init_progress). Importing a course that is
already in the library changes nothing."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from . import db
from .api import books
from .config import PROJECT_ROOT, settings
from .ingest import pdf
from .learning import state
from .pipeline.context import course_dir, figure_local_id

log = logging.getLogger("ua.bundle")

FORMAT = "universal-academy-course"
VERSION = 1
EXAMPLES_DIR = PROJECT_ROOT / "examples"  # <name>/course.json (+ book.pdf, assets/)

ASSET_DIRS = ("figures",)  # the course's files that are copied (PNG only); pages/ and marks/ may show reader marks
# Content tables in insertion order (foreign keys), and the columns that never leave the source database.
_DROPPED = {
    "courses": {"book_id"},  # set to the importing library's book
    "pages": {"raw_json"},
    "figures": set(),
    "foundations": set(),
    "skills": set(),
    "edges": set(),
    "questions": set(),
}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class BundleError(ValueError):
    """A bundle that cannot be exported or imported (unknown format version, missing or damaged files)."""


@dataclass(frozen=True)
class ImportResult:
    course_id: str
    book_id: str
    title: str
    already_imported: bool
    message: str


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _ro_connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise BundleError(f"No database at {db_path}")
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _source_pdf(conn: sqlite3.Connection, book: dict[str, Any]) -> Path:
    """The book's PDF: the first candidate location whose sha256 is one recorded for the book (any, without records)."""
    known = {
        r["key"].removeprefix("book_sha256:")
        for r in _rows(conn, "SELECT key FROM kv WHERE key LIKE 'book_sha256:%' AND value = ?", (book["id"],))
    }
    for path in pdf.book_pdf_candidates(book["id"], book["pdf_path"]):
        if path.is_file() and (not known or pdf.sha256_file(path) in known):
            return path
    raise BundleError(f"The PDF of “{book['title']}” (book {book['id']}) is missing or damaged in the books folder.")


def _clean_pdf_copy(source: Path, target: Path) -> int:
    """Copy the book PDF to target without annotations (a reader's highlights, notes and ink — personal marks that must
    not ship with an example); a PDF without any is copied byte for byte. Links and form fields are kept. Returns the
    number of annotations removed."""
    doc = pymupdf.open(str(source))
    try:
        removed = 0
        for page in doc:
            for annot in list(page.annots() or []):
                page.delete_annot(annot)
                removed += 1
        if removed:
            doc.save(str(target), garbage=3, deflate=True)
    finally:
        doc.close()
    if not removed:
        shutil.copyfile(source, target)
    return removed


def _copy_assets(course_id: str, figures: list[dict[str, Any]], target: Path) -> list[str]:
    """Copy the course's PNGs (ASSET_DIRS) to target; a figure kept only at its stored path (older rows) is copied to
    figures/<local id>.png. Returns the figure ids whose image is missing."""
    source = course_dir(course_id)
    for sub in ASSET_DIRS:
        for png in sorted((source / sub).glob("*.png")) if (source / sub).is_dir() else []:
            if png.is_file() and not png.name.startswith("."):
                (target / sub).mkdir(parents=True, exist_ok=True)
                shutil.copy2(png, target / sub / png.name)
    missing = []
    for fig in figures:
        local = target / "figures" / f"{figure_local_id(course_id, fig['id'])}.png"
        stored = pdf.resolve_stored(fig["image_path"])
        if not local.is_file() and stored.is_file():
            local.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stored, local)
        if not local.is_file():
            missing.append(fig["id"])
    return missing


def _replace(part: Path, final: Path) -> None:
    if final.is_dir():
        shutil.rmtree(final)
    os.replace(part, final)


def export_course(
    course_id: str,
    out_dir: Path,
    *,
    description: str = "",
    license_name: str = "",
    license_url: str = "",
    notice: str = "",
) -> dict[str, Any]:
    """Write the bundle of a ready course to out_dir (course.json, book.pdf, assets/; other files there are kept).
    Reads the database of settings.data_dir read-only. Returns a summary (counts, missing figures)."""
    out_dir = Path(out_dir)
    conn = _ro_connect(settings.db_path)
    try:
        course = conn.execute("SELECT * FROM courses WHERE id = ?", (course_id,)).fetchone()
        if course is None:
            raise BundleError(f"Course {course_id} not found in {settings.db_path}")
        course = dict(course)
        if course["status"] != "ready":
            raise BundleError(f"Course {course_id} is {course['status']}, not ready: only a finished course can be exported.")
        book = dict(conn.execute("SELECT * FROM books WHERE id = ?", (course["book_id"],)).fetchone())
        skills = _rows(conn, "SELECT * FROM skills WHERE course_id = ? ORDER BY rowid", (course_id,))
        content: dict[str, list[dict[str, Any]]] = {
            "pages": _rows(conn, "SELECT * FROM pages WHERE course_id = ? ORDER BY rowid", (course_id,)),
            "figures": _rows(conn, "SELECT * FROM figures WHERE course_id = ? ORDER BY rowid", (course_id,)),
            "foundations": _rows(conn, "SELECT * FROM foundations WHERE course_id = ? ORDER BY rowid", (course_id,)),
            "skills": skills,
            "edges": _rows(conn, "SELECT * FROM edges WHERE course_id = ? ORDER BY rowid", (course_id,)),
            # servable questions only, in pool order (rowid within a knowledge point: engine._questions)
            "questions": _rows(
                conn,
                "SELECT q.* FROM questions q JOIN skills s ON s.id = q.skill_id WHERE s.course_id = ? AND q.flagged = 0"
                " AND q.key NOT LIKE 'old-%' ORDER BY q.rowid",
                (course_id,),
            ),
        }
        stored_pages = conn.execute("SELECT value FROM kv WHERE key = ?", (f"book_pages:{book['id']}",)).fetchone()
        source_pdf = _source_pdf(conn, book)
    finally:
        conn.close()

    for row in content["foundations"]:
        row["known"] = None  # the learner's answer to "do you know this?"
    for row in content["questions"]:
        row["flag_reason"] = ""
    for row in content["figures"]:
        row["image_path"] = f"courses/{course_id}/figures/{figure_local_id(course_id, row['id'])}.png"
    for table, dropped in _DROPPED.items():
        for row in [course] if table == "courses" else content[table]:
            for column in dropped:
                row.pop(column, None)

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_part = out_dir / ".book.pdf.part"
    if removed := _clean_pdf_copy(source_pdf, pdf_part):
        log.info("removed %d annotation(s) (reader marks) from the bundle's book.pdf", removed)
    sha = pdf.sha256_file(pdf_part)
    signatures = (
        (None if stored_pages[0] == "none" else json.loads(stored_pages[0])) if stored_pages else pdf.page_signatures(pdf_part)
    )
    assets_part = out_dir / ".assets.part"
    shutil.rmtree(assets_part, ignore_errors=True)
    assets_part.mkdir()
    missing = _copy_assets(course_id, content["figures"], assets_part)

    bundle = {
        "format": FORMAT,
        "version": VERSION,
        "description": description,
        "license": {"name": license_name, "url": license_url, "notice": notice},
        "book": {
            "title": book["title"],
            "author": book["author"],
            "filename": book["filename"],
            "page_count": book["page_count"],
            "outline_json": book["outline_json"],
            "outline_source": book["outline_source"],
            "page_labels_json": book["page_labels_json"],
            "has_text_layer": book["has_text_layer"],
            "pdf": "book.pdf",
            "sha256": sha,
            "page_signatures": signatures,
        },
        "course": course,
        **content,
    }
    json_part = out_dir / ".course.json.part"
    json_part.write_text(json.dumps(bundle, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(pdf_part, out_dir / "book.pdf")
    _replace(assets_part, out_dir / "assets")
    os.replace(json_part, out_dir / "course.json")  # last: a bundle is complete once course.json is in place
    summary = {"course_id": course_id, "title": course["title"], "missing_figures": missing, "annotations_removed": removed}
    summary.update({table: len(rows) for table, rows in content.items()})
    log.info("exported course %s to %s: %s", course_id, out_dir, summary)
    return summary


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def read_bundle(bundle_dir: Path) -> dict[str, Any]:
    """course.json of a bundle, checked: known format and version, plain ids. BundleError otherwise."""
    path = Path(bundle_dir) / "course.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BundleError(f"{path} cannot be read: {e}") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise BundleError(f"{path} is not a Universal Academy course bundle.")
    if data.get("version") != VERSION:
        raise BundleError(
            f"This course bundle has format version {data.get('version')!r}; this version of the app reads version {VERSION}."
            " Update the app to import it."
        )
    course = data.get("course") or {}
    ids = [course.get("id")] + [s.get("id") for s in data.get("skills", [])] + [q.get("id") for q in data.get("questions", [])]
    if not all(isinstance(i, str) and _ID.match(i) for i in ids):
        raise BundleError(f"{path} names an invalid id.")
    return data


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _insert(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]], **fixed: Any) -> None:
    """Insert rows, keeping only the columns this database has (a column added later takes its default)."""
    columns = _columns(conn, table)
    for row in rows:
        values = {k: v for k, v in row.items() if k in columns and k not in _DROPPED[table]} | fixed
        names = ", ".join(values)
        conn.execute(f"INSERT INTO {table}({names}) VALUES ({', '.join('?' * len(values))})", tuple(values.values()))


_import_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _import_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _import_locks.get(loop)
    if lock is None:
        _import_locks.clear()
        lock = _import_locks[loop] = asyncio.Lock()
    return lock


def _already(course_id: str, title: str, book_id: str) -> ImportResult:
    return ImportResult(course_id, book_id, title, True, f"“{title}” is already in your library.")


async def import_bundle(bundle_dir: Path) -> ImportResult:
    """Add a bundle's course (and its book, unless the library has it) with fresh progress. BundleError for a bundle
    that cannot be imported; an HTTPException from the books API for an unusable PDF."""
    bundle_dir = Path(bundle_dir)
    data = read_bundle(bundle_dir)
    course, book_meta = data["course"], data["book"]
    cid, title = course["id"], course["title"]
    async with _import_lock():
        existing = db.query_one("SELECT book_id FROM courses WHERE id = ?", (cid,))
        if existing is not None:
            return _already(cid, title, existing["book_id"])
        source = bundle_dir / book_meta.get("pdf", "book.pdf")
        if not source.is_file():
            raise BundleError(f"The bundle has no book PDF ({source.name}).")
        sha = await asyncio.to_thread(pdf.sha256_file, source)
        if sha != book_meta.get("sha256"):
            raise BundleError(f"{source.name} in the bundle is not the book the course was built from (its sha256 differs).")
        book = await books._import(source, sha, book_meta.get("filename") or source.name)
        if book.page_count != book_meta.get("page_count"):
            raise BundleError(f"The library's copy of “{book.title}” has {book.page_count} pages, the bundle's {book_meta.get('page_count')}.")

        target = course_dir(cid)
        created = not target.exists()
        assets = bundle_dir / "assets"
        await asyncio.to_thread(_install_assets, assets, target)
        try:
            with db.connect() as conn:
                _insert(conn, "courses", [course], book_id=book.id, created_at=db.now_iso())
                for table in ("pages", "figures", "foundations", "skills", "edges"):
                    _insert(conn, table, data.get(table, []), course_id=cid)
                _insert(conn, "questions", data.get("questions", []), flagged=0, flag_reason="")
        except sqlite3.IntegrityError as e:
            if created:
                shutil.rmtree(target, ignore_errors=True)
            raise BundleError(f"“{title}” conflicts with a course already in your library ({e}).") from e
        except BaseException:
            if created:
                shutil.rmtree(target, ignore_errors=True)
            raise
        state.init_progress(cid)
    log.info("imported course %s (%s) from %s on book %s", cid, title, bundle_dir, book.id)
    return ImportResult(cid, book.id, title, False, f"“{title}” was added to your library.")


def _install_assets(assets: Path, target: Path) -> None:
    for sub in ASSET_DIRS:
        for png in sorted((assets / sub).glob("*.png")) if (assets / sub).is_dir() else []:
            if png.is_file() and not png.name.startswith("."):
                (target / sub).mkdir(parents=True, exist_ok=True)
                shutil.copyfile(png, target / sub / png.name)


# ---------------------------------------------------------------------------
# Examples shipped in the repo
# ---------------------------------------------------------------------------

_summaries: dict[Path, tuple[tuple[int, int], dict[str, Any]]] = {}


def _summary(path: Path) -> dict[str, Any] | None:
    """The listing fields of examples/<name>/course.json (cached per mtime and size); None when unreadable."""
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _summaries.get(path)
    if cached and cached[0] == key:
        return cached[1]
    try:
        data = read_bundle(path.parent)
    except BundleError as e:
        log.warning("example %s skipped: %s", path.parent.name, e)
        return None
    licence = data.get("license") or {}
    summary = {
        "name": path.parent.name,
        "course_id": data["course"]["id"],
        "title": data["course"]["title"],
        "book_title": data["book"].get("title", ""),
        "author": data["book"].get("author", ""),
        "description": data.get("description", ""),
        "license": licence.get("name", ""),
        "license_url": licence.get("url", ""),
        "skills": len(data.get("skills", [])),
    }
    _summaries[path] = (key, summary)
    return summary


def examples() -> list[dict[str, Any]]:
    """Every readable bundle under EXAMPLES_DIR, by folder name, with imported = its course is in the library."""
    if not EXAMPLES_DIR.is_dir():
        return []
    out = []
    for path in sorted(EXAMPLES_DIR.glob("*/course.json")):
        summary = _summary(path)
        if summary is not None:
            imported = db.query_one("SELECT 1 FROM courses WHERE id = ?", (summary["course_id"],)) is not None
            out.append(summary | {"imported": imported})
    return out


def example_dir(name: str) -> Path | None:
    """The folder of the example called name (a folder under EXAMPLES_DIR with a course.json), else None."""
    if not _ID.match(name) or not EXAMPLES_DIR.is_dir():
        return None
    folder = EXAMPLES_DIR / name
    return folder if (folder / "course.json").is_file() else None
