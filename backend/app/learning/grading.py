"""Answer checking (SPEC §6.1).

Public interface (stable):
    check_answer(kind, *, answer, correct_choice, tolerance, response, choice, require_simplified=True) -> GradeResult
    is_checkable(kind, answer, correct_choice, choices=None) -> bool  # does the answer key parse / exist?
    parse_true_false_set(text) -> str | None                # 'T F T', 'true,false,true' ... -> 'TFT'
    true_false_set_display(answer) -> str                   # 'FTT' -> '1 False · 2 True · 3 True'
    true_false_set_request(answer) -> str                   # 'Mark every statement true or false (3 statements).'

true_false_set ("mark each statement true or false"): the key is one letter per statement, in order ('FTT'). The
learner's response is read from T/F/true/false only — glued ('TFT'), separated ('T F T', 't,f,t', 'true false true')
and optionally numbered ('1. T 2. F 3. T'); anything else, or a different number of marks, is invalid (asked again,
never counted). A wrong answer names exactly the statements marked wrong ("Statement 2 is actually false; statement 3
is actually true.") — that is the point of the format, so it never collapses into "how many are true?".

Math answers are parsed with SymPy's parser from a heavily normalized string (unicode, LaTeX-ish input,
`i`/`j` as the imaginary unit, implicit multiplication). The parser only ever sees a whitelisted character
set and identifiers resolved to SymPy objects, and every expression is size-checked on an unevaluated tree
before SymPy is allowed to evaluate it (so inputs like 9^9^9 are rejected instead of hanging). On top of that,
math grading runs in a separate worker process with a hard time budget (GRADE_TIMEOUT_SECONDS): a pathological
input is reported as "too complex to check" and the worker is killed, so grading can never block the server.

Symbol convention: names starting with z or w are complex (z, w, z_1, w2 ...); every other letter is real.
This mirrors complex-number texts (z = a + bi with a, b real) and lets |z|^2 and z^2 be told apart.
Indexed names are canonical: x1, x_1, x_{1} and x₁ are the same symbol x_1 (xₖ is x_k). A one-letter index glued to
its coefficient splits off the same way: 'ax_k' is a·x_k and 'a_kx_k' is a_k·x_k. A longer index stays one name —
'a_ij', 'a_{ij}' and 'x_ky' are the symbols a_ij and x_ky, as in an answer key written for SymPy; a response whose
unknown name would read as the key's symbols with a one-letter index ('x_ky' for x_k·y) is asked again, not counted.

Letter runs: 'xy' is x*y, glued function names are peeled off ('sinx' -> sin(x), 'xsin(x)' -> x*sin(x)),
're'/'im'/'arg' are functions only when applied to something ('re z', 'arg(z)'; but 're^(iθ)' is r·e^(iθ)), \\Re,
\\Im and \\arg are always functions, and an ASCII Greek name typed by the
learner ('xi', 'eta', 'nu' ...) is kept whole only if the answer key uses that letter ('2xi' is 2·x·i).

require_simplified (learner path): when the expected answer is a numeric constant in reduced form and the
response has the same value but is not reduced (e.g. '(2+3i)(4+5i)' for -7+22i, '3+4-2i', '(1+2, 3)'), the
result is invalid ("Same value — now simplify it …"): the learner is asked again and nothing is recorded.
A linear combination of lists ('(-5, 5, 2) + 4(1, 5, -4)') is read componentwise, so retyping a vector question's
own expression gets the same "simplify" message; any other arithmetic on lists asks for the resulting list.
An irreducible radical typed in its textbook form (∛4, \\sqrt[4]{8}) counts as reduced even though SymPy rewrites it
onto a smaller base (2^(2/3), 2^(3/4)).

Radicals (policy): a root left in a denominator counts as reduced — 1/√2, i/√2, 3/√6 are accepted for √2/2, √2/2·i,
√6/2. Rationalising the denominator is a presentation convention the course text (Axler) does not insist on (it writes
factors like 1/√2 itself); asking for it again would only cost the learner a retry. A root that can still be
simplified is not reduced, and when that is the only thing left to do the message names it instead of the generic one:
a fraction or decimal inside a root (√(1/2), √1.5 -> "no fraction inside a root"), roots of the same index in one term
(√3/√2, √2·√3 -> "combine roots in the same term into one"), a factor that comes out of the root (√8, ∛16 -> "take
factors out of a root"). Sets and lists get the same message per component ("Same values — … in each component").
Typing: '√½' is √(1/2) and '2½' is 5/2 (a vulgar fraction is one number), and 'sqrt2' / 'sqrt 2' is sqrt(2) like √2
('1/sqrt2 i' is i/√2, not 1/√(2i)).
"""

from __future__ import annotations

import atexit
import json
import logging
import math
import os
import random
import re
import select
import subprocess
import sys
import threading
import time
import unicodedata
import warnings
from dataclasses import dataclass, field
from keyword import iskeyword
from pathlib import Path

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    function_exponentiation,
    implicit_application,
    implicit_multiplication,
    parse_expr,
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
MAX_EXPONENT = 1e4  # |exponent| bound (symbols probed at ~10) for any power
MAX_FUNC_DEPTH = 6  # nested function applications, e.g. cos(cos(cos(...)))
SIMPLIFY_MAX_OPS = 40
EXACT_REL_TOL = 1e-10  # "exact" comparison of numbers typed as decimals
DEFAULT_NUMERIC_TOL = 1e-6
GRADE_TIMEOUT_SECONDS = 5.0  # hard budget for one math grading call (worker process is killed after it)

TOO_COMPLEX = "That answer is too complex to check quickly — simplify it and try again."

# ---------------------------------------------------------------------------
# Normalization: unicode + LaTeX-ish input -> plain SymPy syntax
# ---------------------------------------------------------------------------

_SET_OPEN, _SET_CLOSE = "⦃", "⦄"  # placeholders for set braces while LaTeX groups are rewritten

_GREEK = [
    "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa", "lamda", "mu",
    "nu", "xi", "omicron", "rho", "sigma", "tau", "upsilon", "phi", "chi", "psi", "omega",
]  # fmt: skip
ALL_GREEK = frozenset(_GREEK)
_GREEK_CHARS = {unicodedata.lookup(f"GREEK SMALL LETTER {name.upper()}"): name for name in _GREEK}
_GREEK_CHARS["π"] = "pi"
_GREEK_CHARS["ϕ"] = "phi"
_GREEK_CHARS["ε"] = "epsilon"
_GREEK_CHARS["ϵ"] = "epsilon"
_GREEK_CHARS["ϑ"] = "theta"

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
_SUBSCRIPT_DIGITS = {chr(0x2080 + d): str(d) for d in range(10)}
# letter subscripts Unicode has (xₖ is x_k); NFKC would turn them into plain letters (x·k)
_SUBSCRIPT_LETTERS = dict(zip("ₐₑₒₓₕₖₗₘₙₚₛₜᵢᵣᵤᵥⱼ", "aeoxhklmnpstiruvj"))
_SUBSCRIPTS_ALL = {**_SUBSCRIPT_DIGITS, **_SUBSCRIPT_LETTERS}

_LATEX_SYMBOLS = {
    "cdot": "*", "times": "*", "ast": "*", "div": "/", "pi": "pi", "lambda": "lamda", "Re": "Re", "Im": "Im",
    "sin": "sin", "cos": "cos", "tan": "tan", "ln": "ln", "log": "log", "exp": "exp", "arg": "Arg",
    "arcsin": "asin", "arccos": "acos", "arctan": "atan", "sinh": "sinh", "cosh": "cosh", "tanh": "tanh",
    "lvert": "|", "rvert": "|", "vert": "|", "mid": "|", "lVert": "|", "rVert": "|",
    "varepsilon": "epsilon", "varphi": "phi", "vartheta": "theta", "imath": "i",
}  # fmt: skip
_LATEX_SYMBOLS.update({g: g for g in _GREEK if g != "lamda"})

