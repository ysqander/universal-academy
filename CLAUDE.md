# Universal Academy

Local web app that turns a textbook chapter (PDF) into a Math-Academy-style course: a knowledge graph of micro-skills,
lessons, checked practice and FSRS spaced repetition. FastAPI + SQLite backend in `backend/`, React + Vite frontend in
`frontend/`, generation via the OpenAI or Anthropic API (switchable per role, `UA_LLM_PROVIDER`; NVIDIA Nemotron for page
layout).

## Read before changing anything

- **[docs/DECISIONS.md](docs/DECISIONS.md) — required reading before touching lessons, questions, scoring or
  verification.** It records what the learner asked for and why (question formats, capstones, 80 % pass with half
  points for hints, verifier model choice, …).
- [docs/SPEC.md](docs/SPEC.md) — behaviour and API; [docs/lesson-v2.md](docs/lesson-v2.md) — lesson-writing design
  and the approved reference lesson; [docs/PRD.md](docs/PRD.md) — product scope.

## Contracts

`backend/app/schemas.py` (HTTP API), `backend/app/llm_schemas.py` (LLM structured outputs), `backend/app/db.py`
(SQLite schema + migrations) and `frontend/src/types.ts` (TypeScript mirror of schemas.py). Keep schemas.py and
types.ts in sync.

Prompts: `backend/app/prompts/rules.py` holds the learner's rules, shared by both providers — a rule the learner gives
goes there, once. `backend/app/prompts/openai/` and `backend/app/prompts/anthropic/` hold each provider's own wording;
tuning for one model goes into that provider's set only (`backend/scripts/prompt_diff.py` shows the differences).

## Commands

```bash
./run.sh                                                    # app on http://localhost:8000
cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider   # offline tests (fake LLM)
cd frontend && npm run build && npm run lint && npm test
```

## Rules

- Never read or print `.env` (API keys).
- Test servers: blank keys or `UA_FAKE_LLM=1`, so nothing calls a paid API by accident.
- The learner's real course and progress live in `~/Library/Application Support/UniversalAcademy` (outside this
  cloud-synced folder; `UA_DATA_DIR` overrides). Don't write to it except through the app or the `backend/scripts/`
  tools (stop the app first; they back up the database). Daily snapshots go to `backups/` here.
