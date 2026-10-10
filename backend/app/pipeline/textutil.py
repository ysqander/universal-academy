"""Pure text helpers shared by the build pipeline and the API views (app.views).

This module imports nothing from the app, so app.views can use it without an import cycle
(pipeline.context imports views).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# ---------------------------------------------------------------------------
# LaTeX for display equations
# ---------------------------------------------------------------------------


def bare_latex(text: str) -> str:
    """LaTeX for one display equation without delimiters: '$$x$$', '\\[x\\]' or '$a$ = $b$' → 'x', 'a = b'.
    An unescaped $ is never valid inside display math, so every one is removed."""
    text = re.sub(r"(?<!\\)\$", "", text or "").strip()
    for left, right in (("\\[", "\\]"), ("\\(", "\\)")):
        if text.startswith(left) and text.endswith(right):
            return text[len(left) : -len(right)].strip()
    return text


# ---------------------------------------------------------------------------
# source_refs: short printed item references only ("1.3", "Example 1.4", "Exercise 1A.3")
# ---------------------------------------------------------------------------

_REF_KINDS = (
    "Definition|Theorem|Example|Exercises?|Lemma|Proposition|Corollary|Remark|Notation|Figure|Section|Problem|"
    "Table|Equation|Result"
)
# A number with structure (1.3, 1A, 1A.3, 1.3(b)) — or any number after an item word (Exercise 3).
_STRUCTURED_NUM = r"\d+(?:[A-Z](?:\.\d+[a-z]?)*|(?:\.\d+[a-z]?)+)(?:\([a-z0-9]+\))?"
_ANY_NUM = r"\d+[A-Z]?(?:\.\d+[a-z]?)*(?:\([a-z0-9]+\))?"
_REF_RE = re.compile(rf"(?:(?i:{_REF_KINDS})\s+{_ANY_NUM}|{_STRUCTURED_NUM})")
_LEAD_END = re.compile(r"$|[\s,;:—–\-(]")
MAX_REF_LENGTH = 24


def clean_source_refs(refs: Iterable[Any]) -> list[str]:
    """Keep only short item references; salvage a leading reference from longer text ("1.1 Definition — complex
    numbers" → "1.1"); drop captions and page notes ("Printed p.8: …"). Order kept, duplicates removed."""
    kept: list[str] = []
    for raw in refs:
        text = re.sub(r"\s+", " ", str(raw or "")).strip().strip(".;,")
        m = _REF_RE.match(text)
        if not m or not _LEAD_END.match(text, m.end()):
            continue
        ref = m.group(0)
        if len(ref) <= MAX_REF_LENGTH and ref not in kept:
            kept.append(ref)
    return kept


# ---------------------------------------------------------------------------
# Figures referenced by lesson text
# ---------------------------------------------------------------------------

FIGURE_PLACEHOLDER_RE = re.compile(r"\[\[figure:([^\]\s]+)\]\]")
MIN_CAPTION_MATCH = 8


def lesson_text(lesson: dict[str, Any]) -> str:
    """All learner-facing prose of a stored lesson (lesson_json), for figure-reference detection."""
    parts = [lesson.get(k) or "" for k in ("why_it_matters", "idea", "missing_steps", "connection", "goal_line", "deeper")]
    for block in lesson.get("blocks") or []:  # v2 teaching sequence
        parts += [block.get("heading") or "", block.get("body") or ""]
        check = block.get("check") or {}
        parts += [check.get("prompt") or "", check.get("explanation") or ""]
    for kp in lesson.get("knowledge_points") or []:
        parts.append(kp.get("explanation") or "")
        example = kp.get("worked_example") or {}
        parts.append(example.get("problem") or "")
        parts += [f"{s.get('math', '')} {s.get('why', '')}" for s in example.get("steps") or []]
        parts.append(example.get("takeaway") or "")
    return "\n".join(parts)


def placeholder_ids(text: str) -> list[str]:
    return list(dict.fromkeys(FIGURE_PLACEHOLDER_RE.findall(text or "")))


def normalize_caption(text: str) -> str:
    text = re.sub(r"[*_`\"“”‘’'«»]", "", text or "").lower()
    return re.sub(r"\s+", " ", text).strip().rstrip(".").strip()


def mentions_caption(text: str, caption: str) -> bool:
    """True when the lesson text quotes the figure's caption (markdown, quotes and a final period ignored)."""
    needle = normalize_caption(caption)
    return len(needle) >= MIN_CAPTION_MATCH and needle in normalize_caption(text)


# ---------------------------------------------------------------------------
# Sibling references in a split plan's assumed prerequisites ("Builds on" chips)
# ---------------------------------------------------------------------------

_KEY = r"[A-Z]\d+(?:[a-z]\d*)?"  # skill keys (S2, S2a, S12a1) and foundation keys (A1)
_SEP = r"\s*(?:[:—–]|-(?!\w))?\s*"  # the separator after a leading reference ("a (S2a): …", "S2a — …")
_LEAD_LOCAL_KEY = re.compile(rf"^\s*(?:(?:skill|sibling)\s+)?([a-z])\s*\(\s*({_KEY})\s*\){_SEP}")
_LEAD_KEY_LOCAL = re.compile(rf"^\s*({_KEY})\s*\(\s*(?:local\s+id\s+)?([a-z])\s*\){_SEP}")
_INLINE_LOCAL_KEY = re.compile(rf"\b([a-z])\s*\(\s*({_KEY})\s*\)")
_INLINE_KEY_LOCAL = re.compile(rf"\b({_KEY})\s*\(\s*(?:local\s+id\s+)?[a-z]\s*\)")


def sibling_keys(key: str, keys: Iterable[str]) -> dict[str, str]:
    """Local ids of a split skill's siblings → their keys: 'S2c' in a course with S2a…S2d → {'a': 'S2a', …, 'd': 'S2d'}
    (the planner names siblings 'a', 'b', … and a split gives them the keys S2a, S2b, … in that order; a lettered key
    split again numbers them: S12a1, S12a2 → 'a', 'b'). {} for a skill that is not part of a split."""
    m = re.fullmatch(r"(.*\d)([a-z])", key or "")
    if m:
        base = m.group(1)
        return {k[len(base) :]: k for k in keys if re.fullmatch(re.escape(base) + "[a-z]", k or "")}
    m = re.fullmatch(r"(.*\d[a-z])(\d+)", key or "")
    if m:
        base = m.group(1)
        numbered = [k for k in keys if re.fullmatch(re.escape(base) + r"\d+", k or "")]
        return {"abcdefghijklmnopqrstuvwxyz"[int(k[len(base) :]) - 1]: k for k in numbered if 0 < int(k[len(base) :]) <= 26}
    return {}


def normalize_sibling_refs(items: Iterable[Any], local_keys: dict[str, str] | None = None) -> list[str]:
    """Assumed prerequisites as the learner reads them: the planner's local ids of sibling skills are replaced by the
    final keys — 'a (S2a): multiply …' / 'S2a (a): …' / 'a: …' / '(a) …' / 'skill a: …' → 'S2a: multiply …', and an
    inline 'b (S2b)' → 'S2b'. local_keys maps local id → final key (preferred over a key in parentheses); without it
    only the forms that carry a key are rewritten. Bare single letters are matched lower-case only, so foundation
    keys ('A1: …') and ordinary text ('$a+bi$') are left alone. Empty entries and duplicates are dropped."""
    local_keys = {k.strip(): v for k, v in (local_keys or {}).items() if k and k.strip()}
    bare = "|".join(re.escape(k) for k in sorted(local_keys, key=len, reverse=True) if re.fullmatch(r"[a-z][a-z0-9]*", k))
    lead_bare = (
        re.compile(rf"^\s*(?:(?:skill|sibling)\s+({bare})\b|\(\s*({bare})\s*\)|({bare})(?=\s*[:)—–]))\s*[:)—–]?\s*")
        if bare
        else None
    )
    out: list[str] = []
    for raw in items:
        text = re.sub(r"\s+", " ", str(raw or "")).strip()
        key, rest = None, text
        if m := _LEAD_LOCAL_KEY.match(text):
            key, rest = local_keys.get(m.group(1), m.group(2)), text[m.end() :]
        elif m := _LEAD_KEY_LOCAL.match(text):
            key, rest = m.group(1), text[m.end() :]
        elif lead_bare is not None and (m := lead_bare.match(text)):
            local = next(g for g in m.groups() if g)
            key, rest = local_keys[local], text[m.end() :]
        rest = _INLINE_LOCAL_KEY.sub(lambda m: local_keys.get(m.group(1), m.group(2)), rest)
        rest = _INLINE_KEY_LOCAL.sub(lambda m: m.group(1), rest).strip()
        text = f"{key}: {rest}" if key and rest else key or rest
        if text and text not in out:
            out.append(text)
    return out
