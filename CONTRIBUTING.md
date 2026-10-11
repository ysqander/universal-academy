# Contributing

Thanks for helping. Issues and pull requests are welcome on GitHub.

## Dev setup

Follow the Quickstart in the [README](README.md) (Python 3.12+ with uv, Node.js 20.19+, 22.18+ for the frontend
tests). `./run.sh` installs both sides. For development:

```bash
cd backend && uv sync && .venv/bin/uvicorn app.main:app --reload   # API on :8000
cd frontend && npm install && npm run dev                          # UI on :5173 (proxies /api)
```

When you start a test server, use blank keys or `UA_FAKE_LLM=1` so that nothing calls a paid API by accident. Also use
a scratch `UA_DATA_DIR` and `UA_BOOKS_DIR`, so that your real course and your `books/` folder are never touched.

## Tests

Run these before you open a pull request:

```bash
cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider   # offline: fake LLM, temporary data and books dirs
cd frontend && npm run build && npm run lint && npm test
```

## Rules

- **Read [docs/DECISIONS.md](docs/DECISIONS.md) before you touch lessons, questions, scoring or verification.** It
  records what the learner asked for and why (question formats, capstones, the 80 % pass mark with half points for
  hints, the choice of verifier model, …). A change that goes against a decision needs a new entry there.
- **Prompts:** a rule about how lessons or questions must be written goes into `backend/app/prompts/rules.py`, once.
  Both providers share that file. Wording tuned for one model goes into that provider's set only
  (`backend/app/prompts/openai/` or `backend/app/prompts/anthropic/`). `backend/scripts/prompt_diff.py` shows where
  the two sets differ.
- **Contracts:** `backend/app/schemas.py` (HTTP API) and `frontend/src/types.ts` (its TypeScript mirror) change
  together. LLM outputs are defined in `backend/app/llm_schemas.py`, and the database schema and its migrations are in
  `backend/app/db.py`.
- No exercise should be bypassable: hints, revealed solutions and retyped expressions must never count as independent
  answers.
- Never commit `.env`, API keys, your data folder or book PDFs.

See [CLAUDE.md](CLAUDE.md) for the same rules in the form that Claude Code reads.
