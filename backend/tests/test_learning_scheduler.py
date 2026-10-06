"""FSRS wrapper, order-sensitive review scoring, rating mapping, FIRe-lite flows (SPEC §6.4, §6.5)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fsrs import Rating, State
from test_learning_fixtures import T0, build_course, card, days, isolated_engine, learn, set_due, status  # noqa: F401

from app import db
from app.learning import scheduler


def test_empty_learning_steps_keep_cards_in_review_state() -> None:
    c = scheduler.review(None, "abc123", Rating.Good, T0)
    assert c.state == State.Review
    assert scheduler.utc(c.due) > T0 + timedelta(days=1)
    again = scheduler.review(c, "abc123", Rating.Again, c.due)
    assert again.state == State.Review  # no relearning steps either
    assert scheduler.load_card(scheduler.dump_card(c)).due == c.due
    assert scheduler.retrievability(c, T0) == pytest.approx(1.0)
    assert scheduler.retrievability(c, T0 + days(30)) < 0.9
    assert scheduler.retrievability(None, T0) is None


def test_order_sensitive_score() -> None:
    # ✖✔✖✔✔ reads as learning happening; ✔✔✖✔✖ reads as struggling (Math Academy).
    assert scheduler.review_score([0, 1, 0, 1, 1]) == pytest.approx(11 / 15)
    assert scheduler.review_score([1, 1, 0, 1, 0]) == pytest.approx(7 / 15)
    assert scheduler.rating_for_score(11 / 15) == Rating.Hard
    assert scheduler.rating_for_score(7 / 15) == Rating.Again
    assert scheduler.review_score([1, 0.5, 1]) == pytest.approx((1 + 1 + 3) / 6)
    assert scheduler.review_score([]) == 0.0


@pytest.mark.parametrize(
    "credits,rating",
    [
        ([1, 1, 1], Rating.Good),  # 1.0
        ([0, 1, 1], Rating.Good),  # 5/6 >= 0.8
        ([1, 1, 0.5], Rating.Hard),  # 0.75
        ([1, 0, 1], Rating.Hard),  # 0.67
        ([1, 1, 0], Rating.Again),  # 0.5
        ([0, 0, 0], Rating.Again),
    ],
)
def test_rating_mapping(credits: list[float], rating: Rating) -> None:
    assert scheduler.rating_for_score(scheduler.review_score(credits)) == rating


def test_rating_thresholds_and_capstone_weight() -> None:
    assert scheduler.rating_for_score(0.8) == Rating.Good and scheduler.rating_for_score(0.7999) == Rating.Hard
    assert scheduler.rating_for_score(0.6) == Rating.Hard and scheduler.rating_for_score(0.5999) == Rating.Again
    # a capstone (weight 2) in 3rd place: order weights 1, 2, 3 × 2
    assert scheduler.review_score([1, 1, 0.5], [1, 1, 2]) == pytest.approx((1 + 2 + 6 * 0.5) / 9)
    assert scheduler.review_score([1, 1, 0.5], [1, 1, 1]) == scheduler.review_score([1, 1, 0.5])


def test_effective_weights_max_product_and_pruning() -> None:
    edges = [("A", "B", 0.8), ("B", "C", 0.6), ("A", "C", 0.3), ("D", "C", 0.2), ("F", "B", 0.4)]
    down = scheduler.effective_weights(edges)
    assert down["C"]["B"] == pytest.approx(0.6)
    assert down["C"]["A"] == pytest.approx(0.48)  # max(0.6 * 0.8, 0.3)
    assert "D" not in down["C"]  # direct edge below 0.3
    assert "F" not in down["C"]  # path product 0.24 below 0.3
    assert down["B"] == {"A": pytest.approx(0.8), "F": pytest.approx(0.4)}
    assert down["A"] == {}


def test_effective_weights_threshold_boundary_is_inclusive() -> None:
    down = scheduler.effective_weights([("E", "B", 0.5), ("B", "C", 0.6)])
    assert down["C"]["E"] == pytest.approx(0.3)


def test_credit_flows_down_once_per_day() -> None:
    fx = build_course()
    for key in ("S1", "S2", "S3"):
        learn(fx, key, T0)
    assert not db.query("SELECT 1 FROM review_log WHERE implicit = 1")  # same day as the explicit reviews: no credit
    s1_before = card(fx.sid("S1"))
    now = T0 + days(1.5)
    set_due(fx.sid("S1"), now + days(2))  # due within the 3-day window
    events = scheduler.apply_success_credit(fx.course_id, fx.sid("S3"), now)
    kinds = {e.skill_id: e.kind for e in events}
    assert kinds[fx.sid("S1")] == "credit"  # w = 0.8
    s1_after = card(fx.sid("S1"))
    assert scheduler.utc(s1_after.last_review) == now
    assert s1_after.stability > s1_before.stability
    rows = db.query("SELECT * FROM review_log WHERE skill_id = ? AND implicit = 1", (fx.sid("S1"),))
    assert len(rows) == 1 and rows[0]["source_skill_id"] == fx.sid("S3") and rows[0]["weight"] == pytest.approx(0.8)
    # second success the same day: no further implicit credit for S1
    set_due(fx.sid("S1"), now + days(1))
    again = scheduler.apply_success_credit(fx.course_id, fx.sid("S3"), now + timedelta(hours=3))
    assert fx.sid("S1") not in {e.skill_id for e in again}


def test_implicit_events_can_be_undone_unless_touched_since() -> None:
    from dataclasses import asdict

    fx = build_course()
    for key in ("S1", "S2", "S3"):
        learn(fx, key, T0)
    now = T0 + days(1.5)
    set_due(fx.sid("S1"), now + days(2))
    s1_json = db.query_one("SELECT card_json FROM progress WHERE skill_id = ?", (fx.sid("S1"),))["card_json"]
    s2_json = db.query_one("SELECT card_json FROM progress WHERE skill_id = ?", (fx.sid("S2"),))["card_json"]
    events = [asdict(e) for e in scheduler.apply_success_credit(fx.course_id, fx.sid("S3"), now)]
    assert {e["kind"] for e in events} == {"credit", "push"}
    set_due(fx.sid("S2"), now + days(9))  # S2 changed after the event: its push must stay
    assert scheduler.revert_implicit(events) == 1
    row = db.query_one("SELECT card_json, implicit_credits FROM progress WHERE skill_id = ?", (fx.sid("S1"),))
    assert row["card_json"] == s1_json and row["implicit_credits"] == 0
    assert not db.query("SELECT 1 FROM review_log WHERE skill_id = ? AND implicit = 1", (fx.sid("S1"),))
    assert db.query_one("SELECT card_json FROM progress WHERE skill_id = ?", (fx.sid("S2"),))["card_json"] != s2_json
    assert db.query("SELECT 1 FROM review_log WHERE skill_id = ? AND implicit = 1", (fx.sid("S2"),))
    assert scheduler.revert_implicit([]) == 0


def test_full_credit_skips_cards_not_due_soon() -> None:
    fx = build_course()
    for key in ("S1", "S2", "S3"):
        learn(fx, key, T0)
    set_due(fx.sid("S1"), T0 + days(10))
    events = scheduler.apply_success_credit(fx.course_id, fx.sid("S3"), T0 + days(1))
    assert fx.sid("S1") not in {e.skill_id for e in events}


def test_partial_credit_pushes_due_date() -> None:
    fx = build_course()
    for key in ("S1", "S2", "S3"):
        learn(fx, key, T0)
    s2 = card(fx.sid("S2"))
    interval = scheduler.utc(s2.due) - scheduler.utc(s2.last_review)
    events = scheduler.apply_success_credit(fx.course_id, fx.sid("S3"), T0 + days(1.1))
    assert {e.skill_id: e.kind for e in events}[fx.sid("S2")] == "push"  # w = 0.4
    pushed = card(fx.sid("S2"))
    expected = min(timedelta(seconds=0.4 * interval.total_seconds()), timedelta(days=7))
    assert scheduler.utc(pushed.due) - scheduler.utc(s2.due) == expected


def test_penalty_flows_up() -> None:
    fx = build_course()
    for key in ("S1", "S2", "S3", "S4"):
        learn(fx, key, T0)
    set_due(fx.sid("S4"), T0 + days(20))
    set_due(fx.sid("S3"), T0 + days(20))
    now = T0 + days(5)
    events = scheduler.apply_failure_penalty(fx.course_id, fx.sid("S1"), now)
    affected = {e.skill_id for e in events}
    assert fx.sid("S3") in affected  # w(S3→S1) = 0.8
    assert fx.sid("S4") not in affected  # w(S4→S1) = 0.48 < 0.5
    assert scheduler.utc(card(fx.sid("S3")).due) == now + days(1)
    events = scheduler.apply_failure_penalty(fx.course_id, fx.sid("S3"), now)
    assert {e.skill_id for e in events} == {fx.sid("S4")}
    assert db.query("SELECT COUNT(*) AS n FROM review_log WHERE implicit = 1 AND rating = 1")[0]["n"] == 2


def test_remediation_makes_top_prerequisites_due_now() -> None:
    fx = build_course()
    for key in ("S1", "S2"):
        learn(fx, key, T0)
    now = T0 + days(1)
    chosen = scheduler.remediate(fx.course_id, fx.sid("S3"), now)
    assert chosen == [fx.sid("S1"), fx.sid("S2")]  # sorted by weight
    for key in ("S1", "S2"):
        assert scheduler.is_due(card(fx.sid(key)), now)
        assert scheduler.remedial_target(fx.sid(key)) == fx.sid("S3")
    scheduler.clear_remedial(target=fx.sid("S3"))
    assert scheduler.remedial_target(fx.sid("S1")) is None


def test_mastery_threshold() -> None:
    fx = build_course()
    learn(fx, "S1", T0)
    assert status(fx.sid("S1")) == "learned"
    now = T0
    c = card(fx.sid("S1"))
    while (c.stability or 0) < scheduler.MASTERED_STABILITY_DAYS:
        now = scheduler.utc(c.due)
        c, _ = scheduler.apply_rating(fx.course_id, fx.sid("S1"), Rating.Good, now)
    assert status(fx.sid("S1")) == "mastered"
