---
name: build-course
description: Build a Universal Academy course with no OpenAI or Anthropic key — Claude Code agents answer every model call of the build the app runs (the UA_AGENT_DIR route). Use when the user runs /build-course or asks to build, generate or finish a course through Claude Code.
---

# Build a course through Claude Code

The app, started with `UA_AGENT_DIR`, writes each model call of a build as a request file and waits; a broker claims
the requests and one agent answers each (backend/app/agent_llm.py). This skill runs that broker.

1. **Find the repo root**: `git rev-parse --show-toplevel` (the folder with `run.sh`). Call it ROOT below.

2. **Check the app**: `curl -s http://127.0.0.1:8000/api/health` (another port if the user set `UA_PORT`). It must
   answer with `"agent_route": true` and an empty `"missing_keys"`. If `missing_keys` lists `NVIDIA_API_KEY`, stop and
   tell the user to add it to `.env` (a free key from https://build.nvidia.com; the page-layout model runs in every
   build) and restart the app — never read or edit `.env` yourself.
   If it does not answer, or `agent_route` is false, stop and tell the user:
   - stop the app if it runs (Ctrl-C in its terminal), then start it from ROOT with `UA_AGENT_DIR=.agent-broker ./run.sh`;
   - in the app (http://localhost:8000): add the PDF in the Library, pick a chapter, press **Build course**;
   - then run /build-course again (the broker waits about 30 minutes for the first request, so either order works).

3. **Tell the user**, in two lines: the build runs on their Claude Code plan's usage, not an OpenAI or Anthropic key; a chapter takes a
   while, often hours (one agent per model call: each page, the knowledge graph, every stage of every lesson, the questions and
   their checks). Progress shows in the app's build view. Keep the app and this session open.

4. **Run the broker** with the Workflow tool:
   `scriptPath: ROOT/tools/claude-code/broker.workflow.js`, `args: {"dir": "ROOT/.agent-broker", "backend": "ROOT/backend"}`
   (absolute paths; if the app was started with another `UA_AGENT_DIR`, pass that folder as `dir`, made absolute
   against ROOT). Optional `parallel` (answer agents at once, default 5): use 2–3 on a machine with 4–6 CPU cores.

   **No Workflow tool?** Do the same loop yourself:
   - run `cd "ROOT/backend" && .venv/bin/python -m app.agent_llm claim "ROOT/.agent-broker" --wait 420 --max N --held ID…`
     (Bash timeout 600000; N = free agent slots, at most 5; `--held` lists the ids your agents are still answering —
     keep the flag even when it lists none);
   - for each request, start one background Agent with the prompt `answerPrompt` builds in broker.workflow.js (fill
     in DIR, BACKEND and the request); use model haiku for role `verify`;
   - repeat. Stop when the output says `"done": true`, or when nothing was offered, none of your agents runs and
     `waiting` was empty three claims in a row (or four in a row before any request came: tell the user to start a
     build in the app).

5. **Report** what the broker returned: calls answered, any it gave up on, and why it stopped. If the build still runs
   in the app (the broker stopped early, or the app shows a failed or interrupted build), run /build-course again —
   it takes over every unanswered call — and press **Resume build** in the app if the build stopped.

Run one /build-course at a time. Never read `.env`, never open the app's data folder, never start or stop the app
yourself.
