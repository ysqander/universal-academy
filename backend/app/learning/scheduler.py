"""Spaced repetition: FSRS cards per skill + FIRe-lite implicit repetition through the graph (SPEC §6.4, §6.5).

Every function takes an explicit `now` (timezone-aware) so tests can simulate days passing.

FIRe-lite in one paragraph: w(S→P) is the effective encompassing weight from skill S down to its ancestor P
(max over paths of the product of edge weights; paths below 0.3 are ignored). Credit flows DOWN on success
(lesson pass / review Good or Hard), penalties flow UP on a failed review (Again), and remediation after a
lesson plateau makes the top prerequisites due now.
"""

from __future__ import annotations

import logging
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from fsrs import Card, Rating, Scheduler

from .. import db

log = logging.getLogger("ua.learning.scheduler")

MASTERED_STABILITY_DAYS = 21.0
FIRE_MIN_WEIGHT = 0.3  # ignore weaker paths entirely
FIRE_FULL_CREDIT_WEIGHT = 0.5  # >= this: an implicit FSRS review (Good)
FIRE_DUE_WINDOW = timedelta(days=3)  # full credit only for cards due within this window
FIRE_MAX_PUSH = timedelta(days=7)  # partial credit: push the due date by at most this much
FIRE_PENALTY_DUE = timedelta(days=1)
IMPLICIT_COOLDOWN = timedelta(hours=24)  # no implicit credit within a day of any other review event
REMEDIATION_PREREQS = 2
LEARNED = ("learned", "mastered")

# learning/relearning steps are empty so cards stay in the Review state (a lesson pass is the first review).
scheduler = Scheduler(desired_retention=0.9, learning_steps=(), relearning_steps=(), enable_fuzzing=True)


# ---------------------------------------------------------------------------
# Time + card helpers
# ---------------------------------------------------------------------------


def utc(dt: datetime) -> datetime:
    """FSRS insists on tzinfo == timezone.utc; naive datetimes are taken to be UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return utc(dt).isoformat(timespec="seconds")


def card_id_for(skill_id: str) -> int:
    try:
        return int(skill_id, 16) % (2**53)
    except ValueError:
        return zlib.crc32(skill_id.encode())


def load_card(card_json: str | None) -> Card | None:
    return Card.from_json(card_json) if card_json else None


def dump_card(card: Card) -> str:
    return card.to_json()


def review(card: Card | None, skill_id: str, rating: Rating, now: datetime) -> Card:
    """Review an existing card, or create the skill's card with this first review."""
    current = card if card is not None else Card(card_id=card_id_for(skill_id), due=utc(now))
    updated, _ = scheduler.review_card(current, rating, review_datetime=utc(now))
    return updated


def retrievability(card: Card | None, now: datetime) -> float | None:
    if card is None:
        return None
    return float(scheduler.get_card_retrievability(card, current_datetime=utc(now)))


def is_due(card: Card | None, now: datetime) -> bool:
    return card is not None and card.due is not None and utc(card.due) <= utc(now)


def status_for(card: Card) -> str:
    return "mastered" if (card.stability or 0.0) >= MASTERED_STABILITY_DAYS else "learned"


def review_score(credits: list[float]) -> float:
    """Order-sensitive score: answer i (1-based) has weight i, so a strong finish counts more than a strong start."""
    if not credits:
        return 0.0
    weights = range(1, len(credits) + 1)
    return sum(w * c for w, c in zip(weights, credits)) / sum(weights)


def rating_for_score(score: float) -> Rating:
    if score >= 1.0 - 1e-9:
        return Rating.Good
    if score >= 0.6 - 1e-9:
        return Rating.Hard
    return Rating.Again


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def log_review(
    course_id: str,
    skill_id: str,
    rating: int,
    now: datetime,
    *,
    implicit: bool = False,
    weight: float = 1.0,
    source_skill_id: str | None = None,
    due_before: datetime | None = None,
    due_after: datetime | None = None,
) -> str:
    log_id = db.new_id()
    db.execute(
        "INSERT INTO review_log(id, skill_id, course_id, rating, implicit, weight, source_skill_id, due_before, due_after, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            log_id,
            skill_id,
            course_id,
            int(rating),
            int(implicit),
            float(weight),
            source_skill_id,
            iso(due_before) if due_before else None,
            iso(due_after) if due_after else None,
            iso(now),
        ),
    )
    return log_id


def save_card(skill_id: str, card: Card, now: datetime, *, implicit_credit: bool = False) -> None:
    db.execute(
        "UPDATE progress SET card_json = ?, status = ?, implicit_credits = implicit_credits + ?, updated_at = ? WHERE skill_id = ?",
        (dump_card(card), status_for(card), int(implicit_credit), iso(now), skill_id),
    )


