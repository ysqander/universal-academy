# Universal Academy — frontend

React 19 + Vite 8 + TypeScript 6 single-page app. It talks only to the FastAPI backend under `/api`
(contract: `src/types.ts`, mirror of `backend/app/schemas.py`; endpoints: `docs/SPEC.md` §5).

```bash
npm install
npm run dev      # http://localhost:5173, proxies /api → http://127.0.0.1:8000
npm run build    # tsc (strict) + vite build → dist/, which FastAPI serves in production
npm run lint     # oxlint
npm test         # node --test tests/*.test.ts (Markdown/TeX normaliser, map viewport; Node >= 23)
```

`UA_API_TARGET=http://127.0.0.1:9000 npm run dev` points the dev proxy at another backend.

## Layout

| Path | What |
|---|---|
| `src/api.ts` | One typed function per endpoint; `ApiError` with readable messages (JSON `detail`, validation errors, backend unreachable). Book upload uses XHR for progress. |
| `src/hooks/useResource.ts` | Keyed data loading with optional polling (paused while the tab is hidden), abort on unmount, `reload` / `mutate`. |
| `src/components/` | Shared UI: `Markdown` (GFM + KaTeX, `\( \)`/`\[ \]` normalisation, `[[figure:id]]` placeholders), `VisualizationView` (+ `viz/`), `Dialog` (modal / drawer / bottom sheet on `<dialog>`), `SourceViewer`, `TaskMenu`, `StatusChip`, `ProgressBar`, `Toaster`, lesson components. |
| `src/pages/library` | `/` — courses, books, PDF drop zone / file picker / import from path. |
| `src/pages/picker` | `/books/:bookId` — outline tree (click / shift-click), thumbnails, page range, build. |
| `src/pages/course` | `/courses/:courseId` — header, build progress, task menu, knowledge map (React Flow + dagre) and list, skill drawer, lesson preview. |
| `src/pages/study` | `/courses/:courseId/study` — lesson intro, practice (knowledge points, question card, hints, feedback), reviews, completion. |
| `src/lib/` | Pure helpers: formatting, outline/page labels, graph layout, ascii-math → LaTeX preview, Markdown/TeX clean-up. |

## Conventions

- Plain CSS: tokens and shared primitives in `src/index.css`, one stylesheet per component. Dark mode follows `prefers-color-scheme`.
- Reading text is serif (Source Serif 4), UI is sans (Instrument Sans); KaTeX is pinned to the 0.16 line that `rehype-katex` renders with, so its CSS matches.
- Keyboard in practice: `1`–`5` choose, `H` next hint, `Enter` check / continue, `⌘/Ctrl+Enter` for free responses.
- Free-response flow: after the learner commits a written answer the UI calls `POST /api/questions/{qid}/reveal` to show the model solution and checklist, then submits the self-grade with `revealed_solution: false` (only an explicit "Show solution" before answering sends `true`).
- The server owns per-attempt state: a question card starts from `QuestionPublic.hints_used` / `revealed` / `committed_response` (a reload reopens hints, a shown solution, or the committed answer in self-grading mode), and "Give me an easier step" is shown only when `TaskProgress.easier_available`.
- `GET /api/health` is fetched once per page load (`useHealth`); the picker's page limit comes from `Health.max_course_pages`.
