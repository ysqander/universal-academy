"""Where the OpenAI and Anthropic prompt sets differ (app/prompts/openai/ vs app/prompts/anthropic/).

    python scripts/prompt_diff.py                 # one line per instruction text: same / differs (+ size change)
    python scripts/prompt_diff.py WRITER_INSTRUCTIONS [--context 2]   # the unified diff of one text

The learner's rules (app/prompts/rules.py) are shared, so they never differ; everything listed here is wording tuned
for one provider. Offline: imports the prompt modules only.
"""

from __future__ import annotations

import argparse
import difflib
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.prompts import for_provider  # noqa: E402
from app.prompts import rules  # noqa: E402


def texts(provider: str) -> dict[str, str]:
    module = for_provider(provider)
    shared = {k for k in vars(rules) if k.isupper()}
    return {k: v for k, v in vars(module).items() if k.isupper() and isinstance(v, str) and k not in shared}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("name", nargs="?", help="one text to diff, e.g. WRITER_INSTRUCTIONS")
    p.add_argument("--context", type=int, default=2)
    args = p.parse_args()
    a, b = texts("openai"), texts("anthropic")
    if args.name:
        if args.name not in a:
            print(f"unknown text {args.name!r}; one of: {', '.join(sorted(a))}")
            return 2
        old = textwrap.fill(a[args.name], 120, replace_whitespace=False).splitlines()
        new = textwrap.fill(b[args.name], 120, replace_whitespace=False).splitlines()
        diff = list(difflib.unified_diff(old, new, "openai", "anthropic", n=args.context, lineterm=""))
        print("\n".join(diff) if diff else f"{args.name}: the same in both sets")
        return 0
    width = max(map(len, a))
    for name in sorted(a):
        same = a[name] == b.get(name)
        delta = "" if same else f"  ({len(a[name])} → {len(b.get(name, ''))} chars)"
        print(f"{name:<{width}}  {'same' if same else 'DIFFERS'}{delta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