def apply_rating(course_id: str, skill_id: str, rating: Rating, now: datetime) -> tuple[Card, str]:
    """Explicit FSRS review of a skill (lesson pass or review task). Returns (new card, review_log id)."""
    row = db.query_one("SELECT card_json FROM progress WHERE skill_id = ?", (skill_id,))
    before = load_card(row["card_json"]) if row else None
    card = review(before, skill_id, rating, now)
    save_card(skill_id, card, now)
    log_id = log_review(
        course_id, skill_id, rating, now, due_before=before.due if before else None, due_after=card.due
    )
    return card, log_id


# ---------------------------------------------------------------------------
# Graph weights
# ---------------------------------------------------------------------------


@dataclass
class SkillGraph:
    prereqs: dict[str, list[tuple[str, float]]] = field(default_factory=dict)  # dst -> [(src, weight)]
    dependents: dict[str, list[str]] = field(default_factory=dict)  # src -> [dst]
    down: dict[str, dict[str, float]] = field(default_factory=dict)  # S -> {ancestor P: w(S→P)}
    up: dict[str, dict[str, float]] = field(default_factory=dict)  # P -> {descendant D: w(D→P)}

    def descendants(self, skill_id: str) -> set[str]:
        seen: set[str] = set()
        stack = [skill_id]
        while stack:
            for d in self.dependents.get(stack.pop(), []):
                if d not in seen:
                    seen.add(d)
                    stack.append(d)
        return seen


def effective_weights(edges: list[tuple[str, str, float]], min_weight: float = FIRE_MIN_WEIGHT) -> dict[str, dict[str, float]]:
    """edges = (prerequisite, dependent, weight). Returns down[S][P] = max over paths of the product of weights."""
    prereqs: dict[str, list[tuple[str, float]]] = defaultdict(list)
    nodes: set[str] = set()
    for src, dst, w in edges:
        nodes.update((src, dst))
        if w >= min_weight:
            prereqs[dst].append((src, w))
    memo: dict[str, dict[str, float]] = {}

    def best(skill: str, path: frozenset[str]) -> dict[str, float]:
        if skill in memo:
            return memo[skill]
        out: dict[str, float] = {}
        for p, w in prereqs.get(skill, []):
            if p in path:  # defensive: the graph stage removes cycles
                continue
            candidates = {p: w}
            for a, wa in best(p, path | {skill}).items():
                candidates[a] = max(candidates.get(a, 0.0), w * wa)
            for a, wa in candidates.items():
                if wa >= min_weight and wa > out.get(a, 0.0):
                    out[a] = wa
        memo[skill] = out
        return out

    return {s: best(s, frozenset()) for s in nodes}


def load_graph(course_id: str) -> SkillGraph:
    rows = db.query("SELECT src, dst, weight FROM edges WHERE course_id = ?", (course_id,))
    graph = SkillGraph()
    for r in rows:
        graph.prereqs.setdefault(r["dst"], []).append((r["src"], float(r["weight"])))
        graph.dependents.setdefault(r["src"], []).append(r["dst"])
    graph.down = effective_weights([(r["src"], r["dst"], float(r["weight"])) for r in rows])
    for s, ancestors in graph.down.items():
        for p, w in ancestors.items():
            graph.up.setdefault(p, {})[s] = w
    return graph


# ---------------------------------------------------------------------------
# FIRe-lite
# ---------------------------------------------------------------------------


@dataclass
class ImplicitEvent:
    skill_id: str
    kind: str  # "credit" | "push" | "penalty"
    weight: float
    before_card_json: str = ""  # enough to undo the event (revert_implicit) when its cause is re-scored
    after_card_json: str = ""
    log_id: str = ""


def _learned_cards(course_id: str) -> dict[str, Card]:
    rows = db.query(
        "SELECT skill_id, card_json FROM progress WHERE course_id = ? AND status IN ('learned','mastered') AND card_json IS NOT NULL",
        (course_id,),
    )
    return {r["skill_id"]: Card.from_json(r["card_json"]) for r in rows}


def _recently_reviewed(skill_id: str, now: datetime) -> bool:
    """At most one implicit credit per skill per day, and none on the day of an explicit review (too early to count)."""
    row = db.query_one(
        "SELECT 1 FROM review_log WHERE skill_id = ? AND created_at > ? LIMIT 1", (skill_id, iso(now - IMPLICIT_COOLDOWN))
    )
    return row is not None


