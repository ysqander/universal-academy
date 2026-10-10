"""Model calls answered by Claude Code agents instead of an API (UA_AGENT_DIR).

With UA_AGENT_DIR set, every llm.structured call is written as a request file and the pipeline waits for an agent to
answer it — the same instructions, context, images and output schema the API would get, and the answer is validated
against the same schema. Nothing else in the pipeline changes (planning, writing, reviews, revisions, repairs,
verification), so a lesson made this way goes through every check an API-made one does.

    <dir>/requests/<id>.json    role, label, effort, instructions, shared_prefix, text, images, schema, schema_ref
    <dir>/requests/<id>/        the same as readable files: instructions.md (the system prompt), context.md (shared
                                context, when there is one), message.md (the user message), schema.json
    <dir>/images/<id>-<n>.png   attached images
    <dir>/responses/<id>.json   the answer (published by `validate`, never written directly by the pipeline)
    <dir>/claims/<id>           an agent is answering it
    <dir>/errors/<id>.txt       the last answer was rejected (the request is offered again)
    <dir>/DONE                  the runs are over (written by whoever started them)

Command line (for the agents and the broker that dispatches them):

    python -m app.agent_llm claim <dir> [--wait SECONDS]   wait for unanswered requests, claim them, print JSON
    python -m app.agent_llm validate <dir> <id>            check responses/<id>.json.tmp against the schema, publish it
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger("ua.llm")
T = TypeVar("T", bound=BaseModel)

POLL_SECONDS = 2.0
STALE_CLAIM_SECONDS = 45 * 60  # a claim this old without an answer is offered again (the agent died)
MAX_REJECTIONS = 3


def _paths(root: Path) -> dict[str, Path]:
    paths = {name: root / name for name in ("requests", "responses", "claims", "errors", "images")}
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def _write_atomic(path: Path, data: str) -> None:
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:6]}.part")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


async def structured(
    *,
    root: Path,
    role: str,
    effort: str,
    instructions: str,
    text: str,
    schema: type[T],
    images: list[bytes] | None,
    shared_prefix: str | None,
    label: str,
    timeout: float,
) -> T:
    """Write the request, wait for its answer, return it parsed. A rejected answer is offered again (MAX_REJECTIONS)."""
    from .llm import LLMError

    paths = _paths(root)
    rid = f"{time.strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}"
    image_paths = []
    for n, png in enumerate(images or []):
        p = paths["images"] / f"{rid}-{n}.png"
        p.write_bytes(png)
        image_paths.append(str(p))
    request = {
        "id": rid,
        "role": role,
        "label": label,
        "effort": effort,
        "instructions": instructions,
        "shared_prefix": shared_prefix or "",
        "text": text,
        "images": image_paths,
        "schema_ref": f"{schema.__module__}:{schema.__qualname__}",
        "schema": schema.model_json_schema(),
    }
    readable = paths["requests"] / rid
    readable.mkdir(exist_ok=True)
    (readable / "instructions.md").write_text(instructions, encoding="utf-8")
    if shared_prefix:
        (readable / "context.md").write_text(shared_prefix, encoding="utf-8")
    (readable / "message.md").write_text(text, encoding="utf-8")
    (readable / "schema.json").write_text(json.dumps(request["schema"], ensure_ascii=False, indent=2), encoding="utf-8")
    _write_atomic(paths["requests"] / f"{rid}.json", json.dumps(request, ensure_ascii=False, indent=1))
    log.info("llm agent request %s %s %s", rid, role, label)
    started = time.monotonic()
    response = paths["responses"] / f"{rid}.json"
    rejections = 0
    while True:
        if response.exists():
            try:
                parsed = schema.model_validate_json(response.read_text(encoding="utf-8"))
            except ValidationError as e:  # published without `validate`: reject, offer it again
                rejections += 1
                response.unlink(missing_ok=True)
                (paths["claims"] / rid).unlink(missing_ok=True)
                (paths["errors"] / f"{rid}.txt").write_text(str(e), encoding="utf-8")
                if rejections >= MAX_REJECTIONS:
                    raise LLMError(f"{role} call {label}: {rejections} answers did not fit {schema.__name__}") from e
                continue
            log.info("llm agent %s %s %s %.0fs $0", rid, role, label, time.monotonic() - started)
            return parsed
        if time.monotonic() - started > timeout:
            raise LLMError(f"{role} call {label}: no agent answer within {timeout / 60:.0f} minutes (request {rid})")
        await asyncio.sleep(POLL_SECONDS)


# ---------------------------------------------------------------------------
# Command line: the broker claims requests, an agent validates and publishes its answer
# ---------------------------------------------------------------------------


def _unanswered(paths: dict[str, Path]) -> list[dict]:
    out = []
    for req in sorted(paths["requests"].glob("*.json")):
        rid = req.stem
        if (paths["responses"] / f"{rid}.json").exists():
            continue
        claim = paths["claims"] / rid
        error = paths["errors"] / f"{rid}.txt"
        if claim.exists():
            stale = time.time() - claim.stat().st_mtime > STALE_CLAIM_SECONDS
            rejected = error.exists() and error.stat().st_mtime > claim.stat().st_mtime
            if not (stale or rejected):
                continue
        data = json.loads(req.read_text(encoding="utf-8"))
        out.append({"id": rid, "role": data["role"], "label": data["label"], "effort": data["effort"],
                    "chars": len(data["instructions"]) + len(data["shared_prefix"]) + len(data["text"])})
    return out


def claim(root: Path, wait: float) -> dict:
    paths = _paths(root)
    deadline = time.monotonic() + wait
    while True:
        pending = _unanswered(paths)
        if pending:
            for p in pending:
                (paths["claims"] / p["id"]).write_text(time.strftime("%H:%M:%S"), encoding="utf-8")
            return {"done": False, "requests": pending}
        if (root / "DONE").exists():
            return {"done": True, "requests": []}
        if time.monotonic() > deadline:
            return {"done": False, "requests": []}
        time.sleep(POLL_SECONDS)


def validate(root: Path, rid: str) -> tuple[bool, str]:
    paths = _paths(root)
    request = json.loads((paths["requests"] / f"{rid}.json").read_text(encoding="utf-8"))
    module, qualname = request["schema_ref"].split(":")
    schema = getattr(importlib.import_module(module), qualname)
    tmp = paths["responses"] / f"{rid}.json.tmp"
    if not tmp.exists():
        return False, f"write your answer to {tmp} first"
    raw = tmp.read_text(encoding="utf-8")
    try:
        schema.model_validate_json(raw)
    except ValidationError as e:
        return False, f"does not fit {qualname}:\n{e}"
    os.replace(tmp, paths["responses"] / f"{rid}.json")
    (paths["errors"] / f"{rid}.txt").unlink(missing_ok=True)
    return True, f"OK {rid} published"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("claim")
    c.add_argument("dir", type=Path)
    c.add_argument("--wait", type=float, default=300)
    v = sub.add_parser("validate")
    v.add_argument("dir", type=Path)
    v.add_argument("id")
    args = p.parse_args(argv)
    if args.cmd == "claim":
        print(json.dumps(claim(args.dir, args.wait)))
        return 0
    ok, message = validate(args.dir, args.id)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