_LATEX_SPACES = ("\\,", "\\;", "\\:", "\\!", "\\ ", "\\quad", "\\qquad", "\\displaystyle", "\\textstyle", "~")
_LATEX_DELIM_SIZERS = re.compile(r"\\(left|right|bigl|bigr|Bigl|Bigr|biggl|biggr|Biggl|Biggr|big|Big|bigg|Bigg)(?![A-Za-z])")
_WRAPPERS = ("mathbf", "mathbb", "mathrm", "mathit", "boldsymbol", "bm", "vec", "text", "textbf", "textit", "operatorname", "mathsf")
# The empty set (a solution set with no element) in every notation a learner or an answer key uses → "{}"; SymPy's
# names of the number fields → the book's letters (an answer "all of C" is keyed "C").
_EMPTY_SET = re.compile(r"\\varnothing|\\emptyset|∅|Ø|(?<![A-Za-z.])(?:S\.)?EmptySet\b|\bempty(?:\s+set)?\b", re.IGNORECASE)
_FIELD_NAMES = ((re.compile(r"(?<![A-Za-z.])(?:S\.)?Complexes\b"), "C"), (re.compile(r"(?<![A-Za-z.])(?:S\.)?Reals\b"), "R"))
_MATRIX_ENV = re.compile(r"\\begin\{([pbBvV]?matrix|smallmatrix|array)\}(?:\{[^}]*\})?(.*?)\\end\{\1\}", re.S)

_FUNCTIONS: dict[str, object] = {
    "sqrt": sp.sqrt, "cbrt": sp.cbrt, "root": sp.root, "exp": sp.exp, "ln": sp.log, "log": sp.log,
    "sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
    "arcsin": sp.asin, "arccos": sp.acos, "arctan": sp.atan, "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
    "abs": sp.Abs, "Abs": sp.Abs, "conj": sp.conjugate, "conjugate": sp.conjugate, "bar": sp.conjugate,
    "re": sp.re, "Re": sp.re, "im": sp.im, "Im": sp.im, "arg": sp.arg, "Arg": sp.arg,
}  # fmt: skip
_CONSTANTS: dict[str, object] = {"i": sp.I, "j": sp.I, "I": sp.I, "e": sp.E, "E": sp.E, "pi": sp.pi}
_RESERVED = {"Symbol", "Integer", "Float", "Rational", "Number", "Add", "Mul", "Pow"}

_KNOWN_NAMES = frozenset(_FUNCTIONS) | {"pi"}  # never split into letters
_PAREN_ONLY = frozenset({"re", "im", "arg", "bar"})  # functions only when called: 're^(iθ)' is r·e^(iθ)
# Function names peeled off a glued letter run ('sinx', 'xsin', 'rcos', 'isqrt'), longest first.
_PEEL_FUNCTIONS = (
    "conjugate", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "sqrt", "cbrt", "asin", "acos", "atan",
    "conj", "root", "sin", "cos", "tan", "exp", "abs", "log", "ln",
)  # fmt: skip


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


def _rewrite_commands(s: str, greek_seen: set[str]) -> str:
    """Rewrite argument-taking LaTeX commands (\\frac, \\sqrt, wrappers, \\bar ...) recursively.

    Every emitted function name is preceded by a space so it never fuses with a preceding letter
    ('i\\sqrt{3}' -> 'i sqrt(3)', 'z\\bar{z}' -> 'z conjugate(z)')."""
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
            out.append(f"(({_rewrite_commands(num, greek_seen)})/({_rewrite_commands(den, greek_seen)}))")
        elif name == "sqrt":
            index = None
            if pos < len(s) and s[pos] == "[":
                close = s.find("]", pos)
                if close < 0:
                    raise AnswerParseError("Unbalanced brackets in \\sqrt[...].")
                index, pos = s[pos + 1 : close], close + 1
            arg, pos = _take_group(s, pos)
            inner = _rewrite_commands(arg, greek_seen)
            out.append(f"(({inner})^(1/({_rewrite_commands(index, greek_seen)})))" if index else f" sqrt({inner})")
        elif name in ("overline", "bar", "conj"):
            arg, pos = _take_group(s, pos)
            out.append(f" conjugate({_rewrite_commands(arg, greek_seen)})")
        elif name in _WRAPPERS:
            arg, pos = _take_group(s, pos)
            out.append(f"({_rewrite_commands(arg, greek_seen)})" if name not in ("text", "operatorname") else f" {arg.strip()} ")
        elif name in _LATEX_SYMBOLS:
            target = _LATEX_SYMBOLS[name]
            if target in ALL_GREEK:
                greek_seen.add(target)
            out.append(f" {target}" + ("" if s[pos : pos + 1] in tuple("_0123456789") else " "))  # \\theta_k keeps its index
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


def _replace_subscripts(s: str) -> str:
    """x₁₂ -> x_12, xₖ -> x_k (before NFKC would turn them into x12, xk); the run ends the index: xₖy is x_k·y."""

    def index(m: re.Match[str]) -> str:
        after = m.string[m.end() : m.end() + 1]
        return "_" + "".join(_SUBSCRIPTS_ALL[c] for c in m.group(0)) + ("*" if after.isdigit() else " " if after.isalpha() else "")

    return re.sub("[" + "".join(_SUBSCRIPTS_ALL) + "]+", index, s)


def _spaced(name: str):
    """A name substituted into the text, kept glued to an index that follows it (θ₁ -> theta_1, ell_k -> l_k)."""
    return lambda m: f" {name}" + ("" if m.string[m.end() : m.end() + 1] in tuple("_0123456789") else " ")


_VULGAR_FRACTION = "[\u00bc-\u00be\u2150-\u215e]"  # ¼ ½ ¾ ⅐ … ⅞


def _replace_vulgar_fractions(s: str) -> str:
    """½ -> (1/2) so that √½ is √(1/2), not √1/2; a mixed number 2½ -> (5/2), not 21/2 (before NFKC)."""

    def frac(ch: str) -> tuple[int, int]:
        num, den = unicodedata.normalize("NFKC", ch).split("\u2044")
        return int(num), int(den)

    def mixed(m: re.Match[str]) -> str:
        num, den = frac(m.group(2))
        return f"({int(m.group(1)) * den + num}/{den})"

    s = re.sub(r"(?<![\d.])(\d{1,6}) ?(" + _VULGAR_FRACTION + ")", mixed, s)
    return re.sub(_VULGAR_FRACTION, lambda m: "({}/{})".format(*frac(m.group(0))), s)


def _replace_sqrt_sign(s: str) -> str:
    """√3 -> ' sqrt(3)' (the leading space keeps 'i√3' from becoming the identifier 'isqrt'); ∛ likewise."""
    s = re.sub(r"∛\s*([0-9.]+|[A-Za-z]\w*)", r" cbrt(\1)", s).replace("∛", " cbrt")
    s = re.sub(r"√\s*([0-9.]+|[A-Za-z]\w*)", r" sqrt(\1)", s)
    return s.replace("√", " sqrt")


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
                out.append(" Abs(")  # leading space: 'x|y|' must not become the identifier 'xAbs'
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

    return re.sub(r"(?<![A-Za-z_])([A-Za-z_][A-Za-z0-9_]*)\s*\(", repl, s)  # also after a number: 2x(a+b), 2x_k(a+b)


_TOKEN = re.compile(r"\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|[A-Za-z_][A-Za-z0-9_]*")
# letters, then (index, letters)*, then an optional index: unambiguous, so matching is linear (no nested
# quantifiers: a letter run ending in "_" must not backtrack exponentially).
_GLUED_KEYWORDS = frozenset({"as", "in", "is"})  # Python keywords that are two glued letters in a math answer
_INDEXED_WORD = re.compile(r"[A-Za-z]+(?:_?\d+[A-Za-z]+)*(?:_?\d+)?")
# names with a one-letter index, glued as typed: 'ax_k' (a·x_k), 'a_kx_k' (a_k·x_k). Every segment ends at its index
# letter: 'a_ij' or 'x_ky' is one name with a longer index (keys written for SymPy name a matrix entry a_ij).
_LETTER_INDEXED_WORD = re.compile(r"(?:[A-Za-z]+_[A-Za-z])+")
# one piece of a name as a learner may have meant it: letters, then a one-letter or digit index ('x_ky' -> x_k, y)
_INDEXED_PIECE = re.compile(r"([A-Za-z]+)(?:_(\d+|[A-Za-z])|(\d+))?")


def _peel(word: str, call: bool, keep_greek: frozenset[str], peel: tuple[str, ...]) -> list[str]:
    """One alphabetic run -> tokens: known names stay whole, glued function/Greek names are peeled off,
    everything else becomes single letters (implicit multiplication)."""
    if len(word) > 1 and word not in _KNOWN_NAMES and word.lower() in _KNOWN_NAMES:
        word = word.lower()  # Sqrt, Pi, SIN
    if word in _PAREN_ONLY:
        return [word] if call else list(word)
    if len(word) == 1 or word in _KNOWN_NAMES or word in keep_greek:
        return [word]
    out: list[str] = []
    i = 0
    while i < len(word):
        name = next((n for n in peel if word.startswith(n, i)), None)
        if name:
            out.append(name)
            i += len(name)
        else:
            out.append(word[i])
            i += 1
    return out