def apply_success_credit(course_id: str, skill_id: str, now: datetime, graph: SkillGraph | None = None) -> list[ImplicitEvent]:
    """Credit flows down: success on `skill_id` implicitly reviews (or postpones) its learned ancestors."""
    graph = graph or load_graph(course_id)
    cards = _learned_cards(course_id)
    events: list[ImplicitEvent] = []
    for ancestor, w in sorted(graph.down.get(skill_id, {}).items(), key=lambda kv: -kv[1]):
        card = cards.get(ancestor)
        if card is None or _recently_reviewed(ancestor, now):
            continue
        if w >= FIRE_FULL_CREDIT_WEIGHT:
            if utc(card.due) > utc(now) + FIRE_DUE_WINDOW:
                continue
            updated = review(card, ancestor, Rating.Good, now)
            kind = "credit"
        else:
            if card.last_review is None:
                continue
            push = min(timedelta(seconds=w * (utc(card.due) - utc(card.last_review)).total_seconds()), FIRE_MAX_PUSH)
            if push <= timedelta(0):
                continue
            updated = Card.from_json(card.to_json())
            updated.due = utc(card.due) + push
            kind = "push"
        save_card(ancestor, updated, now, implicit_credit=True)
        log_id = log_review(
            course_id, ancestor, Rating.Good, now, implicit=True, weight=w, source_skill_id=skill_id,
            due_before=card.due, due_after=updated.due,
        )  # fmt: skip
        events.append(ImplicitEvent(ancestor, kind, w, dump_card(card), dump_card(updated), log_id))
        log.info("FIRe %s: %s -> %s (w=%.2f, due %s -> %s)", kind, skill_id, ancestor, w, iso(card.due), iso(updated.due))
    return events


def apply_failure_penalty(course_id: str, skill_id: str, now: datetime, graph: SkillGraph | None = None) -> list[ImplicitEvent]:
    """Penalties flow up: failing `skill_id` pulls in the reviews of learned skills that rely on it."""
    graph = graph or load_graph(course_id)
    cards = _learned_cards(course_id)
    limit = utc(now) + FIRE_PENALTY_DUE
    events: list[ImplicitEvent] = []
    for dependent, w in graph.up.get(skill_id, {}).items():
        card = cards.get(dependent)
        if card is None or w < FIRE_FULL_CREDIT_WEIGHT or utc(card.due) <= limit:
            continue
        updated = Card.from_json(card.to_json())
        updated.due = limit
        save_card(dependent, updated, now)
        log_id = log_review(
            course_id, dependent, Rating.Again, now, implicit=True, weight=w, source_skill_id=skill_id,
            due_before=card.due, due_after=updated.due,
        )  # fmt: skip
        events.append(ImplicitEvent(dependent, "penalty", w, dump_card(card), dump_card(updated), log_id))
        log.info("FIRe penalty: %s -> %s (w=%.2f) due by %s", skill_id, dependent, w, iso(limit))
    return events


def revert_implicit(events: list[dict]) -> int:
    """Undo FIRe events (as dicts of ImplicitEvent) whose card has not changed since. Returns how many were undone."""
    undone = 0
    for e in reversed(events or []):
        if not e.get("after_card_json") or not e.get("before_card_json"):
            continue
        row = db.query_one("SELECT card_json FROM progress WHERE skill_id = ?", (e["skill_id"],))
        if not row or row["card_json"] != e["after_card_json"]:
            continue  # touched since (a real review, another implicit event): leave it alone
        before = Card.from_json(e["before_card_json"])
        db.execute(
            "UPDATE progress SET card_json = ?, status = ?, implicit_credits = MAX(0, implicit_credits - ?) WHERE skill_id = ?",
            (e["before_card_json"], status_for(before), int(e.get("kind") in ("credit", "push")), e["skill_id"]),
        )
        if e.get("log_id"):
            db.execute("DELETE FROM review_log WHERE id = ?", (e["log_id"],))
        undone += 1
        log.info("FIRe %s on %s undone", e.get("kind"), e["skill_id"])
    return undone


def remediate(course_id: str, skill_id: str, now: datetime, graph: SkillGraph | None = None) -> list[str]:
    """Lesson plateau: make the top direct prerequisites (by weight) due now. Returns their skill ids."""
    graph = graph or load_graph(course_id)
    cards = _learned_cards(course_id)
    ranked = sorted(graph.prereqs.get(skill_id, []), key=lambda pw: -pw[1])
    chosen = [p for p, _ in ranked if p in cards][:REMEDIATION_PREREQS]
    for p in chosen:
        updated = Card.from_json(cards[p].to_json())
        updated.due = min(utc(updated.due), utc(now))
        save_card(p, updated, now)
        db.kv_set(f"remedial:{p}", db.dumps({"for": skill_id, "at": iso(now)}))
    if chosen:
        log.info("remediation for %s: prerequisites %s due now", skill_id, chosen)
    return chosen


def remedial_target(skill_id: str) -> str | None:
    """The skill whose plateau made `skill_id` a remedial review, if any."""
    raw = db.kv_get(f"remedial:{skill_id}")
    return db.loads(raw, {}).get("for") if raw else None


def clear_remedial(skill_id: str | None = None, *, target: str | None = None) -> None:
    if skill_id is not None:
        db.execute("DELETE FROM kv WHERE key = ?", (f"remedial:{skill_id}",))
    if target is not None:
        for row in db.query("SELECT key, value FROM kv WHERE key LIKE 'remedial:%'"):
            if db.loads(row["value"], {}).get("for") == target:
                db.execute("DELETE FROM kv WHERE key = ?", (row["key"],))
