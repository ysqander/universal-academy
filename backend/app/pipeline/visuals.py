"""Visuals stage: a separate, deliberately constrained planner call per skill → VisualizationPlan (SPEC §4.3),
validated, with function plots pre-sampled. A visual failure never fails the course."""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from .. import db, llm
from ..llm_schemas import VisualizationPlan
from .context import BuildContext, CourseInfo, FigureInfo, course_figures, figure_line
from .plot import PlotError, sample_plot, valid_range
from .prompts import VIZ_INSTRUCTIONS

log = logging.getLogger("ua.pipeline.visuals")

COLORS = {"primary", "accent", "muted", "danger"}
CAPS = {"vectors": 12, "points": 20, "segments": 20, "rows": 12, "columns": 6, "steps": 10, "frames": 8}
FORMAT_FIELDS = {"table": None, "steps": [], "diagram": None, "plot": None, "frames": []}
FIELDS_BY_FORMAT = {
    "comparison_table": "table",
    "equation_steps": "steps",
    "diagram_2d": "diagram",
    "function_plot": "plot",
    "frames": "frames",
}


def none_visual(reason: str, *, error: bool = False) -> dict[str, Any]:
    viz: dict[str, Any] = {
        "misunderstanding": "",
        "purpose": "",
        "format": "none",
        "rationale": reason,
        "book_figure_id": None,
        "learner_prompt": "",
        "caption": "",
        **FORMAT_FIELDS,
    }
    if error:
        viz["error"] = reason
    return viz


def needs_visual(skill: dict[str, Any]) -> bool:
    viz = db.loads(skill["viz_json"], None)
    return viz is None or "error" in viz


async def ensure_visual(ctx: BuildContext, skill_id: str) -> bool:
    """Plan and store the skill's visual unless one is stored. Returns False when planning failed."""
    skill = db.query_one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if skill is None or skill["lesson_status"] != "ready" or not needs_visual(skill):
        return True
    figures = skill_figures(ctx.course, skill)
    text = viz_request(skill, db.loads(skill["lesson_json"], {}), figures)
    error: Exception | None = None
    for attempt in range(2):
        try:
            plan = await llm.structured(
                role="viz",
                instructions=VIZ_INSTRUCTIONS,
                text=text,
                schema=VisualizationPlan,
                max_output_tokens=16000,
                label=f"viz {skill['key']}",
                stream=True,
            )
            viz, warnings = finalize_plan(plan, figures)
            for warning in warnings:
                ctx.tracker.log(f"Visual {skill['key']}: {warning}", level=logging.WARNING)
            db.execute("UPDATE skills SET viz_json = ? WHERE id = ?", (db.dumps(viz), skill_id))
            return True
        except Exception as e:  # noqa: BLE001 — retried once, then stored as 'none'
            error = e
            log.warning("visual %s attempt %d failed: %s", skill["key"], attempt + 1, e)
    ctx.tracker.log(f"Visual for {skill['key']} failed ({error}); lesson shown without a visual", level=logging.WARNING)
    db.execute(
        "UPDATE skills SET viz_json = ? WHERE id = ?",
        (db.dumps(none_visual(f"Visual planning failed: {error}", error=True)), skill_id),
    )
    return False


def skill_figures(course: CourseInfo, skill: dict[str, Any]) -> list[FigureInfo]:
    """Figures the graph attached to the skill plus every figure on its source pages."""
    attached = set(db.loads(skill["figure_ids_json"], []))
    pages = set(db.loads(skill["source_pages_json"], []))
    return [f for f in course_figures(course) if f.db_id in attached or f.page in pages]


