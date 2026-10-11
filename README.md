# Universal Academy

Turn a chapter of a textbook PDF into a Math-Academy-style course that you study in your browser. The app reads the
chapter's pages (text, LaTeX, figures), breaks the material into small skills, writes a lesson and checked practice for
each one, and then decides what you should do next based on how you actually answer. It runs on your own machine. You
bring the book, and either an API key or Claude Code.

![The knowledge map of a course in progress: learned skills in green, lessons in progress in amber, reviews due outlined in orange](https://raw.githubusercontent.com/ysqander/universal-academy/main/docs/screenshots/skill-map.png)

*The knowledge map of chapter 1 of Axler's* Linear Algebra Done Right *(the bundled example course), part-way through:
learned skills in green, lessons in progress in amber, reviews that are due outlined in orange.*

**Course page:** progress, reviews due, the next quiz, and the tasks **Continue learning** picks next.

![The course page: progress, reviews due, the next quiz, and what to do next](https://raw.githubusercontent.com/ysqander/universal-academy/main/docs/screenshots/course.png)

**Lesson:** teaching in small steps, with quick checks you answer as math (the preview renders what you type).

![A lesson: worked teaching with a quick check typed as math](https://raw.githubusercontent.com/ysqander/universal-academy/main/docs/screenshots/lesson.png)

**Review:** spaced reviews bring each learned skill back with new questions, scheduled by FSRS.

![A spaced review question answered correctly](https://raw.githubusercontent.com/ysqander/universal-academy/main/docs/screenshots/review.png)

**Quiz:** timed and without help, mixed questions on what you learned lately.

![A timed quiz question](https://raw.githubusercontent.com/ysqander/universal-academy/main/docs/screenshots/quiz.png)

**The learning loop**

1. **Knowledge graph.** The chapter becomes a graph of micro-skills (definitions, results, techniques, proofs), each
   linked to the skills it builds on.
2. **Lessons.** Each skill has 2–3 knowledge points: a worked example, then practice. Proof skills guide you through
   writing the proof and checking it against a model answer.
3. **Checked practice.** Questions are verified independently before you see them. Hints, revealed solutions and
   retyped expressions never count as independent answers. A lesson passes at 80 % (half points with a hint). A
   knowledge point you can't pass pauses the lesson for a few hours, and failing twice at the same spot sends you to
   review its prerequisites first.
4. **Spaced reviews and quizzes.** Reviews come back on an FSRS schedule, and practising an advanced skill also
   refreshes the skills under it. Timed quizzes check what the reviews consolidated. **Continue learning** picks the
   next task and tells you why.

## Quickstart

You need macOS or Linux (on Windows, use [WSL](https://learn.microsoft.com/windows/wsl/install), which is the supported
route), and:

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20.19+ (22.18+ to run the frontend tests)
- optionally the `sqlite3` command-line tool for the daily database snapshot (preinstalled on macOS; without it,
  `run.sh` makes the snapshot with Python)

```bash
git clone https://github.com/ysqander/universal-academy.git
cd universal-academy
./run.sh
```

The first run installs the backend and frontend dependencies and builds the frontend. Then open
<http://localhost:8000>.

## Three ways to get a course

### 1. Try the example course (no key needed)

In the **Library**, press **Add to my library** on the **Example course** card. It is chapter 1, *Vector Spaces*, of
Sheldon Axler's *Linear Algebra Done Right* (see [Credits](#license-and-credits)). You can study it without any API
key.

### 2. Build with an API key

1. Put your keys in `.env` at the project root (`./run.sh` creates it from `.env.example` on first start), then
   restart the app:
   - `ANTHROPIC_API_KEY` (with `UA_LLM_PROVIDER=anthropic`) or `OPENAI_API_KEY` (the default provider) — the model
     that reads the pages and writes the graph, lessons and questions;
   - `NVIDIA_API_KEY` — NVIDIA Nemotron Parse finds each page's layout (text blocks, figures, formulas, reading
     order). Every build uses it; a free key is available at [build.nvidia.com](https://build.nvidia.com).
2. In the **Library**, drop a PDF (or paste a path on the machine running the app). A book that is already in your
   library is opened instead of being added twice.
3. Pick a chapter in the outline (shift-click or "Select several" for consecutive sections), check the page
   thumbnails, and press **Build course**. You can start studying as soon as the first lessons are ready.

Cost depends on the model and on the length of the chapter. When a build ends, its **Build log** (on the course page)
shows the estimated cost of that build.

`.env` is also where you choose the provider and models (see [Configuration](#configuration)).

### 3. Build with Claude Code (no OpenAI or Anthropic key)

If you use [Claude Code](https://claude.com/claude-code), its agents can answer the app's model calls instead of an
API. Only `NVIDIA_API_KEY` is needed in `.env` (page layout, see above):

```bash
UA_AGENT_DIR=.agent-broker ./run.sh
```

Start the build in the app as usual, then open Claude Code in this folder and run `/build-course` (either order
works; if your PDF has no bookmarks, run `/build-course` first, because adding such a book already asks a model for its
table of contents). Each model call the build makes is written to `.agent-broker/`, and a Claude Code agent answers it. The answers
go through the same schemas and the same checks as API answers. This uses your Claude Code plan's usage instead of an
API key. It is much slower than the API (a chapter often takes hours), so keep the app and the Claude Code session open
until the build finishes. If the build stops, run `/build-course` again and press **Resume build** in the app.

## Configuration

Settings are read from `.env` at the project root (see `.env.example`), and environment variables override them.

- **Provider and models:** `UA_LLM_PROVIDER=openai` (the default) or `anthropic` runs every model role on one provider.
  `UA_PROVIDER_VISION|GRAPH|LESSON|VIZ|VERIFY` overrides a single role, for example so that Claude writes and GPT
  verifies. All models and effort levels are listed in [docs/SPEC.md §8](docs/SPEC.md).
- **Offline mode:** `UA_FAKE_LLM=1 ./run.sh` runs the whole app with canned model output, for UI work.
- **Claude Code route:** `UA_AGENT_DIR` (the folder for the model calls, relative to the project root) and
  `UA_AGENT_TIMEOUT` (seconds one call may wait for an agent's answer, default 7200).
- **Port and host:** `UA_PORT` (default 8000). `UA_HOST=0.0.0.0 ./run.sh` lets a tablet or phone on the same network
  open the app at `http://<your-computer's-LAN-IP>:8000`. Only do this on a network you trust: there is no login (see
  [SECURITY.md](SECURITY.md)).

## Where your data lives

| What | Where |
| --- | --- |
| Database, course files, page renders | the data folder: `~/Library/Application Support/UniversalAcademy` on macOS, `$XDG_DATA_HOME/UniversalAcademy` on Linux (default `~/.local/share/UniversalAcademy`), `%APPDATA%\UniversalAcademy` on Windows. Set `UA_DATA_DIR` to use another folder. |
| Book PDFs | `books/<book_id>/book.pdf` in this folder (`UA_BOOKS_DIR` overrides it; not in git) |
| Daily database snapshot | `backups/` in this folder, written by `run.sh` and kept for 14 days |

To back up your progress, back up the data folder and `books/` together. Remove a book from the Library, not by hand,
because otherwise the database still lists it. More detail is in [books/README.md](books/README.md).

## Privacy

- Everything runs on your computer. The app listens on `127.0.0.1` only, unless you set `UA_HOST`.
- There are no accounts, no telemetry and no analytics.
- Your API keys stay on your machine, in `.env` (git ignores it).
- When a course is built (and when the app writes fresh questions for a skill you are reviewing), the page images and
  text the model needs are sent to the provider you chose (OpenAI or Anthropic) and the page images to NVIDIA (layout), or to Claude Code on the Claude
  Code route. Nothing else leaves your machine.

## Development

```bash
cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider   # offline tests (fake LLM, temporary data dirs)
cd frontend && npm run build && npm run lint && npm test          # strict TS build, lint, unit tests
cd backend && .venv/bin/uvicorn app.main:app --reload             # API on :8000
cd frontend && npm run dev                                        # UI on :5173 (proxies /api)
```

The tests never call a paid API and never touch your real data folder. The tests that need Axler's PDF use
`examples/linear-algebra-done-right/book.pdf` (or `UA_TEST_AXLER_PDF`), and they are skipped when it is missing.

To refresh a course after prompt changes and keep your progress, stop the app first and run:

```bash
cd backend && .venv/bin/python scripts/regenerate.py --course <course_id> --dry-run
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before you change lessons, questions, scoring or prompts.

## Project layout

```
backend/            FastAPI + SQLite (Python 3.12, uv)
  app/api/          HTTP routers: books, courses, study, examples
  app/bundle.py     course bundles: export and import of the example course
  app/ingest/       PDF outline, page renders, figure candidates, page parsing
  app/pipeline/     course build: parse → graph → lessons → visuals → verify
  app/learning/     grading, FSRS + FIRe-lite scheduling, task selection, XP
  app/prompts/      rules.py (shared rules) + openai/ and anthropic/ wording
  app/llm*.py       model calls (OpenAI, Anthropic, fake); agent_llm.py is the Claude Code route
  scripts/          maintenance tools (regenerate, export a course, …)
  tests/            pytest, offline
frontend/           React 19 + Vite + TypeScript
examples/           the bundled example course (CC BY-NC 4.0, see NOTICE.md)
tools/claude-code/  the broker workflow that /build-course runs (.claude/skills/build-course/SKILL.md)
books/              your book PDFs (not in git)
docs/               product, spec, decisions, research
```

## Docs

- [docs/PRD.md](docs/PRD.md): product scope
- [docs/SPEC.md](docs/SPEC.md): behaviour, HTTP API, configuration
- [docs/DECISIONS.md](docs/DECISIONS.md): what the learner asked for and why (read it before changing lessons,
  questions, scoring or verification)
- [docs/lesson-v2.md](docs/lesson-v2.md): how lessons are written, with a reference lesson
- [docs/research/](docs/research/): background research notes

## License and credits

The code is released under the [MIT License](LICENSE).

The example course in [`examples/linear-algebra-done-right/`](examples/linear-algebra-done-right/) is built from Sheldon
Axler, *Linear Algebra Done Right*, 4th edition (Springer, 2024), © 2024 Sheldon Axler, Open Access under
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). Book website: <https://linear.axler.net>. The book
PDF and the course, which is an adaptation generated from the book, are licensed under CC BY-NC 4.0 and are for
noncommercial use only. See [NOTICE.md](NOTICE.md) for the full attribution and the list of changes.

The learning design follows [Math Academy](https://www.mathacademy.com)'s published approach. This project is not
affiliated with Math Academy.