# a sign, spaces, then a number or a name the normaliser itself wrote for a glyph ('-π' -> '- pi '): a signed value
_SIGNED_VALUE = re.compile(r"[+-]\s+(?:\(\s*)*(?:\.?\d|(?:sqrt|Abs|pi|l|" + "|".join(_GREEK) + r")(?:_[A-Za-z0-9]+|\d+)?\b)")


def _between_words(s: str, start: int, end: int) -> bool:
    """Is s[start:end] a word of a sentence ('x is 5', 'x is -12', 'λ in C', 'in C', 'C as') rather than two glued
    letters ('as+bs', 'as + bs', '2in', 'in(x+1)', 'as' alone)? A word stands apart (whitespace or an end on both
    sides) and has a word or a number on at least one side — a sign glued to what follows starts a number."""
    if start and not s[start - 1].isspace() or end < len(s) and not s[end].isspace():
        return False  # glued to something
    left, right = s[:start].rstrip(), s[end:].lstrip()
    word_left = bool(left) and (left[-1].isalnum() or left[-1] in "_)")
    word_right = bool(right) and (
        right[0].isalnum() or right[0] in "_(" or (right[0] in "+-." and right[1:2] != "" and (right[1].isalnum() or right[1] in "(."))
        or bool(_SIGNED_VALUE.match(right))  # 'x is - 12', 'y is -π' (written '- pi '), 'y is -√2'
    )
    return (word_left or not left) and (word_right or not right) and (word_left or word_right)


def _split_identifiers(s: str, keep_greek: frozenset[str]) -> str:
    """Make every identifier explicit before SymPy sees it (see the module docstring)."""
    peel = tuple(sorted(set(_PEEL_FUNCTIONS) | {"pi"} | keep_greek, key=len, reverse=True))

    def repl(m: re.Match[str]) -> str:
        tok = m.group(0)
        if not (tok[0].isalpha() or tok[0] == "_"):
            return tok  # a number
        if iskeyword(tok):
            if tok in _GLUED_KEYWORDS and not _between_words(s, m.start(), m.end()):  # 'as+bs' is a·s + b·s
                return " ".join(tok)
            raise AnswerParseError(f"Unexpected word: {tok}")  # 'x is 5', 'λ in C': a sentence, asked again
        rest = s[m.end() :].lstrip()
        call = rest.startswith("(")
        if tok in _PAREN_ONLY and rest[:1] and (rest[0].isalnum() or rest[0] in "(_."):
            call = True  # 'arg z', 're z', 'im z_1', 'arg |z|' (-> ' Abs(') are applications; 're**(it)' is not
        if "_" not in tok and not any(c.isdigit() for c in tok):
            return " ".join(_peel(tok, call, keep_greek, peel))
        if _LETTER_INDEXED_WORD.fullmatch(tok):  # ax_k -> a x_k, as ax_1 -> a x_1
            parts = []
            for letters, index in re.findall(r"([A-Za-z]+)_([A-Za-z])", tok):
                pieces = _peel(letters, False, keep_greek, peel)
                if pieces[-1] in _KNOWN_NAMES:
                    return tok  # sqrt_k ... (checked later)
                pieces[-1] = f"{pieces[-1]}_{index}"
                parts.extend(pieces)
            return " ".join(parts)
        if not _INDEXED_WORD.fullmatch(tok):
            return tok  # a_ij, x_ky, __x ... (checked later)
        parts: list[str] = []
        segments = re.findall(r"([A-Za-z]+)(?:_?(\d+))?", tok)
        for n, (letters, index) in enumerate(segments):
            pieces = _peel(letters, call and n == len(segments) - 1 and not index, keep_greek, peel)
            if index:
                if pieces[-1] in _KNOWN_NAMES:
                    pieces.append(index)  # sqrt2 -> sqrt 2
                else:
                    pieces[-1] = f"{pieces[-1]}_{index}"  # x1, x_1, theta1 -> x_1, theta_1
            parts.extend(pieces)
        return " ".join(parts)

    return _TOKEN.sub(repl, s)


_BARE_ROOT_NUMBER = re.compile(r"(?<![A-Za-z_])(sqrt|cbrt) ?(\d+\.?\d*|\.\d+)(?![\d.(])")


def normalize(text: str, keep_greek: frozenset[str] | None = None) -> str:
    """Learner / answer-key text -> plain SymPy-ish syntax (may still contain [], set placeholders and commas).

    keep_greek: ASCII Greek names kept whole (None = all). Greek typed as unicode or LaTeX is always kept."""
    if text is None:
        raise AnswerParseError("Empty answer.")
    s = text.strip()
    if not s:
        raise AnswerParseError("Empty answer.")
    if len(s) > MAX_INPUT_CHARS:
        raise AnswerParseError("That answer is too long to check.")
    greek_seen: set[str] = set()
    s = _replace_superscripts(s)
    s = _replace_subscripts(s)
    s = _replace_vulgar_fractions(s)
    s = unicodedata.normalize("NFKC", s)  # also turns the script letter ℓ into l
    # ℓ typed as "\ell" or spelled "ell" is the variable l, not e·l·l.
    s = re.sub(r"\\ell(?![A-Za-z])", _spaced("l"), s)
    s = re.sub(r"(?<![A-Za-z\\_])(?<!_\{)ell(?![A-Za-z])", _spaced("l"), s)  # (x_ell, x_{ell} keep the index 'ell')
    for src, dst in _UNICODE_OPS.items():
        s = s.replace(src, dst)
    s = _EMPTY_SET.sub("{}", s)
    for pattern, letter in _FIELD_NAMES:
        s = pattern.sub(letter, s)
    for src, dst in _GREEK_CHARS.items():
        if src in s:
            if dst in ALL_GREEK:
                greek_seen.add(dst)
            s = re.sub(re.escape(src), _spaced(dst), s)
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
    s = re.sub(r"_\{([A-Za-z0-9]+)\}(?=[A-Za-z])", r"_\1 ", s)  # the braces end the index: x_{k}y is x_k·y
    s = re.sub(r"_\{([A-Za-z0-9]+)\}(?=\d)", r"_\1*", s)  # x_{1}2 is x_1·2
    s = re.sub(r"_\{([A-Za-z0-9]+)\}", r"_\1", s)  # x_{12} -> x_12
    s = _rewrite_commands(s, greek_seen)
    s = s.replace("{", "(").replace("}", ")")
    s = _convert_abs_bars(s)
    s = _strip_assignment(s)
    s = s.replace("^", "**")
    s = s.replace("lambda", "lamda")  # SymPy's spelling, also glued: 'alambda_k', 'lambda1' (lambda is a keyword)
    keep = ALL_GREEK if keep_greek is None else frozenset(keep_greek) | greek_seen
    s = _split_identifiers(s, keep)
    s = re.sub(r"(\d)(?=j|J(?=_|\d))", r"\1 ", s)  # 2j_k, 2J_k are 2·j_k, 2·J_k; 2j and 2J alone stay 2i (literals)
    s = re.sub(r"(?<![\w.])0(?=[xXbBoO])", "0 ", s)  # 0x_1 is 0·x_1, not a hex literal
    s = _BARE_ROOT_NUMBER.sub(r"\1(\2)", s)  # 'sqrt2 i' / 'sqrt 2 i' -> sqrt(2) i, like √2 i (not sqrt(2i))
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
    raw: sp.Expr | None = None  # scalar: the unevaluated parse tree (the form the learner typed)
    arithmetic: bool = False  # tuple: read from list arithmetic ('i(2,-1)', '(1,2)+(0,1)'), not typed as a list

    def size(self) -> int:
        return 1 if self.kind == "scalar" else sum(i.size() for i in self.items)

    def scalars(self) -> list[Node]:
        return [self] if self.kind == "scalar" else [s for i in self.items for s in i.scalars()]


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
    if "," in s:
        combined = _parse_list_arithmetic(s)
        if combined is not None:
            return combined
    expr, raw = _parse_scalar(s)
    return Node("scalar", expr=expr, raw=raw)


LIST_ARITHMETIC = "Enter the resulting list itself — work out each component first."
_LIST_PLACEHOLDER = "listarg"
_MAX_LIST_TERMS = 8


