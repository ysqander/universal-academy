# Universal Academy

Turn a chapter of a textbook into a course that teaches it to you, step by step.

You give the app a PDF and pick a chapter. It breaks the chapter into small skills, writes a short lesson and practice
questions for each one, and then walks you through them in the right order. It checks every answer you type, and it
brings skills back for review just before you would forget them. Everything runs on your own computer.

![The knowledge map of a course in progress: learned skills in green, lessons in progress in amber, reviews due outlined in orange](https://raw.githubusercontent.com/ysqander/universal-academy/main/docs/screenshots/skill-map.png)

*The skill map of chapter 1 of Axler's* Linear Algebra Done Right *(the example course that comes with the app), part-way
through: learned skills in green, lessons in progress in amber, reviews that are due outlined in orange.*

**Course page:** your progress, the reviews that are due, the next quiz, and what to do next.

![The course page: progress, reviews due, the next quiz, and what to do next](https://raw.githubusercontent.com/ysqander/universal-academy/main/docs/screenshots/course.png)

**Lesson:** one idea at a time, with quick checks you answer by typing math (the preview shows what you typed).

![A lesson: worked teaching with a quick check typed as math](https://raw.githubusercontent.com/ysqander/universal-academy/main/docs/screenshots/lesson.png)

**Review:** skills you have learned come back later with new questions.

![A spaced review question answered correctly](https://raw.githubusercontent.com/ysqander/universal-academy/main/docs/screenshots/review.png)

**Quiz:** timed, no hints, a mix of what you learned recently.

![A timed quiz question](https://raw.githubusercontent.com/ysqander/universal-academy/main/docs/screenshots/quiz.png)

## How it works

1. **A map of skills.** The chapter becomes a map of small skills (a definition, a computation, a proof), each linked
   to the skills it needs first. A skill opens once you know what it builds on.
2. **Short lessons.** Each lesson explains one idea with a worked example, then has you do it yourself.
3. **Honest practice.** Every question is checked before you see it, and your typed answers are graded. You pass a
   lesson at 80 %, and an answer with a hint earns half a point. If you get stuck, the app sends you back to the skills
   underneath first.
4. **Reviews and quizzes.** Learned skills return on a spaced-repetition schedule, and timed quizzes mix everything
   together. **Continue learning** always picks the next task and tells you why.

## Get started

You need macOS or Linux (on Windows, use [WSL](https://learn.microsoft.com/windows/wsl/install)), and:

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20.19+ (22.18+ to run the frontend tests)
- optionally the `sqlite3` command-line tool, for the daily database snapshot (preinstalled on macOS)

```bash
git clone https://github.com/ysqander/universal-academy.git
cd universal-academy
./run.sh
```

The first run creates `.env` from `.env.example` and stops so you can add your keys (see below). Run `./run.sh` again
and open <http://localhost:8000>.

To try the app before building anything, open the **Library** and press **Add to my library** on the example course
(chapter 1 of Sheldon Axler's *Linear Algebra Done Right*). Studying needs no key.

Building a course always needs `NVIDIA_API_KEY` in `.env`: NVIDIA's page-layout model finds the text blocks, figures and
formulas on each page. A key is free at [build.nvidia.com](https://build.nvidia.com).

## Build a course with Claude Code (recommended)

If you use [Claude Code](https://claude.com/claude-code), its agents write the course instead of a paid API. The work
counts against your Claude Code plan, and only `NVIDIA_API_KEY` is needed in `.env`.

1. Start the app in Claude Code mode:

   ```bash
   UA_AGENT_DIR=.agent-broker ./run.sh
   ```

2. In the app, drop a PDF into the **Library**, pick a chapter and press **Build course**.
3. Open Claude Code in this folder and run `/build-course`.

Steps 2 and 3 can happen in either order. (If your PDF has no bookmarks, run `/build-course` first: adding such a book
already asks a model for its table of contents.) Every model call of the build goes to `.agent-broker/`, and a Claude
Code agent answers it; the answers pass the same checks as API answers. A chapter often takes a few hours, so keep the
app and Claude Code open. If the build stops, run `/build-course` again and press **Resume build** in the app.

## Build a course with an API key

1. Put your keys in `.env` and restart the app:
   - `ANTHROPIC_API_KEY`: Claude is the default (Opus 5.5 writes the skill map, lessons and questions; Haiku 5.5 reads
     the pages and double-checks the questions). To use OpenAI instead, set `OPENAI_API_KEY` and
     `UA_LLM_PROVIDER=openai`.
   - `NVIDIA_API_KEY` (see above).
2. In the **Library**, drop a PDF (or paste a path on this computer). A book you already have is opened instead of
   being added twice.
3. Pick a chapter in the outline, check the page thumbnails, and press **Build course**. You can start studying as soon
   as the first lessons are ready.

The cost depends on the model and the length of the chapter. When a build ends, its **Build log** on the course page
shows what it cost.

## Configuration

Settings are read from `.env` at the project root (see `.env.example`), and environment variables override them.

- **Provider and models:** `UA_LLM_PROVIDER=anthropic` (the default) or `openai` runs every model role on one provider.
  `UA_PROVIDER_VISION|GRAPH|LESSON|VIZ|VERIFY` overrides a single role, for example so that Claude writes and GPT
  verifies. All models and effort levels are listed in [docs/SPEC.md §8](docs/SPEC.md).
- **Offline mode:** `UA_FAKE_LLM=1 ./run.sh` runs the whole app with canned model output, for UI work.
- **Claude Code route:** `UA_AGENT_DIR` (the folder for the model calls, relative to the project root) and
  `UA_AGENT_TIMEOUT` (seconds one call may wait for an agent's answer, default 7200).
- **Port:** `UA_PORT` (default 8000).

## Where your data lives

| What | Where |
| --- | --- |
| Database, course files, page renders | the data folder: `~/Library/Application Support/UniversalAcademy` on macOS, `$XDG_DATA_HOME/UniversalAcademy` on Linux (default `~/.local/share/UniversalAcademy`), `%APPDATA%\UniversalAcademy` on Windows. Set `UA_DATA_DIR` to use another folder. |
| Book PDFs | `books/<book_id>/book.pdf` in this folder (`UA_BOOKS_DIR` overrides it; not in git) |
| Daily database snapshot | `backups/` in this folder, written by `run.sh` and kept for 14 days |

To back up your progress, back up the data folder and `books/` together. Remove a book from the Library, not by hand,
because otherwise the database still lists it. More detail is in [books/README.md](books/README.md).

## Privacy

- Everything runs on your computer. The app listens on `127.0.0.1` only.
- There are no accounts, no telemetry and no analytics.
- Your API keys stay on your machine, in `.env` (git ignores it).
- When a course is built (and when the app writes fresh questions for a skill you are reviewing), the page images and
  text the model needs go to Claude Code or to the API you chose (Anthropic or OpenAI), and the page images to NVIDIA
  for the layout. Nothing else leaves your machine.

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
