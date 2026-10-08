# Universal Academy

Turn a chapter of a textbook PDF into a Math-Academy-style course: a knowledge graph of micro-skills, lessons that keep the book's context, checked practice, and spaced repetition that tells you what to do next.

- Product: [docs/PRD.md](docs/PRD.md) · Technical spec: [docs/SPEC.md](docs/SPEC.md) · Research notes: [docs/research/](docs/research/)

## Run

```bash
./run.sh
```

Then open http://localhost:8000. Keys go in `.env` (project root): `OPENAI_API_KEY` (required) and `NVIDIA_API_KEY` (optional; enables Nemotron Parse for layout/figure detection). To use it from an iPad or phone on the same network: `UA_HOST=0.0.0.0 ./run.sh` and open `http://<your-mac's-LAN-IP>:8000` (importing by path is only allowed from the Mac itself; upload works everywhere).

## Use

1. **Library** → drop a PDF, or paste a local path.
2. **Pick a chapter** in the outline (click a chapter; shift-click or "Select several" for consecutive sections), check the page thumbnails, press **Build course**. Budget about 45 s and $0.10 per page; you can start studying as soon as the first lessons are ready.
3. Watch the build: pages are parsed (layout, LaTeX, figures), the knowledge graph appears, then lessons, visuals and independently verified questions fill in.
4. Press **Continue learning**. The app picks the next task (a new lesson or a due review) and says why. Each lesson has 2–3 knowledge points: a worked example, then practice until you get two right in a row without help. Proof skills ask for a written proof that you check against a model answer and a checklist. Reviews come back on an FSRS schedule, and practising an advanced skill also refreshes the skills it builds on.

How it decides what you know (Math Academy rules, simplified): hints, revealed solutions and retyped question expressions never count as independent answers; a knowledge point you can't pass pauses the lesson for a couple of hours; failing twice at the same spot sends you to review its prerequisites first.

## Develop

```bash
cd backend && .venv/bin/python -m pytest -q                 # 568 offline tests (fake LLM)
cd frontend && npm run build && npm run lint && npm test     # strict TS build, lint, unit tests
cd backend && .venv/bin/uvicorn app.main:app --reload       # API on :8000
cd frontend && npm run dev                                  # UI on :5173 (proxies /api)
UA_FAKE_LLM=1 ./run.sh                                      # whole app offline with canned AI output
```

Refresh a course after prompt changes, keeping your progress (stop the app first):

```bash
cd backend && .venv/bin/python scripts/regenerate.py --course <course_id> --dry-run
```

Model roles run on OpenAI (vision `gpt-6-luna`, graph/lessons/visuals `gpt-6.1-sol`, verification `gpt-6-luna`) or on Anthropic (`UA_LLM_PROVIDER=anthropic`: Haiku 5.5 for vision and verification, Opus 5.5 for the rest), per role if you like; these and other knobs are documented in [docs/SPEC.md §8](docs/SPEC.md).
