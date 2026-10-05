"""Course facts shared by the build stages: page range, labels, figure ids and the chapter text sent to the LLM."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import db, llm
from ..config import settings
from ..ingest.pdf import book_pdf_path
from ..views import label_of
from .tracker import JobTracker


class BuildError(RuntimeError):
    """A failure that stops the whole build (graph failed, nothing could be parsed, too many lessons failed)."""


@dataclass(frozen=True)
class CourseInfo:
    id: str
    book_id: str
    book_title: str
    title: str
    start_page: int
    end_page: int
    sections: list[str]
    pdf_path: Path
    labels: list[str]

    @property
    def pages(self) -> list[int]:
        return list(range(self.start_page, self.end_page + 1))

    def label(self, page: int) -> str:
        return label_of(self.labels, page)

    @property
    def dir(self) -> Path:
        return course_dir(self.id)


@dataclass
class BuildContext:
    """Everything a stage needs during one run of the build."""

    course: CourseInfo
    tracker: JobTracker
    usage: llm.Usage
    abandoned_tasks: int = 0  # active tasks closed because their lesson was replaced (regeneration)

    def cost(self, *roles: str) -> float:
        return sum(self.usage.by_role.get(role, 0.0) for role in roles)


def course_dir(course_id: str) -> Path:
    return settings.courses_dir / course_id


def load_course(course_id: str) -> CourseInfo:
    row = db.query_one(
        "SELECT c.*, b.title AS book_title, b.pdf_path, b.page_labels_json "
        "FROM courses c JOIN books b ON b.id = c.book_id WHERE c.id = ?",
        (course_id,),
    )
    if row is None:
        raise LookupError(f"Course {course_id} not found")
    return CourseInfo(
        id=row["id"],
        book_id=row["book_id"],
        book_title=row["book_title"],
        title=row["title"],
        start_page=row["start_page"],
        end_page=row["end_page"],
        sections=db.loads(row["sections_json"], []),
        pdf_path=book_pdf_path(row["book_id"], row["pdf_path"]),  # follows a moved data dir
        labels=db.loads(row["page_labels_json"], []),
    )


# ---------------------------------------------------------------------------
# Figure ids. The parser names figures per page ("p21-f1"); the figures table has a global primary key, so rows
# are stored as "<course_id>-p21-f1". Prompts and page markdown keep the short local id.
# ---------------------------------------------------------------------------


def figure_db_id(course_id: str, local_id: str) -> str:
    return local_id if local_id.startswith(f"{course_id}-") else f"{course_id}-{local_id}"


def figure_local_id(course_id: str, db_id: str) -> str:
    return db_id.removeprefix(f"{course_id}-")


@dataclass(frozen=True)
class FigureInfo:
    db_id: str
    local_id: str
    page: int
    label: str
    caption: str
    description: str


def course_figures(course: CourseInfo) -> list[FigureInfo]:
    rows = db.query(
        "SELECT id, page_index, caption, description FROM figures WHERE course_id = ? ORDER BY page_index, id",
        (course.id,),
    )
    return [
        FigureInfo(
            db_id=r["id"],
            local_id=figure_local_id(course.id, r["id"]),
            page=r["page_index"],
            label=course.label(r["page_index"]),
            caption=r["caption"],
            description=r["description"],
        )
        for r in rows
    ]


def figure_line(fig: FigureInfo) -> str:
    caption = f' caption "{fig.caption}"' if fig.caption else " (no caption)"
    return f"- {fig.local_id} · page {fig.page} (p.{fig.label}) ·{caption} — {fig.description}".rstrip(" —")


# ---------------------------------------------------------------------------
# Chapter text (graph input and the cached shared prefix of every lesson call)
# ---------------------------------------------------------------------------


def page_marker(page: int, label: str, is_exercise: bool = False) -> str:
    return f"<<page {page} · p.{label}{' · exercises' if is_exercise else ''}>>"


def course_pages(course_id: str) -> list[dict[str, Any]]:
    return db.query(
        "SELECT page_index, label, markdown, is_exercise_page FROM pages WHERE course_id = ? ORDER BY page_index",
        (course_id,),
    )


def chapter_text(course: CourseInfo) -> str:
    """Deterministic for a given DB state, so every lesson call sends a byte-identical prefix."""
    pages = course_pages(course.id)
    first, last = course.label(course.start_page), course.label(course.end_page)
    parts = [
        f'# Source: "{course.book_title}" — {course.title}',
        f"Selected pages: PDF {course.start_page}–{course.end_page} (printed p.{first}–{last}).",
    ]
    if course.sections:
        parts.append("Selected sections: " + "; ".join(course.sections) + ".")
    exercise_pages = [p["page_index"] for p in pages if p["is_exercise_page"]]
    if exercise_pages:
        parts.append("Exercise pages: " + ", ".join(str(p) for p in exercise_pages) + ".")
    parts.append("")
    for p in pages:
        parts.append(page_marker(p["page_index"], p["label"] or course.label(p["page_index"]), bool(p["is_exercise_page"])))
        parts.append(p["markdown"].strip())
        parts.append("")
    figures = course_figures(course)
    parts.append("## Figures")
    if figures:
        parts.extend(figure_line(f) for f in figures)
    else:
        parts.append("(none)")
    return "\n".join(parts).strip() + "\n"


def skill_rows(course_id: str) -> list[dict[str, Any]]:
    return db.query("SELECT * FROM skills WHERE course_id = ? ORDER BY order_index", (course_id,))


def course_map(course_id: str) -> str:
    """Compact list of all skills and their prerequisites (part of the lesson prefix)."""
    skills = skill_rows(course_id)
    by_id = {s["id"]: s for s in skills}
    prereqs: dict[str, list[str]] = {}
    for e in db.query("SELECT src, dst FROM edges WHERE course_id = ?", (course_id,)):
        if e["src"] in by_id:
            prereqs.setdefault(e["dst"], []).append(by_id[e["src"]]["key"])
    lines = ["## Course map (all skills of this course, in learning order)"]
    for s in skills:
        needs = ", ".join(sorted(prereqs.get(s["id"], []), key=lambda k: (key_number(k), k)))
        lines.append(f"- {s['key']} · {s['title']} — {s['objective']}" + (f" [needs {needs}]" if needs else ""))
    return "\n".join(lines) + "\n"


def key_number(key: str) -> int:
    """'S12' → 12; a split skill 'S2a' → 2 (sort by (key_number(k), k) so S2a < S2b < S3)."""
    digits = "".join(ch for ch in key if ch.isdigit())
    return int(digits) if digits else 10**6


def focus_pages(source_pages: list[int], course: CourseInfo) -> list[int]:
    """The skill's own pages plus one neighbour on each side, clipped to the course."""
    pages = set()
    for p in source_pages:
        pages.update({p - 1, p, p + 1})
    return sorted(p for p in pages if course.start_page <= p <= course.end_page)
