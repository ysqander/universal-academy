"""Row → API-model builders shared by the courses API (pipeline) and the study API (learning)."""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from typing import Any

from . import db
from .learning import guided
from .pipeline.textutil import (
    bare_latex,
    clean_source_refs,
    lesson_text,
    mentions_caption,
    normalize_sibling_refs,
    placeholder_ids,
    sibling_keys,
)
from .schemas import (
    Figure,
    InlineCheckPublic,
    KnowledgePoint,
    Lesson,
    MultistepStepPublic,
    NotationItem,
    PlotCurve,
    QuestionPublic,
    SkillRef,
    TeachingBlockView,
    Visualization,
    VisualizationFrame,
    WorkedExample,
)


def page_labels_for(book_id: str) -> list[str]:
    row = db.query_one("SELECT page_labels_json FROM books WHERE id = ?", (book_id,))
    return db.loads(row["page_labels_json"], []) if row else []


def course_page_labels(course_id: str) -> list[str]:
    row = db.query_one(
        "SELECT b.page_labels_json FROM courses c JOIN books b ON b.id = c.book_id WHERE c.id = ?", (course_id,)
    )
    return db.loads(row["page_labels_json"], []) if row else []


def label_of(labels: list[str], page: int) -> str:
    return labels[page] if 0 <= page < len(labels) and labels[page] else str(page + 1)


def local_figure_id(course_id: str, figure_id: str) -> str:
    """The figure id the API exposes: the parser's local id ("p21-f1"), matching [[figure:p21-f1]] placeholders.
    The figures table stores "<course_id>-p21-f1" (see pipeline.context.figure_db_id)."""
    return figure_id.removeprefix(f"{course_id}-")


def figure_view(row: dict[str, Any], labels: list[str]) -> Figure:
    local = local_figure_id(row["course_id"], row["id"])
    return Figure(
        id=local,
        page=row["page_index"],
        page_label=label_of(labels, row["page_index"]),
        caption=row["caption"],
        description=row["description"],
        url=f"/api/courses/{row['course_id']}/figures/{local}.png",
    )


LEGACY_TARGET_QUESTIONS = 2  # knowledge points stored before target_questions existed


def question_public(row: dict[str, Any]) -> QuestionPublic:
    """A question as the learner sees it. No hint text (the learning engine adds the hints the current attempt has
    used; the next one comes from POST /hint, which records it). Multistep: the steps without their answers or
    explanations, and only the first step's prompt (the engine fills in step_index, step_results and the steps
    reached so far)."""
    hints = db.loads(row["hints_json"], [])
    return QuestionPublic(
        id=row["id"],
        skill_id=row["skill_id"],
        key=row["key"],
        kp_index=row["kp_index"],
        tier=row["tier"],
        kind=row["kind"],
        prompt=row["prompt"],
        choices=db.loads(row["choices_json"], []),
        hints=[],
        hint_count=sum(1 for h in hints if isinstance(h, str)),
        checklist=db.loads(row["checklist_json"], []),
        source_exercise=row["source_exercise"],
        flagged=bool(row["flagged"]),
        role="capstone" if row.get("role") == "capstone" else "practice",
        steps=public_steps(row),
    )


def public_steps(row: dict[str, Any], reached: int = 0, hinted: Collection[int] = ()) -> list[MultistepStepPublic]:
    """A multistep question's steps as the learner may see them: the prompts of the steps reached so far (0 ..
    `reached`) — a later step's prompt can state an earlier step's result, so it is sent blank until then — and the
    hint text only of the steps in `hinted` (using a hint halves the step's point; POST /hint records it)."""
    if row.get("kind") != "multistep":
        return []
    out = []
    for i, step in enumerate(db.loads(row.get("steps_json"), []) or []):
        text = str(step.get("hint") or "")
        shown = i <= reached
        out.append(
            MultistepStepPublic(
                index=i,
                prompt=step.get("prompt", "") if shown else "",
                kind=step.get("kind", "expression"),
                choices=list(step.get("choices") or []) if shown else [],
                hint=text if shown and i in hinted else "",
                has_hint=bool(text.strip()),
            )
        )
    return out


def skill_status(skill_id: str) -> str:
    row = db.query_one("SELECT status FROM progress WHERE skill_id = ?", (skill_id,))
    return row["status"] if row else "locked"


def skill_ref(row: dict[str, Any], status: str | None = None) -> SkillRef:
    return SkillRef(id=row["id"], key=row["key"], title=row["title"], status=status or skill_status(row["id"]))


