"""Run the real page parser on a few pages and print the result.

Usage (from backend/):
    .venv/bin/python scripts/try_parse.py <pdf> <page_index> [<page_index> ...] [--out DIR] [--candidates-only]

Writes renders, set-of-mark overlays, figure crops and <page>.md / <page>.json into the work dir (default: a new
temp dir) and prints the markdown, figures and LLM cost. Uses the live APIs unless UA_FAKE_LLM=1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm  # noqa: E402
from app.ingest import parse, pdf  # noqa: E402


async def run(pdf_path: Path, pages: list[int], out: Path, candidates_only: bool) -> None:
    meta = pdf.read_book_meta(pdf_path)
    usage = llm.Usage()
    llm.current_usage.set(usage)
    for page_index in pages:
        label = meta.page_labels[page_index]
        if candidates_only:
            scan = pdf.figure_candidates(pdf_path, page_index)
            print(f"\n=== PDF index {page_index} (p. {label}) ===")
            for c in scan.candidates:
                print("  candidate", c.to_dict())
            for bbox, reason in scan.rejected:
                print("  rejected ", reason, [round(v) for v in bbox])
            continue
        result = await parse.parse_page(pdf_path, page_index, label, out)
        (out / f"{page_index}.md").write_text(result.markdown + "\n", encoding="utf-8")
        record = asdict(result)
        record["figures"] = [{**asdict(f), "image_path": str(f.image_path)} for f in result.figures]
        (out / f"{page_index}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"\n=== PDF index {page_index} (p. {label}) — engine {result.engine} ===")
        print(result.markdown)
        print("--- figures:")
        for f in result.figures:
            print(f"  {f.id} bbox={f.bbox} caption={f.caption!r}\n      {f.description}\n      {f.image_path}")
        print("--- items:", [f"{i['ref']} {i['kind']} {i['title']}" for i in result.items])
        print("--- exercise page:", result.is_exercise_page, "| uncertain:", result.uncertain)
    if not candidates_only:
        print(f"\nLLM calls: {usage.calls}, cost ≈ ${usage.cost_usd:.4f}; work dir: {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", type=Path)
    ap.add_argument("pages", type=int, nargs="+", help="0-based PDF page indexes")
    ap.add_argument("--out", type=Path, default=None, help="work dir (default: new temp dir)")
    ap.add_argument("--candidates-only", action="store_true", help="only print figure candidates (no API calls)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    out = args.out or Path(tempfile.mkdtemp(prefix="ua-try-parse-"))
    out.mkdir(parents=True, exist_ok=True)
    asyncio.run(run(args.pdf, args.pages, out, args.candidates_only))


if __name__ == "__main__":
    main()
