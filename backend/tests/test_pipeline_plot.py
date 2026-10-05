"""function_plot pre-sampling and visualization-plan validation."""

from __future__ import annotations

import math

import pytest

from app.llm_schemas import VisualizationPlan
from app.pipeline.context import FigureInfo
from app.pipeline.plot import PlotError, compile_expression, parameter_values, sample_plot
from app.pipeline.visuals import finalize_plan


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('true')",
        "x.__class__",
        "open('f')",
        "lambda: 1",
        "x if x else 1",
        "y + x",
        "2j*x",
        "[x]",
        "Symbol('z')",
        "x" * 300,
    ],
)
def test_unsafe_or_unknown_expressions_are_rejected(expr):
    with pytest.raises(PlotError):
        compile_expression(expr, "a")


def test_whitelisted_expressions_compile():
    f = compile_expression("a*sin(x)^2 + sqrt(Abs(x)) + exp(0)*log(E) + 2x + pi", "a")
    assert f(1.0, 2.0) == pytest.approx(2 * math.sin(1) ** 2 + 1 + 1 + 2 + math.pi)
    with pytest.raises(PlotError):
        compile_expression("a*x", "sin")


def test_parameter_values_are_capped_and_initial_snaps():
    p = parameter_values(0, 10, 0.1, 3.33)
    assert len(p.values) == 21 and p.values[0] == 0 and p.values[-1] == 10
    assert p.step == pytest.approx(0.5) and p.initial == pytest.approx(3.5)
    p = parameter_values(-2, 2, 1, 0)
    assert p.values == [-2, -1, 0, 1, 2] and p.initial == 0
    with pytest.raises(PlotError):
        parameter_values(1, 1, 0.1, 1)


def test_curves_split_at_asymptotes_and_undefined_points():
    plot, curves, warnings = sample_plot(
        {
            "x_range": [-3, 3],
            "y_range": [-5, 5],
            "functions": [{"expr": "1/x", "label": "1/x"}, {"expr": "sqrt(x)", "label": "sqrt"}],
            "parameter": None,
        }
    )
    assert not warnings
    reciprocal = [c for c in curves if c["label"] == "1/x"]
    assert len(reciprocal) == 2  # one branch on each side of the asymptote
    assert all(p[0] < 0 for p in reciprocal[0]["points"]) and all(p[0] > 0 for p in reciprocal[1]["points"])
    sqrt_curves = [c for c in curves if c["label"] == "sqrt"]
    assert len(sqrt_curves) == 1 and all(p[0] >= 0 for p in sqrt_curves[0]["points"])
    assert all(c["param_value"] is None for c in curves)
    assert plot["y_range"] == [-5, 5]


def test_parameter_curves_and_auto_y_range():
    plot, curves, _ = sample_plot(
        {
            "x_range": [-2, 2],
            "y_range": None,
            "functions": [{"expr": "a*x**2", "label": "$ax^2$"}, {"expr": "bogus(x)", "label": "bad"}],
            "parameter": {"name": "a", "min": -1, "max": 1, "step": 0.5, "initial": 0.3},
        }
    )
    assert {c["param_value"] for c in curves} == {-1, -0.5, 0, 0.5, 1}
    assert all(len(c["points"]) == 200 for c in curves)
    assert plot["parameter"] == {"name": "a", "min": -1, "max": 1, "step": 0.5, "initial": 0.5}
    assert [f["label"] for f in plot["functions"]] == ["$ax^2$"]
    lo, hi = plot["y_range"]
    assert lo < -3 and hi > 3


def test_nothing_plottable_raises():
    with pytest.raises(PlotError):
        sample_plot({"x_range": [-1, 1], "y_range": None, "functions": [{"expr": "os", "label": ""}], "parameter": None})
    with pytest.raises(PlotError):
        sample_plot({"x_range": [-5, -1], "y_range": None, "functions": [{"expr": "log(x)", "label": ""}], "parameter": None})


def plan(**fields) -> VisualizationPlan:
    base = dict(
        misunderstanding="m",
        purpose="p",
        format="none",
        rationale="r",
        book_figure_id=None,
        learner_prompt="",
        caption="",
        table=None,
        steps=[],
        diagram=None,
        plot=None,
        frames=[],
    )
    return VisualizationPlan.model_validate(base | fields)


FIG = FigureInfo(db_id="c1-p3-f1", local_id="p3-f1", page=3, label="4", caption="A figure", description="d")


def test_book_figure_resolution_and_downgrade():
    viz, _ = finalize_plan(plan(format="book_figure", book_figure_id="p3-f1"), [FIG])
    assert viz["format"] == "book_figure" and viz["book_figure_id"] == "c1-p3-f1"
    viz, warnings = finalize_plan(plan(format="book_figure", book_figure_id="p9-f9"), [FIG])
    assert viz["format"] == "none" and viz["book_figure_id"] is None and warnings