def _tuple_groups(s: str) -> list[tuple[int, int, list[str]]]:
    """Bracketed flat lists inside a scalar-looking string: [(open, close, components)]. A group that is a
    function's argument list ('root(8, 3)') is not a list; a list nested in a list raises."""
    groups: list[tuple[int, int, list[str]]] = []
    k = 0
    while k < len(s):
        if s[k] not in "([":
            k += 1
            continue
        close = _matching_close(s, k)
        if close < 0:
            raise AnswerParseError("Unbalanced brackets.")
        parts = _split_top(s[k + 1 : close])
        before = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", s[:k])
        if len(parts) < 2 or (before and before.group(1) in _FUNCTIONS):
            k += 1  # not a list: look inside it
            continue
        if any("," in p or "[" in p for p in parts):
            raise AnswerParseError(LIST_ARITHMETIC)
        groups.append((k, close, parts))
        k = close + 1
    return groups


def _parse_list_arithmetic(s: str) -> Node | None:
    """'(-5, 5, 2) + 4(1, 5, -4)' -> the list of component expressions ('(-5)+4(1)', ...), so the value can be
    checked (and an unsimplified form gets the D3 'simplify' message). Only linear combinations of lists of the
    same length are read this way; other arithmetic on lists is unreadable. None if `s` contains no list."""
    groups = _tuple_groups(s)
    if not groups:
        return None
    sizes = {len(parts) for _, _, parts in groups}
    if len(sizes) != 1 or len(groups) > _MAX_LIST_TERMS:
        raise AnswerParseError(LIST_ARITHMETIC)

    def substitute(pick) -> str:
        out, last = [], 0
        for j, (start, close, parts) in enumerate(groups):
            out.append(s[last:start])
            out.append(f"({pick(j, parts)})")
            last = close + 1
        out.append(s[last:])
        return "".join(out)

    names = [_LIST_PLACEHOLDER + chr(ord("a") + j) for j in range(len(groups))]
    try:
        shape, _ = _parse_scalar(substitute(lambda j, parts: names[j]))
        placeholders = [sym for sym in shape.free_symbols if sym.name in names]
        poly = sp.Poly(shape, *placeholders) if placeholders and shape.is_polynomial(*placeholders) else None
    except AnswerParseError:
        raise AnswerParseError(LIST_ARITHMETIC) from None
    except Exception:  # SymPy: not a polynomial in the placeholders
        poly = None
    if poly is None or not all(sum(m) == 1 for m in poly.monoms()):
        raise AnswerParseError(LIST_ARITHMETIC)  # e.g. (1, 2)(3, 4), |(1, 2)|, 1 + (2, 3)
    items = []
    for k in range(next(iter(sizes))):
        expr, raw = _parse_scalar(substitute(lambda j, parts: parts[k]))
        items.append(Node("scalar", expr=expr, raw=raw))
    return Node("tuple", items=items, arithmetic=True)


_ALLOWED = re.compile(r"^[A-Za-z0-9_+\-*/(). ]*$")


def _make_symbol(name: str) -> sp.Symbol:
    m = re.fullmatch(r"([A-Za-z]+?)_?(\d+)", name)
    if m:
        name = f"{m.group(1)}_{m.group(2)}"  # x1 == x_1
    return sp.Symbol(name) if name[0] in "zw" else sp.Symbol(name, real=True)


