# Universal Academy

Turn a chapter of a textbook PDF into a Math-Academy-style course: a knowledge graph of micro-skills, lessons that keep the book's context, checked practice, and spaced repetition that tells you what to do next.

- Product: [docs/PRD.md](docs/PRD.md) · Technical spec: [docs/SPEC.md](docs/SPEC.md) · Research notes: [docs/research/](docs/research/)

## Run

```bash
./run.sh
```

Then open http://localhost:8000. Keys go in `.env` (project root): `OPENAI_API_KEY` (required) and `NVIDIA_API_KEY` (optional, enables Nemotron Parse for layout/figure detection). To use it from an iPad or phone on the same network: `UA_HOST=0.0.0.0 ./run.sh` and open `http://<your-mac's-LAN-IP>:8000`.

## Use

1. **Library** → drop a PDF (or paste a local path).
2. **Pick a chapter** in the outline (click a chapter or shift-click several sections), check the page thumbnails, press **Build course**.
3. Watch the build: pages are parsed (layout, LaTeX, figures), the knowledge graph appears, then lessons, visuals and verified questions fill in.
4. Press **Continue learning**. The app picks the next task (new lesson or due review) and explains why. Pass each knowledge point with two independent correct answers in a row; reviews come back on an FSRS schedule, and practising an advanced skill also refreshes its prerequisites.

## Develop

```bash
cd backend && .venv/bin/python -m pytest -q                 # offline tests (fake LLM)
cd backend && .venv/bin/uvicorn app.main:app --reload       # API on :8000
cd frontend && npm run dev                                  # UI on :5173 (proxies /api)
UA_FAKE_LLM=1 ./run.sh                                      # whole app offline with canned AI output
```

Model roles and other knobs are documented in [docs/SPEC.md §8](docs/SPEC.md).
