"""function_plot pre-sampling: LLM-written SymPy expressions are whitelisted token by token, parsed with a minimal
namespace and sampled on the backend, so the browser never evaluates an expression."""

from __future__ import annotations

import io
import keyword
import math
import re
import tokenize
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication,
    parse_expr,
    standard_transformations,
)

SAMPLES = 200
MAX_PARAM_VALUES = 21
MAX_EXPR_LENGTH = 200
MAX_ABS_Y = 1e6
MAX_CONST_EXPONENT = 100  # a constant power with a larger exponent (9**9**9) would be evaluated exactly by SymPy

FUNCTIONS: dict[str, Any] = {
    "sin": sp.sin,
    "cos": sp.cos,
    "tan": sp.tan,
    "exp": sp.exp,
    "log": sp.log,
    "sqrt": sp.sqrt,
    "Abs": sp.Abs,
    "pi": sp.pi,
    "E": sp.E,
}
_OPERATORS = {"+", "-", "*", "/", "**", "^", "(", ")", ","}
_NEUTRAL_TOKENS = {tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER}
_GLOBALS = {"Integer": sp.Integer, "Float": sp.Float, "Rational": sp.Rational, "Symbol": sp.Symbol}
_TRANSFORMS = standard_transformations + (implicit_multiplication, convert_xor)


class PlotError(ValueError):
    pass


def compile_expression(expr: str, param: str | None = None) -> Callable[[float, float], float]:
    """Compile `expr` in x (and optionally `param`) into f(x, p). Raises PlotError for anything outside the whitelist."""
    if not expr.strip() or len(expr) > MAX_EXPR_LENGTH:
        raise PlotError("expression is empty or too long")
    if param is not None and not valid_param_name(param):
        raise PlotError(f"invalid parameter name {param!r}")
    allowed = set(FUNCTIONS) | {"x"} | ({param} if param else set())
    _check_tokens(expr, allowed)
    x = sp.Symbol("x", real=True)
    p = sp.Symbol(param or "_p", real=True)
    local = dict(FUNCTIONS, x=x)
    if param:
        local[param] = p
    _guard_constant_powers(expr, local)
    try:
        parsed = parse_expr(expr, local_dict=local, global_dict=dict(_GLOBALS), transformations=_TRANSFORMS)
    except Exception as e:  # noqa: BLE001 — SymPy raises many exception types for malformed input
        raise PlotError(f"cannot parse {expr!r}: {e}") from e
    if not isinstance(parsed, sp.Expr):
        raise PlotError(f"{expr!r} is not an expression")
    extra = parsed.free_symbols - {x, p}
    if extra:
        raise PlotError(f"unknown symbols {sorted(map(str, extra))}")
    try:
        return sp.lambdify((x, p), parsed, modules="math")
    except Exception as e:  # noqa: BLE001 — a printer failure drops only this function
        raise PlotError(f"cannot compile {expr!r}: {e}") from e


def _guard_constant_powers(expr: str, local: dict[str, Any]) -> None:
    """Parse without evaluation and reject constant powers whose exact value would be astronomically large
    (x*9**9**9 makes SymPy compute a number with ~370 million digits and blocks for minutes)."""
    globals_ = dict(_GLOBALS, Add=sp.Add, Mul=sp.Mul, Pow=sp.Pow)
    try:
        tree = parse_expr(expr, local_dict=local, global_dict=globals_, transformations=_TRANSFORMS, evaluate=False)
    except Exception as e:  # noqa: BLE001
        raise PlotError(f"cannot parse {expr!r}: {e}") from e
    if not isinstance(tree, sp.Basic):
        return
    for node in sp.preorder_traversal(tree):
        if isinstance(node, sp.Pow) and not node.free_symbols:
            _estimate(node)


