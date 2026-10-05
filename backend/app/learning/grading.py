"""Answer checking (SPEC §6.1).

Public interface (stable):
    check_answer(kind, *, answer, correct_choice, tolerance, response, choice) -> GradeResult
    is_checkable(kind, answer, correct_choice) -> bool      # does the answer key parse / exist?

Math answers are parsed with SymPy's parser from a heavily normalized string (unicode, LaTeX-ish input,
`i`/`j` as the imaginary unit, implicit multiplication). The parser only ever sees a whitelisted character
set and identifiers resolved to SymPy objects, and every expression is size-checked on an unevaluated tree
before SymPy is allowed to evaluate it (so inputs like 9^9^9 are rejected instead of hanging).

Symbol convention: names starting with z or w are complex (z, w, z_1, w2 ...); every other letter is real.
This mirrors complex-number texts (z = a + bi with a, b real) and lets |z|^2 and z^2 be told apart.
"""

from __future__ import annotations

import logging
import math
import random
import re
import unicodedata
from dataclasses import dataclass, field
from keyword import iskeyword

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    function_exponentiation,
    implicit_application,
    implicit_multiplication,
    parse_expr,
    split_symbols_custom,
    standard_transformations,
)

log = logging.getLogger("ua.learning.grading")


@dataclass
class GradeResult:
    correct: bool
    feedback: str
    invalid: bool = False  # the learner's input could not be read at all (ask again; don't count it)


class AnswerParseError(ValueError):
    """Input that cannot be read as an answer (syntax, unsupported notation, or too large to check safely)."""


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_INPUT_CHARS = 400
MAX_NODES = 400  # nodes in one scalar expression tree
MAX_ITEMS = 64  # components in a tuple / set / matrix
MAX_LOG10 = 5000.0  # reject numbers that could exceed 10^5000 when evaluated
MAX_SYMBOLIC_EXPONENT = 100
SIMPLIFY_MAX_OPS = 40
EXACT_REL_TOL = 1e-10  # "exact" comparison of numbers typed as decimals
DEFAULT_NUMERIC_TOL = 1e-6

# ---------------------------------------------------------------------------
# Normalization: unicode + LaTeX-ish input -> plain SymPy syntax
# ---------------------------------------------------------------------------

_SET_OPEN, _SET_CLOSE = "⦃", "⦄"  # placeholders for set braces while LaTeX groups are rewritten

_GREEK = [
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa", "lamda", "mu",
    "nu", "xi", "omicron", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
]  # fmt: skip
_GREEK_CHARS = {unicodedata.lookup(f"GREEK SMALL LETTER {name.upper()}"): name for name in _GREEK}
_GREEK_CHARS["π"] = "pi"
_GREEK_CHARS["ϕ"] = "phi"
_GREEK_CHARS["ε"] = "epsilon"
_GREEK_CHARS["ϵ"] = "epsilon"

_UNICODE_OPS = {
    "\u2212": "-", "\u2013": "-", "\u2014": "-", "\u2010": "-", "\u2011": "-",
    "×": "*", "·": "*", "⋅": "*", "∗": "*", "•": "*",
    "÷": "/", "∕": "/", "⁄": "/",
    "\u00a0": " ", "\u2009": " ", "\u202f": " ", "\u200b": "",
    "（": "(", "）": ")", "［": "[", "］": "]",
    "ⅈ": "i", "ı": "i",
    "∣": "|", "‖": "|",
}  # fmt: skip

_SUPERSCRIPTS = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁻": "-", "⁺": "+", "ⁿ": "n",
}  # fmt: skip

_LATEX_SYMBOLS = {
    "cdot": "*", "times": "*", "ast": "*", "div": "/", "pi": "pi", "lambda": "lamda", "Re": "re", "Im": "im",
    "sin": "sin", "cos": "cos", "tan": "tan", "ln": "ln", "log": "log", "exp": "exp", "arg": "arg",
    "arcsin": "asin", "arccos": "acos", "arctan": "atan", "sinh": "sinh", "cosh": "cosh", "tanh": "tanh",
    "lvert": "|", "rvert": "|", "vert": "|", "mid": "|", "lVert": "|", "rVert": "|",
    "varepsilon": "epsilon", "varphi": "phi", "vartheta": "theta", "imath": "i",
}  # fmt: skip
_LATEX_SYMBOLS.update({g: g for g in _GREEK if g != "lamda"})