def visualization_view(viz: dict[str, Any] | None, course_id: str, labels: list[str]) -> Visualization | None:
    """viz_json = VisualizationPlan fields + optional pre-sampled 'plot_curves' (list[PlotCurve dict])."""
    if not viz:
        return None
    figure = None
    fid = viz.get("book_figure_id")
    if fid:
        frow = db.query_one(
            "SELECT * FROM figures WHERE course_id = ? AND id IN (?, ?)", (course_id, fid, f"{course_id}-{fid}")
        )
        if frow:
            figure = figure_view(frow, labels)
    fmt = viz.get("format", "none")
    if fmt == "book_figure" and figure is None:
        fmt = "none"
    elif fmt == "none" and figure is not None:
        fmt = "book_figure"  # a downgraded plan falls back to the figure the planner picked
    plot = viz.get("plot") or {}
    return Visualization(
        format=fmt,
        misunderstanding=viz.get("misunderstanding", ""),
        purpose=viz.get("purpose", ""),
        rationale=viz.get("rationale", ""),
        learner_prompt=viz.get("learner_prompt", ""),
        caption=viz.get("caption", ""),
        figure=figure,
        table=viz.get("table"),
        # Stray $/$$ delimiters break KaTeX display rendering; courses built before the pipeline stripped them keep them.
        steps=[{**step, "latex": bare_latex(step.get("latex", ""))} for step in viz.get("steps") or []],
        diagram=viz.get("diagram"),
        plot_x_range=plot.get("x_range"),
        plot_y_range=plot.get("y_range"),
        plot_parameter=plot.get("parameter"),
        plot_curves=[PlotCurve(**c) for c in viz.get("plot_curves", [])],
        frames=[
            VisualizationFrame(**{**f, "latex": bare_latex(f["latex"]) if f.get("latex") else f.get("latex")})
            for f in viz.get("frames") or []
        ],
    )


def lesson_version(lesson_json: str | None) -> str:
    """Version of a stored lesson: sha1 of skills.lesson_json (the same value block feedback stores as lesson_hash).
    Lesson.version carries it to the browser; inline checks and block feedback send it back (409 when it changed)."""
    return hashlib.sha1((lesson_json or "").encode("utf-8")).hexdigest()


def same_version(version: str, lesson_json: str | None) -> bool:
    """Does a client's Lesson.version name this stored lesson? The full hash or a prefix of it (MIN_VERSION_CHARS+)."""
    sent = version.strip().lower()
    return len(sent) >= MIN_VERSION_CHARS and lesson_version(lesson_json).startswith(sent)


MIN_VERSION_CHARS = 8  # a shorter prefix of the hash is too likely to match a different version


def lesson_view(
    skill_id: str, guided_state: dict[str, Any] | None = None, *, learned: bool = False, kp_content: bool = True
) -> Lesson | None:
    """The lesson as the learner sees it. A guided proof lesson (learning/guided.py) of a skill not yet `learned`
    is gated by `guided_state` (the attempt's record; None = an attempt not started): in a scored attempt the blocks
    after the first open scored check, and "Go deeper", are not sent until it is answered; each scored check carries
    its record. `kp_content` False blanks the knowledge points' explanations and worked examples (a scored attempt
    never serves them). Once the skill is learned, every block is sent and the checks are formative again."""
    skill = db.query_one("SELECT * FROM skills WHERE id = ?", (skill_id,))
    if not skill or skill["lesson_status"] != "ready" or not skill["lesson_json"]:
        return None
    course_id = skill["course_id"]
    labels = course_page_labels(course_id)
    data = db.loads(skill["lesson_json"], {})
    gstate = None
    if guided.is_guided(data) and not learned:
        gstate = guided_state if isinstance(guided_state, dict) else guided.new_state(data, lesson_version(skill["lesson_json"]))
    questions = db.query("SELECT kp_index, flagged FROM questions WHERE skill_id = ?", (skill_id,))
    kps = []
    for i, kp in enumerate(data.get("knowledge_points", [])):
        kps.append(
            KnowledgePoint(
                index=i,
                target_questions=_target(kp.get("target_questions")),
                title=kp.get("title", f"Step {i + 1}"),
                explanation=kp.get("explanation", "") if kp_content else "",
                worked_example=WorkedExample(**kp["worked_example"]) if kp_content else WorkedExample(problem="", steps=[], takeaway=""),
                question_count=sum(1 for q in questions if q["kp_index"] == i and not q["flagged"]),
            )
        )
    source_pages = db.loads(skill["source_pages_json"], [])
    figures = [figure_view(r, labels) for r in lesson_figure_rows(skill, data)]
    prereqs = db.query(
        "SELECT s.* FROM edges e JOIN skills s ON s.id = e.src WHERE e.dst = ? ORDER BY s.order_index", (skill_id,)
    )
    unlocks = db.query(
        "SELECT s.* FROM edges e JOIN skills s ON s.id = e.dst WHERE e.src = ? ORDER BY s.order_index", (skill_id,)
    )
    v2 = data.get("format") == "v2"
    v2_fields: dict[str, Any] = {}
    if v2:  # planned teaching sequence (lesson generation v2): blocks with inline checks — never their answers
        plan = data.get("plan") or {}
        # plans stored before validation rewrote the planner's sibling ids show "a (S2a): …" → "S2a: …"
        course_keys = [r["key"] for r in db.query("SELECT key FROM skills WHERE course_id = ?", (course_id,))]
        assumed = normalize_sibling_refs(plan.get("assumed_prerequisites") or [], sibling_keys(skill["key"], course_keys))
        all_blocks = data.get("blocks") or []
        shown = guided.visible_blocks(gstate, len(all_blocks))
        scored = set(gstate.get("order") or []) if gstate else set()
        v2_fields = {
            "format": "v2",
            "goal_line": data.get("goal_line", ""),
            "blocks": [
                block_view(i, b, course_id, labels, guided_state=gstate if i in scored else None)
                for i, b in enumerate(all_blocks[:shown])
            ],
            "assumed": assumed,
            "out_of_scope": list(plan.get("out_of_scope") or []),
            "deeper": data.get("deeper", "") if shown == len(all_blocks) else "",
            "guided": gstate.get("mode") if gstate else None,
            "blocks_total": len(all_blocks),
        }
    return Lesson(
        skill_id=skill_id,
        skill_key=skill["key"],
        title=skill["title"],
        objective=skill["objective"],
        version=lesson_version(skill["lesson_json"]),
        **v2_fields,
        why_it_matters=data.get("why_it_matters", ""),
        idea=data.get("idea", ""),
        notation=[NotationItem(**n) for n in data.get("notation", [])],
        knowledge_points=kps,
        missing_steps=data.get("missing_steps", ""),
        # No "common mistakes" section is generated or shown (SPEC §6.3) — legacy v1 lessons keep the data in
        # lesson_json (the visual planner reads it), but the API no longer serves it.
        common_mistakes=[],
        connection=data.get("connection", ""),
        visualization=None if v2 else visualization_view(db.loads(skill["viz_json"], None), course_id, labels),
        source_refs=clean_source_refs(data.get("source_refs") or db.loads(skill["source_refs_json"], [])),
        source_pages=source_pages,
        page_labels=[label_of(labels, p) for p in source_pages],
        figures=figures,
        prerequisites=[skill_ref(r) for r in prereqs],
        unlocks=[skill_ref(r) for r in unlocks],
        question_count=sum(1 for q in questions if not q["flagged"]),
    )


