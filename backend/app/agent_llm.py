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
    <dir>/BROKER                touched while a broker looks for work
    <dir>/DONE                  the runs are over (written by whoever started them)

A relative UA_AGENT_DIR is taken from the project root (UA_AGENT_DIR=.agent-broker ./run.sh: <project>/.agent-broker).
A waiting call touches its request file; a request nobody touched for ORPHAN_SECONDS (the app was stopped) is never
offered. UA_AGENT_TIMEOUT counts from the request's latest claim, or — while it waits for a free agent — from the
broker's latest look for work: a long build is not failed for queueing, an app nobody answers is.

Command line (for the agents and the broker that dispatches them; tools/claude-code/broker.workflow.js is the broker):

    python -m app.agent_llm claim <dir> [--wait SECONDS] [--max N] [--held ID ...] [--skip ID ...]
        wait for unanswered requests, claim them, print JSON {done, requests: [...], waiting: [ids the app waits on]}.
        --held turns on broker mode: the ids this broker is answering (their claims are renewed); every other
        unanswered request is offered, whatever older claims say (a restarted broker takes over at once). --skip: ids
        the broker gave up on, never offered. --max: claim at most N.
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
ALIVE_TOUCH_SECONDS = 30  # a waiting call touches its request file (and a broker its claims) this often
ORPHAN_SECONDS = 5 * 60  # a request untouched this long has no app waiting for it any more


def _touch(path: Path) -> None:
    try:
        os.utime(path)
    except OSError:
        pass


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


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
    from .config import PROJECT_ROOT
    from .llm import LLMError

    root = root if root.is_absolute() else PROJECT_ROOT / root
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
    request_file = paths["requests"] / f"{rid}.json"
    _write_atomic(request_file, json.dumps(request, ensure_ascii=False, indent=1))
    log.info("llm agent request %s %s %s", rid, role, label)
    started = time.monotonic()
    written = touched = time.time()
    response = paths["responses"] / f"{rid}.json"
    rejections = 0
    while True:
        if time.time() - touched > ALIVE_TOUCH_SECONDS:  # still waiting: not an orphan
            _touch(request_file)
            touched = time.time()
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
        # The wait counts from the latest claim (an agent took it, or the broker renewed it); unclaimed, from the
        # broker's latest look for work (the request is queued behind other calls), else from when it was written.
        held_since = _mtime(paths["claims"] / rid) or _mtime(root / "BROKER") or written
        if time.time() - max(written, held_since) > timeout:
            raise LLMError(
                f"{role} call {label}: no agent answer within {timeout / 60:.0f} minutes (request {rid}) — "
                "is Claude Code running /build-course?"
            )
        await asyncio.sleep(POLL_SECONDS)


# ---------------------------------------------------------------------------
# Command line: the broker claims requests, an agent validates and publishes its answer
# ---------------------------------------------------------------------------


def _unanswered(
    paths: dict[str, Path], held: set[str] | None = None, skip: set[str] | frozenset[str] = frozenset()
) -> tuple[list[dict], list[str]]:
    """(requests to offer, ids of every request the app still waits on). Without `held`, a claim keeps its request
    until the claim goes stale or the answer is rejected; with it (broker mode), every request the broker neither
    holds nor skips is offered, whatever older claims say."""
    offer, waiting = [], []
    now = time.time()
    for req in sorted(paths["requests"].glob("*.json")):
        rid = req.stem
        if (paths["responses"] / f"{rid}.json").exists():
            continue
        touched = _mtime(req)
        if touched is None or now - touched > ORPHAN_SECONDS:  # the app stopped waiting for it (it was stopped)
            continue
        waiting.append(rid)
        if held is not None:
            if rid in held or rid in skip:
                continue
        else:
            claim = paths["claims"] / rid
            error = paths["errors"] / f"{rid}.txt"
            if claim.exists():
                stale = now - claim.stat().st_mtime > STALE_CLAIM_SECONDS
                rejected = error.exists() and error.stat().st_mtime > claim.stat().st_mtime
                if not (stale or rejected):
                    continue
        data = json.loads(req.read_text(encoding="utf-8"))
        offer.append({"id": rid, "role": data["role"], "label": data["label"], "effort": data["effort"],
                      "chars": len(data["instructions"]) + len(data["shared_prefix"]) + len(data["text"])})
    return offer, waiting


def claim(
    root: Path, wait: float, *, limit: int | None = None, held: list[str] | None = None, skip: list[str] = ()
) -> dict:
    """Wait up to `wait` seconds for requests to answer and claim them (at most `limit`). `held`: broker mode, see the
    module docstring. Returns {done, requests, waiting}; done when DONE exists and nothing is left to offer."""
    paths = _paths(root)
    held_ids = None if held is None else set(held)
    deadline = time.monotonic() + wait
    renewed: float | None = None
    while True:
        # A broker is alive: requests queued for a free agent, and those its agents hold, keep waiting.
        if renewed is None or time.monotonic() - renewed > ALIVE_TOUCH_SECONDS:
            (root / "BROKER").touch()
            for rid in held_ids or ():
                _touch(paths["claims"] / rid)
            renewed = time.monotonic()
        offer, waiting = _unanswered(paths, held_ids, set(skip))
        offer = offer[:limit] if limit is not None else offer
        if offer:
            for p in offer:
                (paths["claims"] / p["id"]).write_text(time.strftime("%H:%M:%S"), encoding="utf-8")
            return {"done": False, "requests": offer, "waiting": waiting}
        if (root / "DONE").exists():
            return {"done": True, "requests": [], "waiting": waiting}
        if time.monotonic() > deadline:
            return {"done": False, "requests": [], "waiting": waiting}
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
    c.add_argument("--max", type=int, default=None, dest="limit", help="claim at most this many requests")
    c.add_argument("--held", nargs="*", default=None, help="broker mode: the ids this broker is answering")
    c.add_argument("--skip", nargs="*", default=[], help="ids the broker gave up on: never offered")
    v = sub.add_parser("validate")
    v.add_argument("dir", type=Path)
    v.add_argument("id")
    args = p.parse_args(argv)
    if args.cmd == "claim":
        print(json.dumps(claim(args.dir, args.wait, limit=args.limit, held=args.held, skip=args.skip)))
        return 0
    ok, message = validate(args.dir, args.id)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