_LATEX_SPACES = ("\\,", "\\;", "\\:", "\\!", "\\ ", "\\quad", "\\qquad", "\\displaystyle", "\\textstyle", "~")
_LATEX_DELIM_SIZERS = re.compile(r"\\(left|right|bigl|bigr|Bigl|Bigr|biggl|biggr|Biggl|Biggr|big|Big|bigg|Bigg)(?![A-Za-z])")
_WRAPPERS = ("mathbf", "mathrm", "mathit", "boldsymbol", "bm", "vec", "text", "textbf", "textit", "operatorname", "mathsf")
_MATRIX_ENV = re.compile(r"\\begin\{([pbBvV]?matrix|smallmatrix|array)\}(?:\{[^}]*\})?(.*?)\\end\{\1\}", re.S)

_FUNCTIONS: dict[str, object] = {
    "sqrt": sp.sqrt, "root": sp.root, "exp": sp.exp, "ln": sp.log, "log": sp.log,
    "sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
    "arcsin": sp.asin, "arccos": sp.acos, "arctan": sp.atan, "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
    "abs": sp.Abs, "Abs": sp.Abs, "conj": sp.conjugate, "conjugate": sp.conjugate, "bar": sp.conjugate,
    "re": sp.re, "Re": sp.re, "im": sp.im, "Im": sp.im, "arg": sp.arg,
}  # fmt: skip
_CONSTANTS: dict[str, object] = {"i": sp.I, "j": sp.I, "I": sp.I, "e": sp.E, "E": sp.E, "pi": sp.pi}
_RESERVED = {"Symbol", "Integer", "Float", "Rational", "Number", "Add", "Mul", "Pow"}


def _take_group(s: str, pos: int) -> tuple[str, int]:
    """Read one LaTeX argument at s[pos:]: a balanced {...} group, a \\command, or a single character."""
    while pos < len(s) and s[pos] == " ":
        pos += 1
    if pos >= len(s):
        raise AnswerParseError("A LaTeX command is missing its argument.")
    if s[pos] == "{":
        depth = 0
        for end in range(pos, len(s)):
            if s[end] == "{":
                depth += 1
            elif s[end] == "}":
                depth -= 1
                if depth == 0:
                    return s[pos + 1 : end], end + 1
        raise AnswerParseError("Unbalanced braces.")
    if s[pos] == "\\":
        m = re.match(r"\\[A-Za-z]+", s[pos:])
        if m:
            return m.group(0), pos + len(m.group(0))
    return s[pos], pos + 1


def _rewrite_commands(s: str) -> str:
    """Rewrite argument-taking LaTeX commands (\\frac, \\sqrt, wrappers, \\bar ...) recursively."""
    out: list[str] = []
    pos = 0
    while pos < len(s):
        m = re.match(r"\\([A-Za-z]+)", s[pos:])
        if not m:
            out.append(s[pos])
            pos += 1
            continue
        name = m.group(1)
        pos += len(m.group(0))
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            num, pos = _take_group(s, pos)
            den, pos = _take_group(s, pos)
            out.append(f"(({_rewrite_commands(num)})/({_rewrite_commands(den)}))")
        elif name == "sqrt":
            index = None
            if pos < len(s) and s[pos] == "[":
                close = s.find("]", pos)
                if close < 0:
                    raise AnswerParseError("Unbalanced brackets in \\sqrt[...].")
                index, pos = s[pos + 1 : close], close + 1
            arg, pos = _take_group(s, pos)
            inner = _rewrite_commands(arg)
            out.append(f"(({inner})^(1/({_rewrite_commands(index)})))" if index else f"sqrt({inner})")
        elif name in ("overline", "bar", "conj"):
            arg, pos = _take_group(s, pos)
            out.append(f"conjugate({_rewrite_commands(arg)})")
        elif name in _WRAPPERS:
            arg, pos = _take_group(s, pos)
            out.append(f"({_rewrite_commands(arg)})" if name not in ("text", "operatorname") else arg.strip())
        elif name in _LATEX_SYMBOLS:
            out.append(f" {_LATEX_SYMBOLS[name]} ")
        else:
            raise AnswerParseError(f"Unsupported notation: \\{name}")
    return "".join(out)


