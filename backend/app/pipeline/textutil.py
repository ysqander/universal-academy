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