def viz_request(skill: dict[str, Any], lesson: dict[str, Any], figures: list[FigureInfo]) -> str:
    lines = [
        f"Skill {skill['key']}: {skill['title']}",
        f"Objective: {skill['objective']}",
        f"Kind: {skill['kind']}",
    ]
    misconceptions = db.loads(skill["misconceptions_json"], [])
    if misconceptions:
        lines.append("Known misconceptions:")
        lines.extend(f"- {m}" for m in misconceptions)
    lines += ["", "Lesson idea:", lesson.get("idea", ""), "", "Knowledge points and worked examples:"]
    for i, kp in enumerate(lesson.get("knowledge_points", []), start=1):
        example = kp.get("worked_example", {})
        lines.append(f"{i}. {kp.get('title', '')} — {kp.get('explanation', '')}")
        lines.append(f"   Example: {example.get('problem', '')}")
        lines.extend(f"   · {step.get('math', '')}  ({step.get('why', '')})" for step in example.get("steps", []))
        lines.append(f"   Takeaway: {example.get('takeaway', '')}")
    mistakes = lesson.get("common_mistakes", [])
    if mistakes:
        lines += ["", "Common mistakes:"] + [f"- {m}" for m in mistakes]
    lines += ["", "Book figures on this skill's pages (book_figure_id must come from this list):"]
    lines += [figure_line(f) for f in figures] if figures else ["(none)"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validation (pure)
# ---------------------------------------------------------------------------


def finalize_plan(plan: VisualizationPlan, figures: list[FigureInfo]) -> tuple[dict[str, Any], list[str]]:
    """Validate a plan: known figure ids, required content per format, sane ranges, capped element counts, sampled
    plots. Anything unusable downgrades to format 'none'."""
    warnings: list[str] = []
    viz = plan.model_dump()
    viz["book_figure_id"] = _resolve_figure(viz.get("book_figure_id"), figures, warnings)
    fmt = viz["format"]
    if fmt == "book_figure" and not viz["book_figure_id"]:
        fmt = _downgrade("book_figure without a valid figure id", warnings)
    elif fmt == "comparison_table":
        viz["table"] = _clean_table(viz.get("table"))
        if viz["table"] is None:
            fmt = _downgrade("comparison_table without a usable table", warnings)
    elif fmt == "equation_steps":
        steps = [{**s, "latex": bare_latex(s["latex"])} for s in viz.get("steps") or []]
        viz["steps"] = [s for s in steps if s["latex"]][: CAPS["steps"]]
        if len(viz["steps"]) < 2:
            fmt = _downgrade("equation_steps needs at least 2 steps", warnings)
    elif fmt == "diagram_2d":
        viz["diagram"] = _clean_diagram(viz.get("diagram"))
        if viz["diagram"] is None:
            fmt = _downgrade("diagram_2d without drawable elements", warnings)
    elif fmt == "frames":
        viz["frames"] = _clean_frames(viz.get("frames") or [])
        if len(viz["frames"]) < 2:
            fmt = _downgrade("frames needs at least 2 frames", warnings)
    elif fmt == "function_plot":
        try:
            viz["plot"], viz["plot_curves"], plot_warnings = sample_plot(viz.get("plot") or {})
            warnings.extend(plot_warnings)
        except PlotError as e:
            fmt = _downgrade(f"function_plot unusable: {e}", warnings)
    viz["format"] = fmt
    keep = FIELDS_BY_FORMAT.get(fmt)
    for field, empty in FORMAT_FIELDS.items():
        if field != keep:
            viz[field] = empty
    if fmt != "function_plot":
        viz.pop("plot_curves", None)
    return viz, warnings


def bare_latex(text: str) -> str:
    """LaTeX for one display equation without delimiters: '$$x$$', '\\[x\\]' or '$a$ = $b$' → 'x', 'a = b'.
    An unescaped $ is never valid inside display math, so every one is removed."""
    text = re.sub(r"(?<!\\)\$", "", text).strip()
    for left, right in (("\\[", "\\]"), ("\\(", "\\)")):
        if text.startswith(left) and text.endswith(right):
            return text[len(left) : -len(right)].strip()
    return text


def _downgrade(reason: str, warnings: list[str]) -> str:
    warnings.append(f"{reason} → none")
    return "none"


def _resolve_figure(fid: str | None, figures: list[FigureInfo], warnings: list[str]) -> str | None:
    if not fid:
        return None
    for f in figures:
        if fid.strip() in (f.local_id, f.db_id):
            return f.db_id
    warnings.append(f"unknown figure id {fid!r} ignored")
    return None


def _clean_table(table: dict[str, Any] | None) -> dict[str, Any] | None:
    if not table:
        return None
    headers = [str(h) for h in table.get("headers", [])][: CAPS["columns"]]
    if len(headers) < 2:
        return None
    width = len(headers)
    rows = [
        [str(c) for c in row][:width] + [""] * max(0, width - len(row))
        for row in table.get("rows", [])
        if any(str(c).strip() for c in row)
    ][: CAPS["rows"]]
    return {"headers": headers, "rows": rows} if rows else None


def _point(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    return [x, y] if math.isfinite(x) and math.isfinite(y) and abs(x) < 1e4 and abs(y) < 1e4 else None


def _clean_diagram(diagram: dict[str, Any] | None) -> dict[str, Any] | None:
    if not diagram:
        return None
    vectors = []
    for v in diagram.get("vectors", []):
        start, end = _point(v.get("start")), _point(v.get("end"))
        if start and end:
            vectors.append({**v, "start": start, "end": end, "color": _color(v.get("color"))})
    points = []
    for p in diagram.get("points", []):
        at = _point(p.get("at"))
        if at:
            points.append({**p, "at": at, "color": _color(p.get("color"))})
    segments = []
    for s in diagram.get("segments", []):
        start, end = _point(s.get("start")), _point(s.get("end"))
        if start and end:
            segments.append({**s, "start": start, "end": end, "dashed": bool(s.get("dashed"))})
    vectors, points, segments = vectors[: CAPS["vectors"]], points[: CAPS["points"]], segments[: CAPS["segments"]]
    if not (vectors or points or segments):
        return None
    coords = [c for v in vectors for c in (v["start"], v["end"])] + [p["at"] for p in points]
    coords += [c for s in segments for c in (s["start"], s["end"])]
    labels = [str(a) for a in diagram.get("axis_labels", [])]
    return {
        "x_range": _cover(valid_range(diagram.get("x_range")), [c[0] for c in coords]),
        "y_range": _cover(valid_range(diagram.get("y_range")), [c[1] for c in coords]),
        "show_grid": bool(diagram.get("show_grid")),
        "axis_labels": labels if len(labels) == 2 else [],
        "vectors": vectors,
        "points": points,
        "segments": segments,
    }


def _cover(given: tuple[float, float] | None, values: list[float]) -> list[float]:
    """The model's range, widened (with a margin) so every element is visible."""
    lo, hi = min(values + [0.0]), max(values + [0.0])
    if given is not None and given[0] <= lo and given[1] >= hi:
        return list(given)
    lo, hi = min(lo, given[0]) if given else lo, max(hi, given[1]) if given else hi
    margin = max(1.0, 0.1 * (hi - lo))
    return [math.floor(lo - margin), math.ceil(hi + margin)]


def _color(value: Any) -> str:
    return value if value in COLORS else "primary"


def _clean_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = []
    for frame in frames[: CAPS["frames"]]:
        diagram = _clean_diagram(frame.get("diagram"))
        latex = bare_latex(frame.get("latex") or "") or None
        if diagram is None and latex is None:
            continue
        cleaned.append({"title": frame.get("title", ""), "diagram": diagram, "latex": latex, "note": frame.get("note", "")})
    return cleaned
