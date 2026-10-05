"""Render and parse stages: page PNGs for the course, then page transcription (+ figures) persisted page by page so
a resumed build skips finished pages."""

from __future__ import annotations

import asyncio
import json
import logging

from .. import db
from ..config import settings
from ..ingest import parse as ingest_parse
from ..ingest import pdf as ingest_pdf
from .context import BuildContext, BuildError, figure_db_id

log = logging.getLogger("ua.pipeline.pages")


async def run_render_stage(ctx: BuildContext) -> None:
    course, tracker = ctx.course, ctx.tracker
    out_dir = course.dir / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = course.pages
    todo = [p for p in pages if not (out_dir / f"{p}.png").exists()]
    done = len(pages) - len(todo)
    tracker.update("render", status="running", done=done, total=len(pages), message="")
    failed: list[int] = []
    # Sequential: renders serialize on the MuPDF lock anyway and are fast compared to everything after them.
    for page in todo:
        try:
            await _render_page(ctx, page)
        except Exception as e:  # noqa: BLE001 — retried once
            log.warning("render of page %d failed, retrying: %s", page, e)
            try:
                await _render_page(ctx, page)
            except Exception as e2:  # noqa: BLE001 — a missing render only costs the cached image
                failed.append(page)
                tracker.log(f"Could not render p.{course.label(page)}: {e2}", level=logging.WARNING)
                continue
        done += 1
        tracker.update("render", done=done)
    if len(failed) == len(pages):
        tracker.update("render", status="failed", message="no page could be rendered")
        raise BuildError(f"No page could be rendered from {course.pdf_path}")
    message = f"{done} pages" + (f" · {len(failed)} failed" if failed else "")
    tracker.update("render", status="done", message=message)


async def _render_page(ctx: BuildContext, page: int) -> None:
    png = await asyncio.to_thread(ingest_pdf.render_page_png, ctx.course.pdf_path, page, settings.render_dpi)
    ingest_pdf.write_atomic(ctx.course.dir / "pages" / f"{page}.png", png)


def parsed_pages(course_id: str) -> set[int]:
    return {r["page_index"] for r in db.query("SELECT page_index FROM pages WHERE course_id = ?", (course_id,))}


async def run_parse_stage(ctx: BuildContext) -> None:
    course, tracker = ctx.course, ctx.tracker
    pages = course.pages
    done_pages = parsed_pages(course.id) & set(pages)
    todo = [p for p in pages if p not in done_pages]
    tracker.update("parse", status="running", done=len(done_pages), total=len(pages), message="")
    if todo:
        tracker.log(f"Reading {len(todo)} pages…")
    limit = asyncio.Semaphore(max(1, settings.llm_concurrency))
    failed: list[int] = []

    async def one(page: int) -> None:
        async with limit:
            if not await _parse_with_retry(ctx, page):
                failed.append(page)
        tracker.update("parse", done=len(parsed_pages(course.id) & set(pages)))

    await asyncio.gather(*(one(p) for p in todo))
    n_done = len(parsed_pages(course.id) & set(pages))
    n_figures = db.query_one("SELECT COUNT(*) AS n FROM figures WHERE course_id = ?", (course.id,))["n"]
    if n_done == 0:
        tracker.update("parse", status="failed", message="no page could be read")
        raise BuildError("No page could be read.")
    summary = f"{n_done} pages · {n_figures} figure{'s' * (n_figures != 1)}"
    message = summary + (f" · {len(failed)} failed" if failed else "")
    tracker.update("parse", status="done", done=n_done, message=message)
    if todo:
        tracker.log(f"Parse: {message} · ${ctx.cost('vision'):.2f}")


async def _parse_with_retry(ctx: BuildContext, page: int) -> bool:
    course = ctx.course
    label = course.label(page)
    error: Exception | None = None
    for attempt in range(2):
        try:
            parsed = await ingest_parse.parse_page(course.pdf_path, page, label, course.dir)
        except Exception as e:  # noqa: BLE001 — one retry per page; the page is skipped after that
            error = e
            log.warning("parse of page %d attempt %d failed: %s", page, attempt + 1, e)
            continue
        store_page(course.id, parsed)
        boxed = sum(1 for item in parsed.items if item.get("kind") != "exercise")
        figs = len(parsed.figures)
        tracker_note = f"Parsed p.{label} — {figs} figure{'s' * (figs != 1)}, {boxed} boxed item{'s' * (boxed != 1)}"
        if parsed.is_exercise_page:
            tracker_note += " (exercises)"
        ctx.tracker.log(tracker_note)
        return True
    ctx.tracker.log(f"Could not read p.{label}: {error}", level=logging.WARNING)
    return False


def store_page(course_id: str, parsed: ingest_parse.ParsedPage) -> None:
    """Persist one parsed page and its figures atomically (re-parsing a page replaces its rows)."""
    with db.connect() as conn:
        conn.execute("DELETE FROM figures WHERE course_id = ? AND page_index = ?", (course_id, parsed.page_index))
        for fig in parsed.figures:
            conn.execute(
                "INSERT INTO figures(id, course_id, page_index, bbox_json, caption, description, image_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    figure_db_id(course_id, fig.id),
                    course_id,
                    fig.page_index,
                    db.dumps(list(fig.bbox)),
                    fig.caption,
                    fig.description,
                    str(fig.image_path),
                ),
            )
        conn.execute(
            "INSERT OR REPLACE INTO pages(course_id, page_index, label, markdown, items_json, uncertain_json, "
            "is_exercise_page, engine, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                course_id,
                parsed.page_index,
                parsed.label,
                parsed.markdown,
                db.dumps(parsed.items),
                db.dumps(parsed.uncertain),
                int(parsed.is_exercise_page),
                parsed.engine,
                json.dumps(parsed.raw, ensure_ascii=False, default=str),
            ),
        )