def _estimate(node: sp.Basic) -> float:
    """Float estimate of a constant subexpression; PlotError for a huge exponent or an overflow. nan when unknown
    (function calls), which leaves the node to SymPy's own (symbolic) handling."""
    try:
        if node.is_Number:
            return float(node)
        if node is sp.pi:
            return math.pi
        if node is sp.E:
            return math.e
        if isinstance(node, sp.Add):
            return math.fsum(_estimate(a) for a in node.args)
        if isinstance(node, sp.Mul):
            return math.prod(_estimate(a) for a in node.args)
        if isinstance(node, sp.Pow):
            base, exponent = _estimate(node.args[0]), _estimate(node.args[1])
            if math.isfinite(exponent) and abs(exponent) > MAX_CONST_EXPONENT:
                raise PlotError(f"constant power with exponent {exponent:g} is too large")
            try:
                value = math.pow(base, exponent)
            except ValueError:  # negative base, fractional exponent
                return math.nan
            if math.isinf(value):
                raise PlotError("constant power overflows")
            return value
    except OverflowError as e:
        raise PlotError("constant overflows") from e
    return math.nan


def valid_param_name(name: str) -> bool:
    return (
        bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,15}", name))
        and not keyword.iskeyword(name)
        and name not in FUNCTIONS
        and name not in _GLOBALS
        and name != "x"
    )


def _check_tokens(expr: str, allowed: set[str]) -> None:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(expr).readline))
    except (tokenize.TokenError, SyntaxError) as e:
        raise PlotError(f"cannot tokenize {expr!r}") from e
    for tok in tokens:
        if tok.type in _NEUTRAL_TOKENS:
            continue
        if tok.type == tokenize.NAME and tok.string in allowed:
            continue
        if tok.type == tokenize.NUMBER and not tok.string.lower().endswith("j"):
            continue
        if tok.type == tokenize.OP and tok.string in _OPERATORS:
            continue
        raise PlotError(f"token {tok.string!r} is not allowed")


@dataclass
class ParamValues:
    values: list[float]
    step: float
    initial: float


def parameter_values(minimum: float, maximum: float, step: float, initial: float) -> ParamValues:
    """minimum..maximum by step, thinned to at most 21 evenly spaced values; initial snaps to the nearest one."""
    if not all(map(math.isfinite, (minimum, maximum, step, initial))) or maximum <= minimum:
        raise PlotError("invalid parameter range")
    step = step if step > 0 else (maximum - minimum) / (MAX_PARAM_VALUES - 1)
    count = int(math.floor((maximum - minimum) / step + 1e-9)) + 1
    if count > MAX_PARAM_VALUES or count < 2:
        count = MAX_PARAM_VALUES if count > MAX_PARAM_VALUES else 2
        step = (maximum - minimum) / (count - 1)
    values = [_clean_float(minimum + k * step) for k in range(count)]
    snapped = min(values, key=lambda v: abs(v - initial))
    return ParamValues(values=values, step=_clean_float(step), initial=snapped)


def _clean_float(v: float) -> float:
    return float(f"{v:.10g}")


def sample(f: Callable[[float, float], float], xs: list[float], p: float) -> list[float | None]:
    ys: list[float | None] = []
    for x in xs:
        try:
            y = float(f(x, p))
        except (ValueError, ZeroDivisionError, OverflowError, TypeError):
            y = math.nan
        ys.append(y if math.isfinite(y) and abs(y) <= MAX_ABS_Y else None)
    return ys


def linspace(a: float, b: float, n: int) -> list[float]:
    return [a + (b - a) * k / (n - 1) for k in range(n)]