def _matrix_env_to_brackets(match: re.Match[str]) -> str:
    rows = [r for r in re.split(r"\\\\", match.group(2)) if r.strip() and not r.strip().startswith("\\hline")]
    cells = [[c.strip() for c in row.split("&")] for row in rows]
    if len(cells) == 1:
        return "[" + ",".join(cells[0]) + "]"
    if all(len(r) == 1 for r in cells):
        return "[" + ",".join(r[0] for r in cells) + "]"
    return "[" + ",".join("[" + ",".join(r) + "]" for r in cells) + "]"


def _replace_superscripts(s: str) -> str:
    return re.sub(
        "[" + "".join(_SUPERSCRIPTS) + "]+",
        lambda m: "^(" + "".join(_SUPERSCRIPTS[c] for c in m.group(0)) + ")",
        s,
    )


def _replace_sqrt_sign(s: str) -> str:
    s = re.sub(r"√\s*([0-9.]+|[A-Za-z]\w*)", r"sqrt(\1)", s)
    return s.replace("√", "sqrt")


def _matching_close(s: str, open_pos: int) -> int:
    pairs = {"(": ")", "[": "]", "{": "}", _SET_OPEN: _SET_CLOSE}
    want = pairs[s[open_pos]]
    stack: list[str] = []
    for k in range(open_pos, len(s)):
        ch = s[k]
        if ch in pairs:
            stack.append(pairs[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
            if not stack:
                return k if ch == want else -1
    return -1


def _mark_set_braces(s: str) -> str:
    s = s.replace("\\{", _SET_OPEN).replace("\\}", _SET_CLOSE)
    stripped = s.strip()
    # Plain-text set "{1, 2}" (no LaTeX): the whole answer is one brace group.
    if stripped.startswith("{") and _matching_close(stripped, 0) == len(stripped) - 1 and "\\" not in stripped:
        s = _SET_OPEN + stripped[1:-1] + _SET_CLOSE
    return s


def _convert_abs_bars(s: str) -> str:
    out: list[str] = []
    open_bars = 0
    prev = ""
    for ch in s:
        if ch == "|":
            closes = open_bars > 0 and prev not in ("", "(", "[", ",", "+", "-", "*", "/", "^", "|", _SET_OPEN)
            if closes:
                out.append(")")
                open_bars -= 1
            else:
                out.append("Abs(")
                open_bars += 1
            prev = ")" if closes else "("
            continue
        out.append(ch)
        if not ch.isspace():
            prev = ch
    if open_bars:
        raise AnswerParseError("Unbalanced | bars.")
    return "".join(out)


def _strip_assignment(s: str) -> str:
    """'x = 3' -> '3' (a leading 'name =' is common in typed answers)."""
    if s.count("=") != 1:
        if "=" in s:
            raise AnswerParseError("Give a single value or expression, not an equation.")
        return s
    lhs, rhs = s.split("=")
    if re.fullmatch(r"\s*[A-Za-z][A-Za-z0-9_]*\s*(\(\s*[A-Za-z0-9_]*\s*\))?\s*", lhs):
        return rhs
    raise AnswerParseError("Give a single value or expression, not an equation.")


def _star_unknown_calls(s: str) -> str:
    """a(b+c) means a*(b+c) unless `a` is a known function name."""

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        return m.group(0) if name in _FUNCTIONS else f"{name}*("

    return re.sub(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(", repl, s)


def normalize(text: str) -> str:
    """Learner / answer-key text -> plain SymPy-ish syntax (may still contain [], set placeholders and commas)."""
    if text is None:
        raise AnswerParseError("Empty answer.")
    s = text.strip()
    if not s:
        raise AnswerParseError("Empty answer.")
    if len(s) > MAX_INPUT_CHARS:
        raise AnswerParseError("That answer is too long to check.")
    s = _replace_superscripts(s)
    s = unicodedata.normalize("NFKC", s)
    for src, dst in _UNICODE_OPS.items():
        s = s.replace(src, dst)
    for src, dst in _GREEK_CHARS.items():
        s = s.replace(src, f" {dst} ")
    s = _replace_sqrt_sign(s)
    # LaTeX delimiters and spacing
    s = s.replace("$", "").replace("\\(", "").replace("\\)", "")
    s = re.sub(r"\\\[|\\\]", "", s)
    for sp_cmd in _LATEX_SPACES:
        s = s.replace(sp_cmd, " ")
    s = _LATEX_DELIM_SIZERS.sub("", s)
    s = s.replace("\\|", "|")
    s = _MATRIX_ENV.sub(_matrix_env_to_brackets, s)
    s = _mark_set_braces(s)
    s = re.sub(r"\^\s*\{", "^{", s)
    s = re.sub(r"_\{([A-Za-z0-9]+)\}", r"_\1", s)  # x_{12} -> x_12
    s = _rewrite_commands(s)
    s = s.replace("{", "(").replace("}", ")")
    s = _convert_abs_bars(s)
    s = _strip_assignment(s)
    s = s.replace("^", "**")
    s = re.sub(r"\blambda\b", "lamda", s)
    s = _star_unknown_calls(s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        raise AnswerParseError("Empty answer.")
    return s


# ---------------------------------------------------------------------------
# Structure: scalar | tuple | set | matrix
# ---------------------------------------------------------------------------


@dataclass
class Node:
    kind: str  # "scalar" | "tuple" | "set" | "matrix"
    expr: sp.Expr | None = None
    items: list[Node] = field(default_factory=list)

    def size(self) -> int:
        return 1 if self.kind == "scalar" else sum(i.size() for i in self.items)


def _split_top(s: str, sep: str = ",") -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for k, ch in enumerate(s):
        if ch in "([{" + _SET_OPEN:
            depth += 1
        elif ch in ")]}" + _SET_CLOSE:
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(s[start:k])
            start = k + 1
    parts.append(s[start:])
    return [p.strip() for p in parts]


def _wrapped(s: str, opener: str) -> bool:
    return s.startswith(opener) and _matching_close(s, 0) == len(s) - 1


def _parse_structure(s: str, depth: int = 0) -> Node:
    if depth > 3:
        raise AnswerParseError("Too deeply nested.")
    s = s.strip()
    if not s:
        raise AnswerParseError("An entry is empty.")
    if _wrapped(s, _SET_OPEN):
        inner = s[1:-1].strip()
        items = [_parse_structure(p, depth + 1) for p in _split_top(inner)] if inner else []
        return Node("set", items=items)
    if _wrapped(s, "["):
        parts = _split_top(s[1:-1])
        if all(_wrapped(p, "[") for p in parts) and len(parts) > 0:
            rows = [_parse_structure(p, depth + 1) for p in parts]
            widths = {len(r.items) for r in rows}
            if len(widths) != 1:
                raise AnswerParseError("Matrix rows have different lengths.")
            return Node("matrix", items=rows)
        return Node("tuple", items=[_parse_structure(p, depth + 1) for p in parts])
    if _wrapped(s, "("):
        parts = _split_top(s[1:-1])
        if len(parts) > 1:
            return Node("tuple", items=[_parse_structure(p, depth + 1) for p in parts])
    parts = _split_top(s)
    if len(parts) > 1:
        return Node("tuple", items=[_parse_structure(p, depth + 1) for p in parts])
    return Node("scalar", expr=_parse_scalar(s))


_ALLOWED = re.compile(r"^[A-Za-z0-9_+\-*/(). ]*$")


def _can_split(name: str) -> bool:
    """'xy' means x*y, but Greek letter names (theta, lamda ...) stay whole."""
    return name.isalpha() and len(name) > 1 and name not in _GREEK


def _make_symbol(name: str) -> sp.Symbol:
    return sp.Symbol(name) if name[0] in "zw" else sp.Symbol(name, real=True)


_TRANSFORMS = standard_transformations + (
    convert_xor,
    split_symbols_custom(_can_split),
    implicit_multiplication,
    implicit_application,
    function_exponentiation,
)
_GLOBALS: dict[str, object] = {
    "__builtins__": {},
    "Symbol": _make_symbol,
    "Integer": sp.Integer,
    "Float": sp.Float,
    "Rational": sp.Rational,
    "Number": sp.Number,
    "Add": sp.Add,
    "Mul": sp.Mul,
    "Pow": sp.Pow,
    "I": sp.I,
}


def _check_tokens(s: str) -> None:
    if not _ALLOWED.match(s):
        bad = sorted({c for c in s if not _ALLOWED.match(c)})
        raise AnswerParseError(f"Unexpected character(s): {' '.join(bad)}")
    if "__" in s or re.search(r"\.\s*[A-Za-z_]", s):
        raise AnswerParseError("Unexpected syntax.")
    for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", s):
        if iskeyword(name) or name in _RESERVED or name.startswith("_"):
            raise AnswerParseError(f"Unexpected word: {name}")


def _log_magnitude(node: sp.Basic) -> float:
    """Upper bound for log10|value| of an unevaluated tree (symbols count as ~10)."""
    if node.is_Number:
        if isinstance(node, sp.Rational):  # includes Integer
            return math.log10(max(abs(int(node.p)), int(node.q)) + 1)
        value = abs(float(node))
        return math.log10(value + 1.0) if math.isfinite(value) else MAX_LOG10 + 1
    if node.is_Atom:
        return 1.0
    if isinstance(node, sp.Pow) or isinstance(node, sp.exp):
        base, exponent = (node.args if isinstance(node, sp.Pow) else (sp.E, node.args[0]))
        m_exp = _log_magnitude(exponent)
        if m_exp > 4:
            raise AnswerParseError("That number is too large to check.")
        if base.free_symbols and exponent.is_Number and abs(float(exponent)) > MAX_SYMBOLIC_EXPONENT:
            raise AnswerParseError("That exponent is too large to check.")
        return max(_log_magnitude(base), 1.0) * (10**m_exp + 1)
    child = [_log_magnitude(a) for a in node.args]
    if isinstance(node, sp.Mul):
        return sum(child)
    if isinstance(node, sp.Add):
        return max(child) + math.log10(len(child))
    return max(child, default=1.0) + 1.0


def _parse_scalar(s: str) -> sp.Expr:
    _check_tokens(s)
    try:
        raw = parse_expr(s, local_dict={**_FUNCTIONS, **_CONSTANTS}, global_dict=dict(_GLOBALS), transformations=_TRANSFORMS, evaluate=False)
    except AnswerParseError:
        raise
    except Exception as exc:  # SymPy raises a zoo of exception types for malformed input
        raise AnswerParseError("Couldn't read that expression.") from exc
    if not isinstance(raw, sp.Expr):
        raise AnswerParseError("Couldn't read that expression.")
    if sum(1 for _ in sp.preorder_traversal(raw)) > MAX_NODES:
        raise AnswerParseError("That expression is too long to check.")
    if _log_magnitude(raw) > MAX_LOG10:
        raise AnswerParseError("That number is too large to check.")
    try:
        expr = parse_expr(s, local_dict={**_FUNCTIONS, **_CONSTANTS}, global_dict=dict(_GLOBALS), transformations=_TRANSFORMS)
    except Exception as exc:
        raise AnswerParseError("Couldn't read that expression.") from exc
    if not isinstance(expr, sp.Expr) or expr.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
        raise AnswerParseError("That expression is undefined (division by zero?).")
    return expr


def parse_answer(text: str) -> Node:
    node = _parse_structure(normalize(text))
    if node.size() > MAX_ITEMS:
        raise AnswerParseError("Too many components to check.")
    return node


# ---------------------------------------------------------------------------
# Scalar equivalence
# ---------------------------------------------------------------------------


def _to_complex(value: sp.Expr) -> complex | None:
    try:
        c = complex(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return c if math.isfinite(c.real) and math.isfinite(c.imag) else None


def _numeric(expr: sp.Expr, subs: dict[sp.Symbol, sp.Expr] | None = None) -> complex | None:
    try:
        return _to_complex(sp.N(expr, 30, subs=subs) if subs else sp.N(expr, 30))
    except Exception:  # evalf can raise on exotic inputs; treat as "not evaluable"
        return None


def _close(a: complex, b: complex, rel: float, abs_tol: float = 0.0) -> bool:
    return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b), 1.0))


def _sample_points(symbols: list[sp.Symbol], rng: random.Random) -> dict[sp.Symbol, sp.Expr]:
    def real_value() -> float:
        v = rng.uniform(0.3, 2.9)
        return v if rng.random() < 0.6 else -v

    return {s: sp.Float(real_value()) if s.is_real else sp.Float(real_value()) + sp.I * sp.Float(real_value()) for s in symbols}


def _sampled_relation(a: sp.Expr, b: sp.Expr, relation) -> bool | None:
    """Evaluate `relation(va, vb)` at random points. True if it holds everywhere, False if it fails anywhere."""
    symbols = sorted(a.free_symbols | b.free_symbols, key=lambda s: s.name)
    rng = random.Random(1729)
    valid = 0
    for _ in range(10):
        point = _sample_points(symbols, rng)
        va, vb = _numeric(a, point), _numeric(b, point)
        if va is None or vb is None:
            continue
        if not relation(va, vb):
            return False
        valid += 1
        if valid >= 5:
            return True
    return None if valid < 3 else True


def _equal(a: sp.Expr, b: sp.Expr, abs_tol: float | None = None) -> bool:
    """Is b equivalent to a? abs_tol applies to number-valued answers only."""
    diff = a - b
    if diff == 0:
        return True
    try:
        if sp.count_ops(diff) <= SIMPLIFY_MAX_OPS and sp.simplify(diff) == 0:
            return True
    except Exception:  # simplify can fail on odd inputs; fall through to numeric checks
        log.debug("simplify failed for %s vs %s", a, b, exc_info=True)
    if not a.free_symbols and not b.free_symbols:
        va, vb = _numeric(a), _numeric(b)
        if va is None or vb is None:
            return False
        if abs_tol is not None:
            return abs(va - vb) <= max(abs_tol, 1e-9 * abs(va))
        return _close(va, vb, EXACT_REL_TOL)
    return bool(_sampled_relation(a, b, lambda x, y: _close(x, y, 1e-9)))


def _has_float(expr: sp.Expr) -> bool:
    return any(isinstance(atom, sp.Float) for atom in expr.atoms(sp.Number))


def _near_miss(expected: sp.Expr, got: sp.Expr, abs_tol: float | None, explicit_tol: float | None) -> str:
    """A short, specific hint for common slips; generic message otherwise."""
    extra = {s.name for s in got.free_symbols} - {s.name for s in expected.free_symbols}
    if extra:
        names = ", ".join(sorted(extra))
        verb = "doesn't" if len(extra) == 1 else "don't"
        return f"Your answer uses {names}, which {verb} appear in the expected answer."
    if _equal(expected, -got, abs_tol):
        return "Check the sign."
    conj = sp.conjugate(expected)
    if not _equal(conj, expected) and _equal(conj, got, abs_tol):
        return "Your answer is the complex conjugate of the expected one — check the sign of the imaginary part."
    if not _equal(conj, expected) and _equal(-conj, got, abs_tol):
        return "Close: check the sign of the real part."
    if not expected.free_symbols and not got.free_symbols:
        ve, vg = _numeric(expected), _numeric(got)
        if ve is not None and vg is not None and ve != 0:
            if ve.real and ve.imag and _close(complex(ve.imag, ve.real), vg, 1e-9):
                return "The real and imaginary parts look swapped."
            if _close(1 / ve, vg, 1e-9) and not _close(ve, vg, 1e-9):
                return "That's the reciprocal of the expected value."
            ratio = vg / ve
            if abs(ratio.imag) < 1e-9:
                factor = _nice_factor(ratio.real)
                if factor:
                    return factor
            rel = abs(vg - ve) / max(abs(ve), 1e-12)
            if explicit_tol is not None and rel < 1e-2:
                return f"Close, but outside the allowed tolerance (±{explicit_tol:g})."
            if rel < 5e-4 and _has_float(got) and not _has_float(expected):
                return "Numerically close, but an exact value is expected (a fraction, root or multiple of π)."
            if rel < 5e-4:
                return "Very close — give more precision."
            if rel < 1e-2:
                return "Close — check your arithmetic."
        return "Not quite."
    ratio = None
    if expected != 0:
        try:
            quotient = got / expected
            ratio = sp.simplify(quotient) if sp.count_ops(quotient) <= SIMPLIFY_MAX_OPS else None
        except Exception:  # best-effort hint only
            ratio = None
    if ratio is not None and ratio.is_Number:
        factor = _nice_factor(float(ratio))
        if factor:
            return factor
    return "Not equivalent to the expected answer."


def _nice_factor(r: float) -> str | None:
    for n in range(2, 11):
        if abs(r - n) < 1e-9:
            return f"Your answer is {n}× the expected value — a factor is off."
        if abs(r - 1 / n) < 1e-9:
            return f"Your answer is 1/{n} of the expected value — a factor is off."
    return None


# ---------------------------------------------------------------------------
# Structured comparison
# ---------------------------------------------------------------------------


@dataclass
class _Verdict:
    ok: bool
    feedback: str


def _flatten_vector(node: Node) -> Node:
    """A 1×n or n×1 matrix compares like a vector."""
    if node.kind == "matrix":
        if len(node.items) == 1:
            return node.items[0]
        if all(len(r.items) == 1 for r in node.items):
            return Node("tuple", items=[r.items[0] for r in node.items])
    return node


def _compare(expected: Node, got: Node, abs_tol: float | None, explicit_tol: float | None = None) -> _Verdict:
    if expected.kind == "scalar" and got.kind == "scalar":
        assert expected.expr is not None and got.expr is not None
        if _equal(expected.expr, got.expr, abs_tol):
            return _Verdict(True, "")
        return _Verdict(False, _near_miss(expected.expr, got.expr, abs_tol, explicit_tol))
    if expected.kind == "set":
        if got.kind == "scalar":
            got = Node("tuple", items=[got])
        return _compare_unordered(expected.items, got.items, abs_tol)
    expected, got = _flatten_vector(expected), _flatten_vector(got)
    if expected.kind == "scalar":
        return _Verdict(False, "A single value is expected, not a list.")
    if got.kind == "scalar":
        return _Verdict(False, f"Expected {len(expected.items)} components, e.g. (a, b, c).")
    if expected.kind == "matrix" and got.kind != "matrix":
        return _Verdict(False, "A matrix is expected, e.g. [[1, 2], [3, 4]].")
    if got.kind == "set" and expected.kind == "tuple":
        unordered = _compare_unordered(expected.items, got.items, abs_tol)
        if unordered.ok:
            return _Verdict(False, "Order matters here — write an ordered tuple ( … ), not a set { … }.")
        return _Verdict(False, "Expected an ordered tuple ( … ).")
    if len(expected.items) != len(got.items):
        noun = "rows" if expected.kind == "matrix" else "components"
        return _Verdict(False, f"Expected {len(expected.items)} {noun}, got {len(got.items)}.")
    results = [_compare(e, g, abs_tol, explicit_tol) for e, g in zip(expected.items, got.items)]
    wrong = [k for k, r in enumerate(results) if not r.ok]
    if not wrong:
        return _Verdict(True, "")
    if _compare_unordered(expected.items, got.items, abs_tol).ok:
        return _Verdict(False, "Right components, but in the wrong order.")
    label = "Row" if expected.kind == "matrix" else "Component"
    if len(wrong) == 1:
        detail = results[wrong[0]].feedback
        suffix = f" {detail}" if detail and detail not in ("Not quite.", "Not equivalent to the expected answer.") else ""
        return _Verdict(False, f"{label} {wrong[0] + 1} is not right.{suffix}")
    return _Verdict(False, f"{len(wrong)} of {len(results)} {label.lower()}s are not right.")


def _compare_unordered(expected: list[Node], got: list[Node], abs_tol: float | None) -> _Verdict:
    remaining = list(got)
    missing = 0
    for e in expected:
        match = next((k for k, g in enumerate(remaining) if _compare(e, g, abs_tol).ok), None)
        if match is None:
            missing += 1
        else:
            remaining.pop(match)
    if not missing and not remaining:
        return _Verdict(True, "")
    parts = []
    if missing:
        parts.append(f"{missing} expected element{'s are' if missing > 1 else ' is'} missing")
    if remaining:
        parts.append(f"{len(remaining)} element{'s don’t' if len(remaining) > 1 else ' doesn’t'} belong")
    return _Verdict(False, "; ".join(parts).capitalize() + ".")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_HELP = "Use e.g. 3/4, -2.5, sqrt(2)/2, 2+3i, x^2 or (1, -2, 3)."


def check_answer(
    kind: str,
    *,
    answer: str,
    correct_choice: int | None,
    tolerance: float | None,
    response: str | None,
    choice: int | None,
) -> GradeResult:
    if kind in ("multiple_choice", "true_false"):
        if choice is None:
            return GradeResult(False, "Choose one of the options.", invalid=True)
        if correct_choice is None:
            log.warning("choice question without correct_choice")
            return GradeResult(False, "This question has no answer key — please report it as wrong.")
        ok = int(choice) == int(correct_choice)
        return GradeResult(ok, "Correct." if ok else "Not this option.")
    if kind == "free_response":
        return GradeResult(False, "Compare your answer with the model solution and the checklist.")
    if kind in ("numeric", "expression"):
        return _check_math(kind, answer, tolerance, response)
    log.warning("unsupported question kind %r", kind)
    return GradeResult(False, f"Unsupported question kind: {kind}")


def _check_math(kind: str, answer: str, tolerance: float | None, response: str | None) -> GradeResult:
    try:
        got = parse_answer(response or "")
    except AnswerParseError as exc:
        return GradeResult(False, f"{exc} {_HELP}", invalid=True)
    try:
        expected = parse_answer(answer)
    except AnswerParseError:
        log.warning("answer key does not parse: %r", answer)
        same = _loose_text(answer) == _loose_text(response or "")
        return GradeResult(same, "Correct." if same else "This answer can't be checked automatically — compare with the solution.")
    abs_tol = tolerance if tolerance is not None else (DEFAULT_NUMERIC_TOL if kind == "numeric" else None)
    if kind == "numeric" and got.kind == "scalar" and got.expr is not None and got.expr.free_symbols:
        return GradeResult(False, "A number is expected here.")
    verdict = _compare(expected, got, abs_tol, tolerance)
    if not verdict.ok:
        return GradeResult(False, verdict.feedback or "Not quite.")
    if _same_text(answer, response or ""):
        return GradeResult(True, "Correct.")
    if tolerance is not None and _differs_numerically(expected, got):
        return GradeResult(True, "Correct (within the allowed tolerance).")
    return GradeResult(True, "Correct — equivalent to the expected answer.")


def _differs_numerically(expected: Node, got: Node) -> bool:
    if expected.kind == "scalar" and got.kind == "scalar" and expected.expr is not None and got.expr is not None:
        va, vb = _numeric(expected.expr), _numeric(got.expr)
        return va is not None and vb is not None and not _close(va, vb, EXACT_REL_TOL)
    return False


def _loose_text(s: str) -> str:
    return re.sub(r"[\s*]+", "", s or "").lower()


def _same_text(answer: str, response: str) -> bool:
    """Same answer up to spacing and explicit `*` (so '-7+22i' reads as identical to '-7+22*i')."""
    try:
        return _loose_text(normalize(answer)) == _loose_text(normalize(response))
    except AnswerParseError:
        return False


def is_checkable(kind: str, answer: str, correct_choice: int | None) -> bool:
    """True if a question's answer key can be graded automatically."""
    if kind in ("multiple_choice", "true_false"):
        return correct_choice is not None
    if kind in ("numeric", "expression"):
        try:
            parse_answer(answer)
        except AnswerParseError:
            return False
        return True
    return kind == "free_response"