def test_diagram_is_capped_and_ranges_cover_elements():
    vector = {"start": [0, 0], "end": [9, 2], "label": "v", "color": "danger"}
    diagram = {
        "x_range": [0, 1],
        "y_range": [5, 5],
        "show_grid": True,
        "axis_labels": ["Re"],
        "vectors": [vector] * 30 + [{"start": [0], "end": [1, 1], "label": "", "color": "primary"}],
        "points": [],
        "segments": [],
    }
    viz, _ = finalize_plan(plan(format="diagram_2d", diagram=diagram, table={"headers": ["a"], "rows": []}), [])
    d = viz["diagram"]
    assert viz["format"] == "diagram_2d" and viz["table"] is None
    assert len(d["vectors"]) == 12 and d["vectors"][0]["color"] == "danger"
    assert d["x_range"][0] <= 0 and d["x_range"][1] >= 9 and d["y_range"][0] < d["y_range"][1]
    assert d["axis_labels"] == []


def test_function_plot_is_sampled_and_bad_tables_downgrade():
    plot = {"x_range": [0, 1], "y_range": [0, 1], "functions": [{"expr": "x", "label": "x"}], "parameter": None}
    viz, _ = finalize_plan(plan(format="function_plot", plot=plot), [])
    assert viz["format"] == "function_plot" and viz["plot_curves"][0]["points"][0] == [0, 0]
    viz, warnings = finalize_plan(plan(format="comparison_table", table={"headers": ["only"], "rows": [["x"]]}), [])
    assert viz["format"] == "none" and warnings


@pytest.mark.parametrize(
    ("raw", "bare"),
    [
        ("$$(2+3i)(4+5i)=\\color{#d9480f}{-7+22i}.$$", "(2+3i)(4+5i)=\\color{#d9480f}{-7+22i}."),
        ("\\[x^2\\]", "x^2"),
        ("$a$ = $b$", "a = b"),
        ("\\$5 + x", "\\$5 + x"),
        ("  x+y  ", "x+y"),
    ],
)
def test_equation_latex_loses_its_delimiters(raw, bare):
    viz, _ = finalize_plan(
        plan(format="equation_steps", steps=[{"latex": raw, "note": ""}, {"latex": "$$y$$", "note": ""}]), []
    )
    assert [s["latex"] for s in viz["steps"]] == [bare, "y"]


# ---------------------------------------------------------------------------
# Regression tests (QA pass)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expr", ["x*9**9**9", "x + 7**7**7", "x + 10**5000", "(1+1/1000000)**1000000"])
def test_constant_power_towers_are_rejected_without_evaluation(expr):
    import time

    started = time.monotonic()
    with pytest.raises(PlotError, match="too large|overflow"):
        compile_expression(expr, "a")
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("expr", ["2^x^2", "x**(1/3)", "Abs(x)**(2/3)", "(1+1/100)**100", "x**(9**9)", "(x-1)**2/(x+2)"])
def test_reasonable_powers_still_compile(expr):
    compile_expression(expr, "a")


def test_one_bad_function_does_not_drop_the_plot():
    plot, curves, warnings = sample_plot(
        {
            "x_range": [-1, 1],
            "y_range": None,
            "functions": [{"expr": "x + 7**7**7", "label": "tower"}, {"expr": "x**2", "label": "square"}],
            "parameter": None,
        }
    )
    assert [f["label"] for f in plot["functions"]] == ["square"] and curves
    assert any("dropped" in w for w in warnings)


def test_downgraded_plan_falls_back_to_the_chosen_book_figure():
    viz, warnings = finalize_plan(plan(format="equation_steps", steps=[{"latex": "x", "note": ""}], book_figure_id="p3-f1"), [FIG])
    assert viz["format"] == "book_figure" and viz["book_figure_id"] == "c1-p3-f1" and viz["steps"] == []
    assert any("book figure" in w for w in warnings)


def test_equation_steps_that_repeat_the_worked_example_are_dropped():
    lesson = {
        "knowledge_points": [
            {
                "worked_example": {
                    "problem": "Compute $(2+3i)(4+5i)$.",
                    "steps": [
                        {"math": "$(2+3i)(4+5i)=2\\cdot4+2\\cdot5i+3i\\cdot4+(3i)(5i)$", "why": ""},
                        {"math": "$=8+10i+12i+15i^2$", "why": ""},
                        {"math": "$=8+22i-15=-7+22i$", "why": ""},
                    ],
                    "takeaway": "",
                }
            }
        ]
    }
    same = [
        {"latex": "(2+3i)(4+5i)", "note": ""},
        {"latex": "8+10i+12i+15\\color{#d9480f}{i^2}", "note": ""},
        {"latex": "\\color{#d9480f}{-7}+22i", "note": ""},
    ]
    viz, warnings = finalize_plan(plan(format="equation_steps", steps=same), [], lesson)
    assert viz["format"] == "none" and any("repeat a worked example" in w for w in warnings)
    other = [{"latex": "(1-2i)(3+i)", "note": ""}, {"latex": "3+i-6i-2i^2", "note": ""}, {"latex": "5-5i", "note": ""}]
    viz, _ = finalize_plan(plan(format="equation_steps", steps=other), [], lesson)
    assert viz["format"] == "equation_steps"


def test_bare_latex_is_still_exported_by_visuals():
    from app.pipeline.visuals import bare_latex

    assert bare_latex("$$x$$") == "x"