# Identifier splitting happens in normalize() (_split_identifiers), so SymPy's split_symbols is not used.
_TRANSFORMS = standard_transformations + (
    convert_xor,
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
    if re.search(r"\d\s+\.?\d", s):
        raise AnswerParseError("Unexpected space inside a number — write 1000, not 1 000.")
    for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", s):
        if iskeyword(name) or name in _RESERVED or name.startswith("_"):
            raise AnswerParseError(f"Unexpected word: {name}")


_PROBE = sp.Float(9.7)  # stand-in value for symbols when bounding exponents ("symbols count as ~10")


def _exponent_size(exponent: sp.Expr) -> float:
    """A realistic bound on |exponent| (a Rational p/q counts as p/q, symbols as ~10)."""
    if exponent.is_Number:
        value = abs(complex(exponent))
        return value if math.isfinite(value) else math.inf
    bound = _log_magnitude(exponent)  # recursive guard: inner towers are rejected before any evaluation
    if bound > 20:
        raise AnswerParseError("That number is too large to check.")
    probe = exponent.xreplace({s: _PROBE for s in exponent.free_symbols})
    value = _numeric(probe)
    return abs(value) if value is not None else 10**bound


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
        e_abs = _exponent_size(exponent)
        if e_abs > MAX_EXPONENT:
            raise AnswerParseError("That number is too large to check.")
        if base.free_symbols and exponent.is_Number and e_abs > MAX_SYMBOLIC_EXPONENT:
            raise AnswerParseError("That exponent is too large to check.")
        return max(_log_magnitude(base), 1.0) * (e_abs + 1)
    child = [_log_magnitude(a) for a in node.args]
    if isinstance(node, sp.Mul):
        return sum(child)
    if isinstance(node, sp.Add):
        return max(child) + math.log10(len(child))
    return max(child, default=1.0) + 1.0


def _function_depth(node: sp.Basic) -> int:
    inner = max((_function_depth(a) for a in node.args), default=0)
    return inner + 1 if isinstance(node, sp.Function) else inner


def _validate_raw(raw: sp.Basic) -> None:
    """Size checks on the unevaluated tree, before SymPy is allowed to evaluate anything."""
    if not isinstance(raw, sp.Expr):
        raise AnswerParseError("Couldn't read that expression.")
    nodes = list(sp.preorder_traversal(raw))
    if len(nodes) > MAX_NODES:
        raise AnswerParseError("That expression is too long to check.")
    if any(not isinstance(n, sp.Basic) or isinstance(n, sp.Tuple) for n in nodes):
        raise AnswerParseError("Couldn't read that expression.")  # e.g. 'cos cos', 'x(1, 2)'
    if _function_depth(raw) > MAX_FUNC_DEPTH:
        raise AnswerParseError("That expression is nested too deeply to check.")
    if _log_magnitude(raw) > MAX_LOG10:
        raise AnswerParseError("That number is too large to check.")


def _parse_scalar(s: str) -> tuple[sp.Expr, sp.Expr]:
    """-> (evaluated expression, unevaluated tree)."""
    _check_tokens(s)
    local = {**_FUNCTIONS, **_CONSTANTS}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # SymPy deprecation chatter on malformed input
        try:
            raw = parse_expr(s, local_dict=local, global_dict=dict(_GLOBALS), transformations=_TRANSFORMS, evaluate=False)
            _validate_raw(raw)
        except AnswerParseError:
            raise
        except Exception as exc:  # SymPy raises a zoo of exception types for malformed input
            raise AnswerParseError("Couldn't read that expression.") from exc
        try:
            expr = parse_expr(s, local_dict=local, global_dict=dict(_GLOBALS), transformations=_TRANSFORMS)
            undefined = not isinstance(expr, sp.Expr) or expr.has(sp.zoo, sp.nan, sp.oo, -sp.oo)
        except Exception as exc:
            raise AnswerParseError("Couldn't read that expression.") from exc
    if undefined:
        raise AnswerParseError("That expression is undefined (division by zero?).")
    return expr, raw


def parse_answer(text: str, keep_greek: frozenset[str] | None = None) -> Node:
    node = _parse_structure(normalize(text, keep_greek))
    if node.size() > MAX_ITEMS:
        raise AnswerParseError("Too many components to check.")
    return node


def _greek_names(node: Node) -> frozenset[str]:
    """Greek letters used as symbols in a parsed answer key (theta, theta_1 ...)."""
    names = set()
    for scalar in node.scalars():
        assert scalar.expr is not None
        for sym in scalar.expr.free_symbols:
            base = re.sub(r"_(?:\d+|[A-Za-z])$", "", sym.name)  # theta_1, theta_k
            if base in ALL_GREEK:
                names.add(base)
    return frozenset(names)


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
    if not a.free_symbols and not b.free_symbols:
        # Constants: high-precision numeric evaluation decides (simplify can take minutes on function towers).
        va, vb = _numeric(a), _numeric(b)
        if va is not None and vb is not None:
            if abs_tol is not None:
                return abs(va - vb) <= max(abs_tol, 1e-9 * abs(va))
            return _close(va, vb, EXACT_REL_TOL)
        try:
            return sp.count_ops(diff) <= SIMPLIFY_MAX_OPS and sp.simplify(diff) == 0
        except Exception:
            return False
    try:
        if sp.count_ops(diff) <= SIMPLIFY_MAX_OPS and sp.simplify(diff) == 0:
            return True
    except Exception:  # simplify can fail on odd inputs; fall through to numeric checks
        log.debug("simplify failed for %s vs %s", a, b, exc_info=True)
    return bool(_sampled_relation(a, b, lambda x, y: _close(x, y, 1e-9)))


def _has_float(expr: sp.Expr) -> bool:
    return any(isinstance(atom, sp.Float) for atom in expr.atoms(sp.Number))


_NEAR_EXACT = "Numerically close, but an exact value is expected (a fraction, root or multiple of π)."


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
                return _NEAR_EXACT
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
# Simplified form (D3): is an unevaluated constant tree in reduced literal form?
# ---------------------------------------------------------------------------

_TRANSCENDENTAL = (sp.log, sp.sin, sp.cos, sp.tan, sp.asin, sp.acos, sp.atan, sp.sinh, sp.cosh, sp.tanh, sp.exp)


def _ev(node: sp.Basic) -> sp.Basic:
    """Evaluate an unevaluated tree bottom-up (cheap: only used on small constant trees)."""
    if not node.args or node.is_Atom:
        return node
    return node.func(*[_ev(a) for a in node.args])


def _number_sign(node: sp.Basic) -> tuple[int, sp.Basic]:
    if node.is_Number and node.is_negative:
        return -1, -node
    return 1, node


def _collect(node: sp.Basic, num: list[sp.Basic], den: list[sp.Basic]) -> None:
    """Flatten a product into numerator / denominator factors (Pow(x, -1) moves x to the denominator)."""
    if isinstance(node, sp.Mul):
        for a in node.args:
            _collect(a, num, den)
    elif isinstance(node, sp.Pow) and _ev(node.exp) == -1:
        sub_num: list[sp.Basic] = []
        sub_den: list[sp.Basic] = []
        _collect(node.base, sub_num, sub_den)
        num.extend(sub_den)
        den.extend(sub_num)
    else:
        num.append(node)


def _terms(node: sp.Basic) -> list[sp.Basic]:
    if isinstance(node, sp.Add):
        return [t for a in node.args for t in _terms(a)]
    return [node]


# A root that is fine except that it can still be simplified (see _unsimplified_feedback); each names the step left.
ROOT_FRACTION = "no fraction inside a root (√(1/2) = √2/2)"
ROOT_COMBINE = "combine roots in the same term into one (√3/√2 = √6/2)"
ROOT_FACTOR = "take factors out of a root (√8 = 2√2)"
_ROOT_DEFECTS = (ROOT_FRACTION, ROOT_COMBINE, ROOT_FACTOR)


def _literal_factor(f: sp.Basic, defects: list[str] | None = None) -> bool:
    """An irreducible real non-number factor: pi, e, an irreducible radical sqrt(2) / 2^(1/3), e^2, log(2) ...

    defects (lenient mode, for the feedback only): a root that is irrational but not reduced — a fraction or decimal
    inside it (sqrt(1/2)), or a factor that comes out of it (sqrt(8)) — is accepted and its defect appended."""
    if f in (sp.pi, sp.E):
        return True
    if isinstance(f, sp.Pow):
        base, exp = _ev(f.base), _ev(f.exp)
        if base.has(sp.I) or exp.has(sp.I) or not exp.is_Rational or exp.is_integer and base not in (sp.pi, sp.E):
            return False
        if base in (sp.pi, sp.E):
            return True
        if not (base.is_positive and (base.is_Integer or _is_reduced_number(f.base, real_only=True))):
            return False
        value = sp.Pow(base, exp)
        if isinstance(value, sp.Pow) and value.base == base and value.exp == exp:
            return True
        # SymPy rewrites an irreducible radical onto a smaller base (4**(1/3) -> 2**(2/3), 8**(1/4) -> 2**(3/4),
        # 12**(1/3) -> 2**(2/3)*3**(1/3)). That is still reduced when nothing was pulled out of the root
        # (sqrt(8) -> 2*sqrt(2)) and the root index did not shrink (4**(1/4) -> sqrt(2), 36**(1/4) -> sqrt(6)).
        if base.is_Integer and base > 1 and 0 < exp < 1:
            coeff, rest = value.as_coeff_Mul()
            parts = rest.args if isinstance(rest, sp.Mul) else (rest,)
            same_index = all(
                isinstance(p, sp.Pow) and p.base.is_Integer and p.exp.is_Rational and p.exp.q == exp.q for p in parts
            )
            if same_index and coeff != 1 and defects is not None:
                defects.append(ROOT_FACTOR)  # sqrt(8) = 2*sqrt(2), ∛16 = 2∛2
                return True
            return coeff == 1 and same_index
        if defects is not None and base % 1 != 0 and 0 < abs(exp) < 1 and not value.is_Rational:
            defects.append(ROOT_FRACTION)  # sqrt(1/2), sqrt(3/2), sqrt(1.5): irrational, but not reduced
            return True
        return False
    if isinstance(f, sp.exp):
        return _literal_factor(sp.Pow(sp.E, f.args[0], evaluate=False))
    if isinstance(f, _TRANSCENDENTAL) and len(f.args) == 1:
        arg = f.args[0]
        if arg.has(sp.I) or not _is_reduced_number(arg, real_only=True):
            return False
        value = f.func(_ev(arg))
        return value.func == f.func  # atan(1) -> pi/4 and sin(pi/6) -> 1/2 are not reduced
    return False


def _term_info(
    term: sp.Basic, *, allow_den: bool = True, defects: list[str] | None = None
) -> tuple[bool, tuple[str, ...], int | None] | None:
    """-> (imaginary?, irrational-part key, integer coefficient) for a reduced term, None if not reduced.
    defects: lenient about roots that can still be simplified (see _literal_factor); each one is appended."""
    num: list[sp.Basic] = []
    den: list[sp.Basic] = []
    _collect(term, num, den)
    i_count = 0
    nums: list[sp.Basic] = []
    den_nums: list[sp.Basic] = []
    keys: list[str] = []
    root_exps: list[sp.Basic] = []  # |exponent| of each radical factor: two with the same one combine
    for f, below in [(f, False) for f in num] + [(f, True) for f in den]:
        if f == sp.I and not below:
            i_count += 1
        elif f.is_Number:
            if f == -1 and not below:
                continue
            (den_nums if below else nums).append(_number_sign(f)[1])
        elif not f.has(sp.I) and _literal_factor(f, defects):
            keys.append(("1/" if below else "") + sp.srepr(_ev(f)))
            exp = _ev(f.exp) if isinstance(f, sp.Pow) else None
            if exp is not None and exp.is_Rational and not exp.is_integer:
                root_exps.append(abs(exp))
        else:
            return None
    if i_count > 1 or len(nums) > 1 or len(den_nums) > 1 or (den_nums and not allow_den):
        return None
    if any(isinstance(n, sp.Float) for n in nums + den_nums) and len(nums) + len(den_nums) > 1:
        return None
    if any(not (n.is_Integer or isinstance(n, sp.Float)) for n in nums + den_nums):
        return None  # a Rational atom only appears in exponents; be strict elsewhere
    if den_nums and (den_nums[0] <= 1):
        return None
    if nums and den_nums and nums[0].is_Integer and math.gcd(int(nums[0]), int(den_nums[0])) != 1:
        return None
    # radicals that combine (sqrt(2)*sqrt(3), sqrt(2)/sqrt(3)) or repeated factors (pi*pi) are not reduced
    if len(set(keys)) != len(keys):
        return None
    if len(set(root_exps)) != len(root_exps):
        if defects is None:
            return None
        defects.append(ROOT_COMBINE)
    coefficient = int(nums[0]) if nums and nums[0].is_Integer else (1 if not nums else None)
    return i_count == 1, tuple(sorted(keys)), coefficient


def _is_reduced_number(raw: sp.Basic, *, real_only: bool = False, defects: list[str] | None = None) -> bool:
    """A constant typed in reduced form: at most one term of each kind (rational / each irrational part, real or
    imaginary), integer fractions in lowest terms, no arithmetic left to do. '(p + q i)/n' is also accepted.
    defects: reduced except for roots that can still be simplified, which are appended (see _term_info)."""
    if raw.free_symbols:
        return False
    if isinstance(raw, sp.Mul) and len(raw.args) == 2 and sp.S.NegativeOne in raw.args and all(a.is_Number for a in raw.args):
        # '6*(-1)', '6(-1)', '-1*6': a minus sign typed on a number literal parses as Integer(-6), so a lone
        # Mul(-1, 6) outside a sum is a product left to evaluate (inside a sum, '3 - 6' is a subtraction term)
        return False
    # (p + q i)/n with an integer n: the numerator must be reduced, with no common factor left with n
    num: list[sp.Basic] = []
    den: list[sp.Basic] = []
    _collect(raw, num, den)
    adds = [f for f in num if isinstance(f, sp.Add)]
    if adds and den:
        rest_num = [f for f in num if not isinstance(f, sp.Add) and f != -1]
        if len(adds) != 1 or rest_num or len(den) != 1 or not den[0].is_Integer or abs(int(den[0])) <= 1:
            return False
        infos = [_term_info(t, allow_den=False, defects=defects) for t in _terms(adds[0])]
        if any(i is None for i in infos) or not _distinct_terms(infos, real_only):  # type: ignore[arg-type]
            return False
        coefficients = [i[2] for i in infos if i is not None]
        if any(c is None for c in coefficients):
            return False
        return math.gcd(*[int(c) for c in coefficients if c is not None], abs(int(den[0]))) == 1
    infos = [_term_info(t, defects=defects) for t in _terms(raw)]
    if any(i is None for i in infos):
        return False
    return _distinct_terms(infos, real_only)  # type: ignore[arg-type]


def _distinct_terms(infos: list[tuple[bool, tuple[str, ...], int | None]], real_only: bool) -> bool:
    keys = [(imag, part) for imag, part, _ in infos]
    if real_only and any(imag for imag, _ in keys):
        return False
    return len(set(keys)) == len(keys)


UNCOMPUTED_FEEDBACK = "Not yet computed — write it as (real part) + (imaginary part)·i; the parts may stay unexpanded."
UNCOMPUTED_REAL_FEEDBACK = "Not yet computed — this is a real number: work out the product and write it without i."


def _uncomputed(raw: sp.Basic) -> bool:
    """A typed expression with letters that still holds a complex computation: a product of two factors containing i
    ('(a+bi)(c+di)', 'i(a+bi)'), a sum containing i times a factor with a letter ('c(a+bi)'), a power or quotient of
    something containing i ('(a+bi)^2', 'i^2'), or a sum with two terms containing i — the summands typed back
    unadded ('c+di+g+hi' for β+λ; the S4a evaluation, 2026-10-08). '(ac-bd)+(ad+bc)i' and '-(ad+bc)+(ac-bd)i' are
    computed."""
    for node in sp.preorder_traversal(raw):
        if isinstance(node, sp.Pow) and node.base.has(sp.I):
            return True
        if isinstance(node, sp.Add) and sum(1 for term in node.args if term.has(sp.I)) >= 2:
            return True
        if isinstance(node, sp.Mul):
            with_i = [f for f in node.args if f.has(sp.I)]
            if len(with_i) >= 2:
                return True
            sums = [f for f in with_i if isinstance(f, sp.Add)]
            if sums and any(f.free_symbols for f in node.args if f is not sums[0]):
                return True
    return False


LIST_NOT_COMPUTED = "Same values — now work out each component and enter the resulting list itself."
SYMBOL_NOT_SIMPLIFIED = "Same value — now simplify it."
_SINGLE_SYMBOL = re.compile(r"^-?\s*(?:\d+\s*\*?\s*)?[A-Za-z]\w*$")


def _list_arithmetic_feedback(expected: Node, got: Node) -> str | None:
    """A list asked for, and the learner typed the arithmetic that produces it — the question's own 'i(2,-1)' or
    '(1,2)+3(0,1)' — instead of working out the components (the bypass audit of 2026-10-08: 'compute i(2,-1)' accepted
    'i(2,-1)', because distributing the scalar left reduced-looking components)."""
    if got.arithmetic and expected.kind == "tuple" and not expected.arithmetic:
        return LIST_NOT_COMPUTED
    return None


def _pieces(name: str, keep_greek: frozenset[str]) -> list[str] | None:
    """A name read as the learner may have meant it: one-letter or digit indices, letters split as in a letter run
    ('x_ky' -> x_k, y; 'x_kpi' -> x_k, pi; 'x_1y_k' -> x_1, y_k). None when it has another shape."""
    peel = tuple(sorted(set(_PEEL_FUNCTIONS) | {"pi"} | keep_greek, key=len, reverse=True))
    pieces: list[str] = []
    pos = 0
    for m in _INDEXED_PIECE.finditer(name):
        if m.start() != pos or not m.group(0):
            return None
        parts = ["l"] if m.group(1) == "ell" else _peel(m.group(1), False, keep_greek, peel)
        index = m.group(2) or m.group(3)
        if index and parts[-1] in _KNOWN_NAMES:
            parts.append(index)
        elif index:
            parts[-1] = f"{parts[-1]}_{index}"
        pieces += parts
        pos = m.end()
    return pieces if pos == len(name) else None


def _long_index_feedback(expected: Node, got: Node) -> str | None:
    """'x_ky' typed for x_k·y reads as one name with the index ky ('x_kpi', 'x_1y_k' alike). When that name is not in
    the key but its split reading uses only the key's symbols (and numbers, i, j, e, π, functions), the response is
    asked again with the way to write it — a typing slip, not a wrong answer."""
    names = lambda node: {s.name for sc in node.scalars() if sc.expr is not None for s in sc.expr.free_symbols}  # noqa: E731
    known = names(expected)
    allowed = known | set(_CONSTANTS) | set(_FUNCTIONS)
    for name in sorted(names(got) - known):
        pieces = _pieces(name, _greek_names(expected))
        if pieces and len(pieces) > 1 and all(p in allowed or p.isdigit() for p in pieces):
            shown = lambda n: n.replace("lamda", "lambda")  # noqa: E731 — as the learner typed it
            return (
                f"{shown(name)} reads as one name. An index after _ is one letter or a number — put a space or * after "
                f"it: {' * '.join(map(shown, pieces))}."
            )
    return None


def _symbol_feedback(expected: Node, got: Node, response: str) -> str | None:
    """A key that is one symbol, possibly with a number in front ('v', '-v', '2v' — a vector in an abstract space):
    the answer must be that short too. '-(-v)', 'v+0', '1v+v' are the expression still to simplify (the audit found
    'simplify -(-v)' accepting '-(-v)'; the parser folds the double sign, so the typed text is compared)."""
    if expected.kind != "scalar" or got.kind != "scalar" or expected.raw is None or expected.expr is None:
        return None
    if not expected.expr.free_symbols or len(expected.expr.free_symbols) != 1:
        return None
    key_text = str(expected.raw).replace(" ", "")
    if not _SINGLE_SYMBOL.match(key_text):
        return None
    typed = re.sub(r"\s+", "", response)
    return SYMBOL_NOT_SIMPLIFIED if re.search(r"[()+]|--|-\s*-", typed) else None


def _uncomputed_feedback(expected: Node, got: Node) -> str | None:
    """The standard-form rule for keys with letters (the proof track, 2026-10-08): "enter α(βi) in standard form" only
    measures something when '(a+bi)(-d+ci)' — the product retyped — is not accepted as the computed result. Applies
    when the key has letters and is itself computed; a real key with letters (a coordinate, a slot) takes no i."""
    if expected.kind != got.kind or len(expected.scalars()) != len(got.scalars()) or expected.kind == "set":
        return None
    for e, g in zip(expected.scalars(), got.scalars()):
        if e.expr is None or not e.expr.free_symbols or e.raw is None or g.raw is None or _uncomputed(e.raw):
            continue
        real = {s: sp.Symbol(s.name, real=True) for s in e.expr.free_symbols}
        try:
            imaginary = sp.simplify(sp.im(e.expr.xreplace(real))) != 0
        except Exception:  # noqa: BLE001 — an odd key is left to the comparison
            continue
        if imaginary and _uncomputed(g.raw):
            return UNCOMPUTED_FEEDBACK
        if not imaginary and g.raw.has(sp.I):
            return UNCOMPUTED_REAL_FEEDBACK
    return None


def _unsimplified_feedback(expected: Node, got: Node) -> str | None:
    """D3: same value as a reduced constant key, but not typed in reduced form -> a form-appropriate message."""
    pairs: list[tuple[Node, Node]] = []
    e_scalars, g_scalars = expected.scalars(), got.scalars()
    if expected.kind == "scalar" and got.kind == "scalar":
        pairs = [(expected, got)]
    elif len(e_scalars) == len(g_scalars):
        if expected.kind == "set" or got.kind == "set":
            pairs = [(e, g) for e in e_scalars for g in g_scalars]  # require every component to be reduced
        else:
            pairs = list(zip(e_scalars, g_scalars))
    else:
        return None
    if not all(e.expr is not None and not e.expr.free_symbols and e.raw is not None and _is_reduced_number(e.raw) for e, _ in pairs):
        return None
    if all(g.raw is not None and _is_reduced_number(g.raw) for _, g in pairs):
        return None
    many = len(e_scalars) > 1
    defects: list[str] = []
    if all(g.raw is not None and _is_reduced_number(g.raw, defects=defects) for _, g in pairs) and defects:
        # reduced but for its roots (√(1/2) + √(1/2)i is already a + bi): name what is left to do
        steps = "; ".join(d for d in _ROOT_DEFECTS if d in defects)
        return f"Same values — now simplify the roots in each component: {steps}." if many else f"Same value — now simplify the roots: {steps}."
    complex_key = any((v := _numeric(e.expr)) is not None and abs(v.imag) > 1e-12 for e in e_scalars if e.expr is not None)
    if complex_key:
        return "Same values — now simplify each component to the form a + bi." if many else "Same value — now simplify it to the form a + bi."
    if many:
        return "Same values — now simplify each component fully (combine terms, reduce fractions)."
    return "Same value — now simplify it fully (combine terms, reduce fractions, evaluate powers and roots)."


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


_GENERIC_MISSES = ("Not quite.", "Not equivalent to the expected answer.")


def _tuple_example(n: int) -> str:
    """'(a, b)' for 2 components, '(a, b, c)' for 3 … — the shape of an n-component answer."""
    return "(" + ", ".join("abcd"[:n]) + ")" if 1 <= n <= 4 else f"(x_1, …, x_{n})"


PAIR_EXPECTED = (
    "An ordered pair is expected here, e.g. (a, b) — for a complex number, its real and imaginary coordinates — "
    "not a single value."
)
SINGLE_EXPECTED = "A single value is expected here, not a pair — write a complex number in the form a + bi."


def _pair_form_mismatch(expected: Node, got: Node) -> str | None:
    """A single value for a 2-component key, or a pair for a single-value key: the learner used the other notation
    (typically a complex number as a + bi vs (real, imaginary)). That is a form slip — ask again, never mark it wrong.
    The message depends only on the shapes, never on whether the value is right."""
    expected, got = _flatten_vector(expected), _flatten_vector(got)
    if expected.kind == "tuple" and len(expected.items) == 2 and got.kind == "scalar":
        return PAIR_EXPECTED
    if expected.kind == "scalar" and got.kind == "tuple" and len(got.items) == 2:
        return SINGLE_EXPECTED
    return None


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
        return _Verdict(False, f"Expected {len(expected.items)} components, e.g. {_tuple_example(len(expected.items))}.")
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
        suffix = f" {detail}" if detail and detail not in _GENERIC_MISSES else ""
        return _Verdict(False, f"{label} {wrong[0] + 1} is not right.{suffix}")
    details = {results[k].feedback for k in wrong}
    count = f"{len(wrong)} of {len(results)} {label.lower()}s"
    if details == {_NEAR_EXACT}:
        return _Verdict(False, f"{count} are numerically close, but exact values are expected (fractions, roots or multiples of π).")
    if len(details) == 1 and (detail := next(iter(details))) and detail not in _GENERIC_MISSES:
        return _Verdict(False, f"{count} are not right. {detail}")
    return _Verdict(False, f"{count} are not right.")


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
_SEPARATED_NUMBER = re.compile(r"[+-]?\d+(?:,\d+)+(?:\.\d+)?")
_NUMBER_WITH_UNITS = re.compile(r"[+-]?(?:\d+(?:[.,]\d*)?|\.\d+)\s*[A-Za-z°%][A-Za-z°%/^*.\s\d]*")


# ---------------------------------------------------------------------------
# true_false_set: "mark each statement true or false"
# ---------------------------------------------------------------------------

_TF_TOKEN = re.compile(r"(?:true|false|t|f)+")
_TF_WORD = re.compile(r"true|false|t|f")
# Optional statement numbers: '1.', '2)', '3:', '1 T', '1T' (a number directly followed by a mark, so '1 0 1' is not one)
_TF_NUMBER = re.compile(r"(?<![0-9])([0-9]{1,2})\s*(?:[.):=\-]\s*)?(?=[a-z])")
_TF_SEPARATORS = re.compile(r"[\s,;/|.\-()\[\]]+")


def parse_true_false_set(text: str | None) -> str | None:
    """A true/false-set response or key -> one letter per statement ('TFT'); None if it isn't only T/F marks.

    Accepted: 'TFT', 'T F T', 't,f,t', 'T/F/T', 'true false true', 'True, False, True', '1. T 2. F 3. T', '1T 2F 3T'.
    Rejected: digits as marks ('1 0 1'), yes/no, ticks, anything else."""
    if text is None:
        return None
    s = unicodedata.normalize("NFKC", text).casefold().strip()
    for src, dst in _UNICODE_OPS.items():
        s = s.replace(src, dst)
    if not s or len(s) > MAX_INPUT_CHARS:
        return None
    numbers = [int(m.group(1)) for m in _TF_NUMBER.finditer(s)]
    if numbers:
        if numbers != list(range(1, len(numbers) + 1)):
            return None
        s = _TF_NUMBER.sub(" ", s)
    letters: list[str] = []
    for token in _TF_SEPARATORS.split(s):
        if not token:
            continue
        if not _TF_TOKEN.fullmatch(token):
            return None
        letters.extend(word[0].upper() for word in _TF_WORD.findall(token))
    if not letters or (numbers and len(numbers) != len(letters)):
        return None
    return "".join(letters)


def _statements(n: int) -> str:
    return f"{n} statement" if n == 1 else f"{n} statements"


def true_false_set_request(answer: str) -> str:
    """The 'answer every statement' message for a true/false set (no part of the key is revealed)."""
    key = parse_true_false_set(answer)
    return f"Mark every statement true or false ({_statements(len(key))})." if key else "Mark every statement true or false."


def true_false_set_display(answer: str) -> str:
    """'FTT' -> '1 False · 2 True · 3 True' (the key as shown after an answer; no '1.' so Markdown keeps it inline)."""
    key = parse_true_false_set(answer)
    if not key:
        return answer
    return " · ".join(f"{k} {'True' if mark == 'T' else 'False'}" for k, mark in enumerate(key, start=1))


def _check_true_false_set(answer: str, response: str | None) -> GradeResult:
    key = parse_true_false_set(answer)
    if not key:
        log.warning("true_false_set question without a readable answer key: %r", answer)
        return GradeResult(False, "This question has no answer key — please report it as wrong.")
    request = true_false_set_request(answer)
    got = parse_true_false_set(response)
    if got is None:
        return GradeResult(False, f"{request} Use T or F for each statement, in order.", invalid=True)
    if len(got) != len(key):
        return GradeResult(False, f"{request} You marked {len(got)}.", invalid=True)
    wrong = [k for k in range(len(key)) if got[k] != key[k]]
    if not wrong:
        return GradeResult(True, "Correct.")
    parts = [f"statement {k + 1} is actually {'true' if key[k] == 'T' else 'false'}" for k in wrong]
    text = "; ".join(parts)
    return GradeResult(False, text[0].upper() + text[1:] + ".")


def check_answer(
    kind: str,
    *,
    answer: str,
    correct_choice: int | None,
    tolerance: float | None,
    response: str | None,
    choice: int | None,
    require_simplified: bool = True,
) -> GradeResult:
    if kind == "true_false_set":
        return _check_true_false_set(answer, response)
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
        return _check_math_budgeted(kind, answer, tolerance, response, require_simplified)
    log.warning("unsupported question kind %r", kind)
    return GradeResult(False, f"Unsupported question kind: {kind}")


def _check_math(kind: str, answer: str, tolerance: float | None, response: str | None, require_simplified: bool = True) -> GradeResult:
    try:
        expected: Node | None = parse_answer(answer)
    except AnswerParseError:
        expected = None
    keep = _greek_names(expected) if expected is not None else None
    try:
        got = parse_answer(response or "", keep)
    except AnswerParseError as exc:
        return GradeResult(False, f"{exc} {_HELP}", invalid=True)
    if expected is None:
        log.warning("answer key does not parse: %r", answer)
        same = _loose_text(answer) == _loose_text(response or "")
        return GradeResult(same, "Correct." if same else "This answer can't be checked automatically — compare with the solution.")
    text = (response or "").strip()
    if expected.kind == "scalar" and got.kind == "tuple" and _SEPARATED_NUMBER.fullmatch(text):
        return GradeResult(
            False, "Write one number, with a dot for decimals and no thousands separators (e.g. 1500 or 1.5).", invalid=True
        )
    abs_tol = tolerance if tolerance is not None else (DEFAULT_NUMERIC_TOL if kind == "numeric" else None)
    if kind == "numeric" and got.kind == "scalar" and got.expr is not None and got.expr.free_symbols:
        if _NUMBER_WITH_UNITS.fullmatch(text):
            return GradeResult(False, "Enter just the number, without units.", invalid=True)
        return GradeResult(False, "A number is expected here.")
    shape = _pair_form_mismatch(expected, got) or _long_index_feedback(expected, got)
    if shape:
        return GradeResult(False, shape, invalid=True)
    verdict = _compare(expected, got, abs_tol, tolerance)
    if not verdict.ok:
        return GradeResult(False, verdict.feedback or "Not quite.")
    if _same_text(answer, response or "", keep):
        return GradeResult(True, "Correct.")
    if require_simplified:
        form = (
            _list_arithmetic_feedback(expected, got)
            or _uncomputed_feedback(expected, got)
            or _symbol_feedback(expected, got, response or "")
            or _unsimplified_feedback(expected, got)
        )
        if form:
            return GradeResult(False, form, invalid=True)
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


def _same_text(answer: str, response: str, keep_greek: frozenset[str] | None = None) -> bool:
    """Same answer up to spacing and explicit `*` (so '-7+22i' reads as identical to '-7+22*i')."""
    try:
        return _loose_text(normalize(answer)) == _loose_text(normalize(response, keep_greek))
    except AnswerParseError:
        return False


def is_checkable(kind: str, answer: str, correct_choice: int | None, choices: list[str] | None = None) -> bool:
    """True if a question's answer key can be graded automatically. For a true/false set, pass its statements as
    `choices` to also require one mark per statement."""
    if kind == "true_false_set":
        key = parse_true_false_set(answer)
        return bool(key) and (choices is None or len(key) == len(choices))
    if kind in ("multiple_choice", "true_false"):
        return correct_choice is not None
    if kind in ("numeric", "expression"):
        try:
            parse_answer(answer)
        except AnswerParseError:
            return False
        return True
    return kind == "free_response"


# ---------------------------------------------------------------------------
# Time budget: math grading runs in worker processes that are killed when they overrun
# ---------------------------------------------------------------------------

_BACKEND_DIR = str(Path(__file__).resolve().parents[2])
_WORKER_BOOT = "import sys; sys.path.insert(0, sys.argv[1]); from app.learning.grading import _worker_main; _worker_main()"
_WORKER_START_TIMEOUT = 60.0
MAX_WORKERS = 3
_in_worker = False


class _WorkerTimeout(Exception):
    pass


class _WorkerUnavailable(Exception):
    pass


class _Worker:
    """One grading process speaking JSON lines over stdin/stdout."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _WORKER_BOOT, _BACKEND_DIR],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            cwd=_BACKEND_DIR,
            close_fds=True,
        )
        self._buf = b""
        try:
            ready = self._read_line(_WORKER_START_TIMEOUT)
        except Exception:
            self.kill()
            raise
        if json.loads(ready).get("ready") is not True:
            self.kill()
            raise _WorkerUnavailable("grading worker did not start")

    def alive(self) -> bool:
        return self.proc.poll() is None

    def _read_line(self, timeout: float) -> bytes:
        assert self.proc.stdout is not None
        fd = self.proc.stdout.fileno()
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _WorkerTimeout()
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                raise _WorkerTimeout()
            chunk = os.read(fd, 65536)
            if not chunk:
                raise _WorkerUnavailable("grading worker exited")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line

    def call(self, job: dict, timeout: float) -> dict:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(job).encode() + b"\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise _WorkerUnavailable("grading worker is gone") from exc
        return json.loads(self._read_line(timeout))

    def kill(self) -> None:
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:  # already gone
            pass


class _WorkerPool:
    def __init__(self, size: int) -> None:
        self.size = size
        self._idle: list[_Worker] = []
        self._all: set[_Worker] = set()
        self._count = 0
        self._cond = threading.Condition()

    def run(self, job: dict, timeout: float) -> dict:
        worker = self._acquire()
        try:
            result = worker.call(job, timeout)
        except BaseException:
            self._release(worker, broken=True)
            raise
        self._release(worker, broken=False)
        return result

    def _acquire(self) -> _Worker:
        with self._cond:
            while True:
                while self._idle:
                    worker = self._idle.pop()
                    if worker.alive():
                        return worker
                    self._forget(worker)
                if self._count < self.size:
                    self._count += 1
                    break
                self._cond.wait()
        try:
            worker = _Worker()
        except BaseException as exc:
            with self._cond:
                self._count -= 1
                self._cond.notify()
            raise _WorkerUnavailable(str(exc)) from exc
        with self._cond:
            self._all.add(worker)
        return worker

    def _forget(self, worker: _Worker) -> None:
        self._all.discard(worker)
        self._count -= 1

    def _release(self, worker: _Worker, *, broken: bool) -> None:
        if broken or not worker.alive():
            worker.kill()
            with self._cond:
                self._forget(worker)
                self._cond.notify()
            return
        with self._cond:
            self._idle.append(worker)
            self._cond.notify()

    def shutdown(self) -> None:
        with self._cond:
            workers = list(self._all)
            self._all.clear()
            self._idle.clear()
            self._count = 0
        for w in workers:
            w.kill()


_pool = _WorkerPool(MAX_WORKERS)
atexit.register(_pool.shutdown)
_fallback_warned = False


def _use_worker() -> bool:
    return not _in_worker and os.name == "posix" and os.environ.get("UA_GRADING_WORKER", "1") != "0"


_CANT_CHECK = "Couldn't check that answer — try writing it differently."


def _check_math_safe(kind: str, answer: str, tolerance: float | None, response: str | None, require_simplified: bool) -> GradeResult:
    try:
        return _check_math(kind, answer, tolerance, response, require_simplified)
    except Exception:  # a grading bug must not become a server error or a counted miss
        log.exception("grading failed (answer key %r, response %r)", answer, (response or "")[:200])
        return GradeResult(False, _CANT_CHECK, invalid=True)


def _check_math_budgeted(kind: str, answer: str, tolerance: float | None, response: str | None, require_simplified: bool) -> GradeResult:
    global _fallback_warned
    if not _use_worker():
        return _check_math_safe(kind, answer, tolerance, response, require_simplified)
    job = {"kind": kind, "answer": answer, "tolerance": tolerance, "response": response, "require_simplified": require_simplified}
    try:
        out = _pool.run(job, GRADE_TIMEOUT_SECONDS)
    except _WorkerTimeout:
        log.warning("grading timed out after %.1fs (answer key %r, response %r)", GRADE_TIMEOUT_SECONDS, answer, (response or "")[:200])
        return GradeResult(False, TOO_COMPLEX, invalid=True)
    except _WorkerUnavailable as exc:
        if not _fallback_warned:
            log.warning("grading worker unavailable (%s); grading in-process without a time budget", exc)
            _fallback_warned = True
        return _check_math_safe(kind, answer, tolerance, response, require_simplified)
    if "error" in out:
        log.error("grading failed in the worker: %s (answer key %r, response %r)", out["error"], answer, (response or "")[:200])
        return GradeResult(False, _CANT_CHECK, invalid=True)
    return GradeResult(bool(out["correct"]), str(out["feedback"]), bool(out["invalid"]))


def warm_up() -> None:
    """Start one grading worker in the background (optional; the first grade otherwise pays ~1 s of start-up)."""
    if not _use_worker():
        return

    def start() -> None:
        try:
            _pool._release(_pool._acquire(), broken=False)
        except Exception:
            log.debug("grading worker warm-up failed", exc_info=True)

    threading.Thread(target=start, name="grading-warmup", daemon=True).start()


def _worker_main() -> None:
    """Entry point of a grading worker process: one JSON job per line in, one JSON result per line out."""
    global _in_worker
    _in_worker = True
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # stray prints must not corrupt the protocol
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s (grading worker): %(message)s")
    out.write(b'{"ready": true}\n')
    out.flush()
    for line in sys.stdin.buffer:
        try:
            job = json.loads(line)
            r = _check_math(job["kind"], job["answer"], job["tolerance"], job["response"], job["require_simplified"])
            msg: dict = {"correct": r.correct, "feedback": r.feedback, "invalid": r.invalid}
        except Exception as exc:  # report, keep serving
            msg = {"error": f"{type(exc).__name__}: {exc}"}
        out.write(json.dumps(msg).encode() + b"\n")
        out.flush()
