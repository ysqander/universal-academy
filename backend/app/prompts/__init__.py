"""Prompt sets per LLM provider (option A, 2026-10-08).

- app/prompts/rules.py: the learner's rules, shared by every provider (one place per rule).
- app/prompts/openai/, app/prompts/anthropic/: each provider's instruction texts — assembly, wording, emphasis and
  the corrections tuned against that model. Tuning one never changes the other.

Call sites pick the set at call time with for_role(role): the set of the provider that runs that role (a mixed setup —
Opus writing, GPT reviewing — gives each call its own provider's texts). scripts/prompt_diff.py shows where the two
sets differ.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

from ..config import PROVIDERS, settings


def for_provider(provider: str) -> ModuleType:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown LLM provider {provider!r} (one of {', '.join(PROVIDERS)})")
    return import_module(f"{__name__}.{provider}")


def for_role(role: str) -> ModuleType:
    """The prompt set of the provider that runs `role` (vision, graph, lesson, viz, verify)."""
    return for_provider(settings.provider(role))