def split_curve(
    xs: list[float], ys: list[float | None], y_range: tuple[float, float]
) -> list[list[list[float]]]:
    """Break a sampled curve at undefined points, at points far outside the view and at jumps larger than the view
    height (asymptotes), returning polyline pieces with at least 2 points."""
    lo, hi = y_range
    span = hi - lo
    band = (lo - 2 * span, hi + 2 * span)
    decimals = max(2, 4 - int(math.floor(math.log10(span)))) if span > 0 else 4
    pieces: list[list[list[float]]] = []
    current: list[list[float]] = []
    prev: float | None = None
    for x, y in zip(xs, ys):
        if y is None or not band[0] <= y <= band[1] or (prev is not None and abs(y - prev) > span):
            if len(current) >= 2:
                pieces.append(current)
            current = []
            prev = None
            if y is None or not band[0] <= y <= band[1]:
                continue
        current.append([round(x, decimals), round(y, decimals)])
        prev = y
    if len(current) >= 2:
        pieces.append(current)
    return pieces


def auto_y_range(values: list[float]) -> tuple[float, float]:
    """y-range for plots that did not specify one: all values, unless a few extreme ones (near an asymptote) would
    flatten everything else — then the 5th–95th percentile band. Padded by 10%."""
    if not values:
        return (-1.0, 1.0)
    ordered = sorted(values)
    lo, hi = ordered[0], ordered[-1]
    p_lo, p_hi = ordered[int(0.05 * (len(ordered) - 1))], ordered[int(0.95 * (len(ordered) - 1))]
    if hi - lo > 4 * (p_hi - p_lo):
        lo, hi = p_lo, p_hi
    if hi - lo < 1e-9:
        return (lo - 1.0, hi + 1.0)
    pad = 0.1 * (hi - lo)
    return (_clean_float(lo - pad), _clean_float(hi + pad))


def valid_range(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        a, b = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(a) and math.isfinite(b)) or b <= a or b - a > 1e4:
        return None
    return (a, b)


def sample_plot(plot: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Validate and sample a VizPlot dict. Returns (normalized plot, PlotCurve dicts, warnings).
    Raises PlotError when nothing plottable remains."""
    warnings: list[str] = []
    x_range = valid_range(plot.get("x_range")) or (-5.0, 5.0)
    param = plot.get("parameter")
    pvals: ParamValues | None = None
    if param:
        try:
            if not valid_param_name(str(param["name"])):
                raise PlotError(f"invalid parameter name {param['name']!r}")
            pvals = parameter_values(
                float(param["min"]), float(param["max"]), float(param["step"]), float(param["initial"])
            )
        except (PlotError, KeyError, TypeError, ValueError) as e:
            warnings.append(f"parameter dropped: {e}")
            param = None
    name = str(param["name"]) if param else None

    kept: list[dict[str, Any]] = []
    compiled: list[tuple[str, Callable[[float, float], float]]] = []
    for fn in (plot.get("functions") or [])[:3]:
        try:
            compiled.append((fn.get("label") or fn["expr"], compile_expression(fn["expr"], name)))
            kept.append(fn)
        except Exception as e:  # noqa: BLE001 — PlotError, or anything unexpected: drop only this function
            warnings.append(f"function {fn.get('expr')!r} dropped: {e}")
    if not compiled:
        raise PlotError("no plottable function")

    p_values: list[float | None] = list(pvals.values) if pvals else [None]
    xs = linspace(*x_range, SAMPLES)
    sampled = [(label, pv, sample(f, xs, pv if pv is not None else 0.0)) for label, f in compiled for pv in p_values]
    y_range = valid_range(plot.get("y_range"))
    if y_range is None:
        y_range = auto_y_range([y for *_, ys in sampled for y in ys if y is not None])
    curves = [
        {"label": label, "param_value": pv, "points": piece}
        for label, pv, ys in sampled
        for piece in split_curve(xs, ys, y_range)
    ]
    if not curves:
        raise PlotError("no function is defined on the plotted range")
    normalized = {
        "x_range": list(x_range),
        "y_range": list(y_range),
        "functions": kept,
        "parameter": (
            {"name": name, "min": pvals.values[0], "max": pvals.values[-1], "step": pvals.step, "initial": pvals.initial}
            if pvals and name
            else None
        ),
    }
    return normalized, curves, warnings