def _target(value: Any) -> int:
    try:
        return min(3, max(1, int(value)))
    except (TypeError, ValueError):
        return LEGACY_TARGET_QUESTIONS


def block_view(
    index: int, block: dict[str, Any], course_id: str, labels: list[str], guided_state: dict[str, Any] | None = None
) -> TeachingBlockView:
    """A stored v2 teaching block as the learner sees it: the inline check without its answer or explanation (they
    come back from POST /api/skills/{sid}/blocks/{i}/check — or, for a scored guided step (`guided_state` = the
    attempt's record), from POST /api/tasks/{tid}/blocks/{i}/check once the check is done)."""
    check = block.get("check")
    public = None
    if check:
        public = InlineCheckPublic(kind=check["kind"], prompt=check["prompt"], choices=list(check.get("choices") or []))
        if guided_state is not None:
            public.scored = True
            public.second_try = guided.second_try(check)
            rec = guided.record(guided_state, index)
            public.state = guided.public_state(check, rec, scored=guided_state.get("mode") == "scored")
    return TeachingBlockView(
        index=index,
        kind=block.get("kind", "explain"),
        heading=block.get("heading") or None,
        body=block.get("body", ""),
        visualization=visualization_view(block.get("visual"), course_id, labels) if block.get("visual") else None,
        check=public,
    )


def lesson_figure_rows(skill: dict[str, Any], lesson: dict[str, Any]) -> list[dict[str, Any]]:
    """Figure rows a lesson shows: the figures attached to the skill, every figure its text places with
    [[figure:ID]], and figures on its source pages whose caption the text quotes (lessons may only refer to figures
    the learner can see). v2: also the book figures its block visuals show."""
    course_id = skill["course_id"]
    rows = db.query("SELECT * FROM figures WHERE course_id = ? ORDER BY page_index, id", (course_id,))
    if not rows:
        return []
    attached = {f"{course_id}-{local_figure_id(course_id, f)}" for f in db.loads(skill["figure_ids_json"], [])}
    text = lesson_text(lesson)
    placed = {f"{course_id}-{local_figure_id(course_id, f)}" for f in placeholder_ids(text)}
    placed |= {
        f"{course_id}-{local_figure_id(course_id, b['visual']['book_figure_id'])}"
        for b in lesson.get("blocks") or []
        if b.get("visual") and b["visual"].get("book_figure_id")
    }
    pages = set(db.loads(skill["source_pages_json"], []))
    return [
        r
        for r in rows
        if r["id"] in attached
        or r["id"] in placed
        or (r["page_index"] in pages and r["caption"] and mentions_caption(text, r["caption"]))
    ]
