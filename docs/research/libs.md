# Universal Academy: verified notes on the learning-engine and frontend libraries (checked 2026-10-05)

Versions come from `npm view` and the PyPI JSON API, run on 2026-10-05. Bundle sizes are min+gzip figures from the bundlephobia API. Library behaviour comes from reading the official docs and source. **None of the snippets were executed or compiled**, because no packages were installed. Anything not confirmed from a primary source is marked UNVERIFIED.

## 0. Version pins

| Package | Pin | Released | Notes |
|---|---|---|---|
| `fsrs` (py-fsrs) | **6.3.2** | 2026-08-09 | Python ≥3.10, FSRS-6 (21 params). https://pypi.org/project/fsrs/ |
| `pymupdf` | **1.28.2** | 2026-08-06 | Python ≥3.10, AGPL-3.0 or commercial. https://pymupdf.readthedocs.io/en/latest/changes.html |
| `react` / `react-dom` | 19.3.0 | 2026-09-09 | The create-vite react-ts template pins `^19.3.0` |
| `vite` / `@vitejs/plugin-react` | 8.3.2 / 6.1.2 | 2026-10-01 / 2026-10-05 | Template pins `^8.3.1` / `^6.1.1` |
| `typescript` | **~6.0.2** (template) | — | npm `latest` is 7.0.2 (2026-07-08). Keep the template's pin. https://github.com/vitejs/vite/blob/main/packages/create-vite/template-react-ts/package.json |
| `@xyflow/react` | **12.12.0** | 2026-09-24 | ~60 KB gz |
| `@dagrejs/dagre` | **3.1.1** | 2026-08-08 | ~16 KB gz. ESM, ships types. The old `dagre@0.8.5` is unmaintained. |
| `cytoscape` / `cytoscape-dagre` | 3.34.3 / 4.0.1 | 2026-09-07 / 2026-08-28 | ~137 KB + ~15 KB gz. Both ship their own types. |
| `elkjs` | 0.12.0 | 2026-07-17 | ~433 KB gz, async. Too heavy for 30 nodes. |
| `react-markdown` | **10.1.0** | 2025-03-07 | Peer `react >=18` |
| `remark-math` / `rehype-katex` | **6.0.0 / 7.0.1** | 2023-09-19 / 2024-08-19 | `rehype-katex` depends on `katex ^0.16.0` |
| `remark-gfm` | **4.0.1** | 2025-02-10 | |
| `katex` | **0.16.47 (pin; not `latest` 0.19.0)** | 2026-05-16 | See §3b |
| `mafs` | **0.21.0** | 2024-10-20 | Last commit 2025-03-30. Depends on `katex ^0.16`. |
| `expr-eval-fork` | 3.0.3 | 2026-02-02 | ~8 KB gz. Contains the CVE-2025-12735 fix (≥3.0.0). |
| `function-plot` / `jsxgraph` | 1.25.4 / 1.14.0 | 2026-04-26 / 2026-10-05 | ~59 KB / ~259 KB gz |
| `ts-fsrs` (if FSRS is ever needed client-side) | 5.4.2 | 2026-09-01 | Not reviewed further |

---

## 1. FSRS in Python: `fsrs==6.3.2` (open-spaced-repetition/py-fsrs)

Sources: https://github.com/open-spaced-repetition/py-fsrs, plus `fsrs/scheduler.py`, `card.py` and `review_log.py` on `main`. I read `main`, not the 6.3.2 tag, so treat "identical to 6.3.2" as UNVERIFIED.

**Exports:** `Scheduler`, `Card`, `Rating`, `ReviewLog`, `State`, `Optimizer`.

**`Scheduler`**
- Constructor: `Scheduler(parameters: Sequence[float] = DEFAULT_PARAMETERS, desired_retention: float = 0.9, learning_steps = (timedelta(minutes=1), timedelta(minutes=10)), relearning_steps = (timedelta(minutes=10),), maximum_interval: int = 36500, enable_fuzzing: bool = True)`.
- `parameters` holds 21 weights. The constructor validates the length and the bounds.
- `desired_retention` is **not validated**.

**`review_card(card, rating, review_datetime: datetime | None = None, review_duration: int | None = None) -> tuple[Card, ReviewLog]`**
- `review_duration` is in milliseconds.
- It returns a **shallow copy** and does not mutate the input card.
- It raises `ValueError("datetime must be timezone-aware and set to UTC")` if `tzinfo is None or tzinfo != timezone.utc`. Because of that check, `ZoneInfo("UTC")` or pytz UTC objects will probably be rejected (my reading of the code, not tested). Always pass `datetime.now(timezone.utc)` or `.astimezone(timezone.utc)`.

**`get_card_retrievability(card, current_datetime=None) -> float`**
- Returns `0` if `last_review` or `stability` is `None`.
- Otherwise it computes `(1 + FACTOR * elapsed_days / S) ** DECAY`, where `DECAY = -w[20]` and `FACTOR = 0.9 ** (1/DECAY) - 1`.
- `elapsed_days` is `(now - last_review).days`, an integer floor. So R stays **1.0 for the whole first 24 hours**.

**Other `Scheduler` methods:** `reschedule_card(card, review_logs) -> Card`, `to_dict()`, `from_dict()` (classmethod), `to_json(indent=None)` and `from_json()`.
- The dict keys are `parameters`, `desired_retention`, `learning_steps`, `relearning_steps`, `maximum_interval` and `enable_fuzzing`.
- Steps are serialized as integer seconds.

**`Card`**
- Constructor: `Card(card_id: int | None = None, state = State.Learning, step: int | None = None, stability = None, difficulty = None, due = None, last_review = None)`.
- With the default `card_id`, the id is epoch milliseconds **and the constructor calls `time.sleep(0.001)`** to avoid collisions. Pass your own id (for example the skill id) so you don't wait 1 ms per card.
- `step` defaults to 0 when the state is Learning. `due` defaults to now (UTC).
- `to_dict()` keys: `card_id`, `state` (int), `step`, `stability`, `difficulty`, `due` (ISO string), `last_review` (ISO string or `None`).

**`ReviewLog`**
- Constructor: `ReviewLog(card_id, rating, review_datetime, review_duration)`.
- `to_dict()` keys: `card_id`, `rating`, `review_datetime` (ISO string), `review_duration`.
- Has the same `to_json`, `from_dict` and `from_json` methods.

**Enums**
- `State`: `Learning=1`, `Review=2`, `Relearning=3`. There is **no `New` state**: a new card is `Learning` with step 0.
- `Rating`: `Again=1`, `Hard=2`, `Good=3`, `Easy=4`.

**Step logic (from the source)**
- `Good` on the last learning step, or `Easy` at any step, moves the card to `Review` with an interval derived from stability.
- `Again` resets the card to step 0.
- `learning_steps=()` sends a card straight to `Review` after its first review. This suits a Math Academy-style flow that is "graded by mastery quiz".

```python
# pip install "fsrs==6.3.2"
import json
from datetime import datetime, timedelta, timezone
from fsrs import Scheduler, Card, Rating, ReviewLog, State

scheduler = Scheduler(
    desired_retention=0.9,
    learning_steps=(timedelta(minutes=1), timedelta(minutes=10)),
    relearning_steps=(timedelta(minutes=10),),
    maximum_interval=36500,
    enable_fuzzing=True,          # False in tests -> deterministic intervals
)

card = Card(card_id=1017)          # use skill/question id; avoids 1 ms sleep
assert card.state == State.Learning and card.step == 0

now = datetime.now(timezone.utc)
card, log = scheduler.review_card(card, Rating.Good, review_datetime=now, review_duration=12_000)
# -> Learning, step 1, due ≈ now + 10 min

r_week = scheduler.get_card_retrievability(card, current_datetime=now + timedelta(days=7))

blob = json.dumps({"scheduler": scheduler.to_dict(), "card": card.to_dict(), "logs": [log.to_dict()]})

d = json.loads(blob)
scheduler2 = Scheduler.from_dict(d["scheduler"])
card2 = Card.from_dict(d["card"])
logs2 = [ReviewLog.from_dict(x) for x in d["logs"]]
assert card2.due == card.due
card2, log2 = scheduler2.review_card(card2, Rating.Again, review_datetime=card2.due)  # fromisoformat("+00:00") == timezone.utc
```

**Storage suggestion for SQLite**
- Store the `srs_card` columns straight from `Card.to_dict()`, plus an integer `due_ts` (epoch seconds) with an index, for "due now" queries.
- Store an append-only `srs_review_log` table from `ReviewLog.to_dict()`. You need it for `reschedule_card` and the optimizer.
- Keep `Scheduler.to_dict()` in a settings row.

**Optimizer:** `pip install "fsrs[optimizer]"` pulls in torch. Usage: `Optimizer(review_logs).compute_optimal_parameters()`, then `.compute_optimal_retention(params)`. The minimum number of logs it needs is UNVERIFIED.

**Design note (not a library feature):** FSRS has no partial or implicit credit. Math Academy's "credit flows to prerequisites" has to be your own code, for example calling `review_card(prereq, Rating.Good)` on prerequisites that are due when a downstream skill is passed.

---

## 2. PyMuPDF `1.28.2`

Sources: https://pymupdf.readthedocs.io/en/latest/page.html, `document.html`, `textpage.html`, `pixmap.html`, and `src/__init__.py` / `src/utils.py` on `main`.

**Changes in 1.28.x**
- 1.28.2 (2026-08-06) **warns when you `import fitz`**. The exact text is a `FutureWarning`: "The `fitz` API is deprecated … Use `import pymupdf`".
- 1.28.2 also fixes outline/TOC parsing and a `get_texttrace` leak, and makes `find_tables()` faster.
- 1.28.0 (2026-06-29) uses MuPDF 1.28.

**`Document.get_toc(simple=True)`**
- Returns `[lvl, title, page]` entries, with a 4th `dest` dict only when `simple=False`.
- `page` is **1-based**, and `-1` means there is no destination.
- `load_page` and `doc[i]` are **0-based**.

**`Page.get_label()`** (since v1.18.6)
- Returns the printed label (for example `'vii'`), or `''` if the PDF has no `/PageLabels`.
- `doc.get_page_numbers(label, only_one=False)` maps a label to a list of 0-based page numbers. Labels can repeat.
- `doc.get_page_labels()` returns the label rules.

**`Page.get_pixmap(*, matrix=Identity, dpi=None, colorspace=None→csRGB, clip=None, alpha=False, annots=True)`**
- Arguments are keyword-only.
- **`dpi` overrides `matrix`** and also stamps the DPI into the pixmap.
- `clip` is in page points: 1/72 inch, origin top-left.
- `Pixmap.save(filename, output=None, jpg_quality=95)` writes a file. `Pixmap.tobytes(output="png")` returns bytes.

**`Page.cluster_drawings(clip=None, drawings=None, x_tolerance=3, y_tolerance=3, final_filter=True) -> list[Rect]`** (since v1.24.2)
- Joins neighbouring vector paths into bounding rectangles.
- `final_filter` drops rectangles thinner than the tolerance, which removes horizontal rules.
- It **only covers line-art**, not the text labels inside a figure.

**`Page.get_drawings(extended=False)`** returns path dicts with keys such as `type` (`'f'`, `'s'` or `'fs'`), `fill`, `color`, `items` (for example `("re", rect, …)` or `("l", p1, p2)`) and `rect`.

**`Page.get_text(option="text", *, clip=None, flags=None, textpage=None, sort=False, delimiters=None, tolerance=3)`**
- `"dict"` returns `{width, height, blocks:[{type 0|1, bbox, number, lines:[{bbox, wmode, dir, spans:[{bbox, origin, font, size, flags, char_flags, color, alpha, ascender, descender, text}]}]}]}`.
- Span `flags` bits: `TEXT_FONT_SUPERSCRIPT=1`, `ITALIC=2`, `SERIFED=4`, `MONOSPACED=8`, `BOLD=16`.
- `"words"` returns tuples `(x0, y0, x1, y1, word, block_no, line_no, word_no)`.
- The default `TEXTFLAGS_DICT` **includes `TEXT_PRESERVE_IMAGES`**, so image bytes get embedded in the dict. Mask it out.

```python
# pip install "pymupdf==1.28.2"
import pymupdf  # not `import fitz`

doc = pymupdf.open("axler-ladr-4e.pdf")

# TOC -> 0-based page range for one chapter
toc = doc.get_toc(simple=True)                       # [[lvl, title, page1], ...]
def chapter_pages(toc, startswith, n_pages):
    for i, (lvl, title, p) in enumerate(toc):
        if title.strip().startswith(startswith) and p > 0:
            nxt = next((q for l, _, q in toc[i+1:] if l <= lvl and q > 0), n_pages + 1)
            return range(p - 1, nxt - 1)
    raise KeyError(startswith)
pages = chapter_pages(toc, "2", doc.page_count)        # adapt to the real TOC titles

printed = {pno: (doc[pno].get_label() or str(pno + 1)) for pno in pages}

page = doc[pages[0]]
page.get_pixmap(dpi=150).save("page.png")                          # full page
png = page.get_pixmap(dpi=200, clip=pymupdf.Rect(72, 100, 540, 400)).tobytes("png")

# Vector figures: drop plain shaded boxes (theorem/definition backgrounds) first -- heuristic
paths = page.get_drawings()
is_box = lambda p: p["type"] == "f" and len(p["items"]) == 1 and p["items"][0][0] == "re"
words = page.get_text("words", sort=True)
figs = []
for r in page.cluster_drawings(drawings=[p for p in paths if not is_box(p)]):
    r = r + (-8, -8, 8, 8)                        # Rect + 4-tuple = componentwise grow
    for w in words:
        wr = pymupdf.Rect(w[:4])
        if wr.intersects(r):
            r |= wr                               # union -> pull in axis/vector labels
    if r.width > 40 and r.height > 40:
        figs.append(r)
for i, r in enumerate(figs):
    page.get_pixmap(dpi=200, clip=r).save(f"fig_{page.number}_{i}.png")

# Styled text (headings, bold "Definition"/"Theorem" run-ins, margin notes by x0)
d = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES, sort=True)
for b in d["blocks"]:
    if b["type"] != 0:
        continue
    for line in b["lines"]:
        for s in line["spans"]:
            bold = bool(s["flags"] & pymupdf.TEXT_FONT_BOLD)
            sup = bool(s["flags"] & pymupdf.TEXT_FONT_SUPERSCRIPT)
            # s["text"], s["font"], s["size"], s["bbox"]
```

**Practical notes**
- Text extraction **loses LaTeX structure**: fractions, sub- and superscripts split into separate spans, and math-font glyphs map to odd Unicode. Use PyMuPDF for layout, anchors and page numbers. Send page or clip PNGs at 150–200 dpi to the VLM or Nemotron for LaTeX.
- Several things about Axler are UNVERIFIED: whether its PDF defines `/PageLabels` (if not, fall back to parsing the header/footer number from `words`), whether its definition/theorem boxes are drawn as single `re` fills (if they use rounded corners, the `is_box` filter misses them), and the margin-note column position.

---

## 3. Frontend (React 19 + Vite 8 + TypeScript)

```bash
npm create vite@latest web -- --template react-ts      # create-vite 9.2.1
cd web
npm i @xyflow/react@12.12.0 @dagrejs/dagre@3.1.1
npm i react-markdown@10.1.0 remark-gfm@4.0.1 remark-math@6.0.0 rehype-katex@7.0.1 katex@0.16.47
npm i mafs@0.21.0 expr-eval-fork@3.0.3
```

The template's `tsconfig.app.json` sets `verbatimModuleSyntax: true`, so type-only imports need `type`, for example `import { type Node } from '@xyflow/react'`.

### 3a. Knowledge-graph DAG

| | **React Flow** (`@xyflow/react` 12.12.0) + `@dagrejs/dagre` 3.1.1 | **Cytoscape.js** 3.34.3 + `cytoscape-dagre` 4.0.1 |
|---|---|---|
| Size (gz) | ~60 KB + ~16 KB | ~137 KB + ~15 KB (dagre v3 is bundled inside cytoscape-dagre) |
| Rendering | DOM/SVG. Nodes are React components, so you can put KaTeX titles, progress bars or Tailwind classes in them. | Canvas. Labels are plain text; HTML or KaTeX labels need an extra extension. |
| React fit | Declarative, `onNodeClick(event, node)` | Imperative (`useEffect`). `react-cytoscapejs` was last released 2.0.0 in 2022. |
| Layout | You call dagre yourself (synchronous, ~25 lines) | `layout: { name: 'dagre' }` |
| Touch | `zoomOnPinch` and `panOnDrag` are on by default | Built-in tap and pinch |
| Extras | `colorMode` (`light` / `dark` / `system`); the attribution badge may only be hidden with React Flow Pro, per its docs | Graph queries (`predecessors()`, `successors()`), scales to very large graphs |

**Recommendation: React Flow + `@dagrejs/dagre`.** For 10–30 nodes, React-component nodes (math in titles, status badges, accessibility) matter more than canvas performance. Do prerequisite traversal in your own code or on the backend. ELK is overkill here: it is async and about 433 KB gz. React Flow's own guide recommends dagre for tree and DAG layouts: https://reactflow.dev/learn/layouting/layouting.

```tsx
// src/components/SkillGraph.tsx
import { useMemo } from 'react';
import { ReactFlow, Background, Controls, Handle, Position, MarkerType,
         type Node, type Edge, type NodeProps } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from '@dagrejs/dagre';            // default export {graphlib, layout, ...}; named exports also exist

export type SkillStatus = 'locked' | 'available' | 'learning' | 'mastered' | 'due';
export type Skill = { id: string; title: string; status: SkillStatus; prereqs: string[] };

const COLORS: Record<SkillStatus, string> = {
  locked: '#9ca3af', available: '#2563eb', learning: '#d97706', mastered: '#059669', due: '#dc2626',
};
const W = 176, H = 52;
type SkillData = { title: string; status: SkillStatus };   // use `type`, not `interface` (must fit Record<string, unknown>)
type SkillNode = Node<SkillData, 'skill'>;

function SkillNodeView({ data, selected }: NodeProps<SkillNode>) {
  return (
    <div style={{ width: W, height: H, boxSizing: 'border-box', padding: '4px 10px', borderRadius: 10,
      display: 'flex', alignItems: 'center', fontSize: 13, color: '#fff', background: COLORS[data.status],
      outline: selected ? '3px solid #111827' : 'none', outlineOffset: 2 }}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      {data.title}
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}
const nodeTypes = { skill: SkillNodeView };    // keep outside the component (stable reference)

function layout(skills: Skill[]) {
  const ids = new Set(skills.map(s => s.id));
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'TB', nodesep: 24, ranksep: 56 });
  g.setDefaultEdgeLabel(() => ({}));
  skills.forEach(s => g.setNode(s.id, { width: W, height: H }));
  const edges: Edge[] = [];
  skills.forEach(s => s.prereqs.filter(p => ids.has(p)).forEach(p => {   // unknown ids would become size-less dagre nodes
    g.setEdge(p, s.id);
    edges.push({ id: `${p}->${s.id}`, source: p, target: s.id, markerEnd: { type: MarkerType.ArrowClosed } });
  }));
  dagre.layout(g);
  const nodes: SkillNode[] = skills.map(s => {
    const { x, y } = g.node(s.id);             // dagre returns CENTRE coordinates
    return { id: s.id, type: 'skill', position: { x: x - W / 2, y: y - H / 2 },
             data: { title: s.title, status: s.status } };
  });
  return { nodes, edges };
}

export function SkillGraph({ skills, selectedId, onSelect }:
  { skills: Skill[]; selectedId?: string; onSelect: (id: string) => void }) {
  const { nodes, edges } = useMemo(() => layout(skills), [skills]);
  const shown = useMemo(() => nodes.map(n => ({ ...n, selected: n.id === selectedId })), [nodes, selectedId]);
  // The parent of <ReactFlow> MUST have an explicit height
  return (
    <div style={{ width: '100%', height: 'min(70vh, 640px)' }}>
      <ReactFlow nodes={shown} edges={edges} nodeTypes={nodeTypes}
        onNodeClick={(_, n) => onSelect(n.id)}
        nodesDraggable={false} nodesConnectable={false}
        fitView minZoom={0.2} maxZoom={2} colorMode="system">
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
```

**If you choose Cytoscape instead (sketch)**
- Setup: `import cytoscape from 'cytoscape'; import dagre from 'cytoscape-dagre'; cytoscape.use(dagre);`.
- Create the graph in `useEffect` with `cytoscape({ container, elements, style, layout: {...} as unknown as cytoscape.LayoutOptions, autoungrabify: true })`. The cast is needed because the core `LayoutOptions` union has no `'dagre'` member.
- Wire selection with `cy.on('tap', 'node', e => onSelect(e.target.id()))` and clean up with `return () => cy.destroy()`.
- `@types/cytoscape` is now a stub: "cytoscape provides its own type definitions".
- Layout options: `nodeSep`, `rankSep`, `rankDir` (`'TB' | 'LR' | …`), `ranker`, `fit`, `padding`, `animate`. See https://github.com/cytoscape/cytoscape.js-dagre.

### 3b. Markdown + LaTeX: `react-markdown` 10.1.0 + `remark-math` 6.0.0 + `rehype-katex` 7.0.1 + `remark-gfm` 4.0.1 + `katex` 0.16.47

Sources: https://github.com/remarkjs/react-markdown, https://github.com/remarkjs/remark-math, https://github.com/micromark/micromark-extension-math.

```tsx
// src/components/MathMarkdown.tsx
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';   // rehype-katex does NOT import CSS for you

// remark-math only understands $…$ and $$…$$; LLMs often emit \( \) and \[ \]
export const normalizeMathDelimiters = (md: string) =>
  md.replace(/\\\[([\s\S]+?)\\\]/g, (_m, t: string) => `\n$$\n${t.trim()}\n$$\n`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_m, t: string) => `$${t.trim()}$`);

const katexOptions = {
  strict: 'ignore' as const,
  macros: { '\\F': '\\mathbf{F}', '\\R': '\\mathbf{R}', '\\C': '\\mathbf{C}' },  // Axler uses bold F/R/C
};

export function MathMarkdown({ children }: { children: string }) {
  return (
    <div className="lesson-md">  {/* v10 removed the `className` prop -> wrap instead */}
      <Markdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[[rehypeKatex, katexOptions]]}>
        {normalizeMathDelimiters(children)}
      </Markdown>
    </div>
  );
}
```

**Gotchas**
1. **Pin `katex@0.16.x`.**
   - `rehype-katex` 7.0.1 and `mafs` both depend on `katex ^0.16`.
   - KaTeX 0.18.0 (2026-07-17) **prefixed its CSS classes** (a breaking change), and 0.19.0 (2026-10-01) changed `strict` warnings. See https://github.com/KaTeX/KaTeX/blob/main/CHANGELOG.md.
   - If you install `katex@latest`, the CSS you import (0.19) will not match the HTML that `rehype-katex` renders with its own nested 0.16. That the mismatch actually breaks rendering is my inference, not tested.
   - Pinning `0.16.47` dedupes all three packages to one KaTeX.
2. **Delimiters.** Only `$` and `$$` are supported; `\( \)` and `\[ \]` are not.
   - `$$…$$` **inside a paragraph renders inline**. Display math needs `$$` on its own lines.
   - `singleDollarTextMath` defaults to `true`. Set it to `false` if the content contains currency.
   - ```` ```math ```` fences also render, in display mode.
3. **Tables need `remark-gfm`.** CommonMark has no tables, strikethrough or task lists.
   - UNVERIFIED: `|` inside math in a GFM table cell (for example `$|x|$`) probably splits the cell. Use `\lvert x\rvert` instead.
4. **Errors don't crash.** On a bad expression, `rehype-katex` logs a vfile message and re-renders with `throwOnError:false, strict:'ignore'`. If that also fails, it emits `<span class="katex-error">` in `errorColor` (default `#cc0000`). `displayMode` and `throwOnError` are not accepted as options.
5. **JSON escaping from the LLM.** If the model writes a raw `\frac`, `\beta`, `\nabla` or `\theta` inside JSON, those parse as the escapes `\f`, `\b`, `\n` and `\t`, so you silently get control characters. Use OpenAI structured outputs and still scan for `\x0c`, `\x08` and a tab or newline followed by letters, then repair. In TS fixtures, use `String.raw`.
6. **Security.** The setup is safe by default. Do not add `rehype-raw` for LLM content. If you do, add `rehype-sanitize`.

### 3c. 2D math diagrams from a JSON spec

| Option | Size (gz) | React | Vectors + labels | y = f(x) | Constrained slider | Notes |
|---|---|---|---|---|---|---|
| **Mafs 0.21.0** | ~95 KB including KaTeX (~18 KB of its own when KaTeX is deduped) | Native | `<Vector tail tip>` + `<Text attach>` / `<LaTeX>` | `<Plot.OfX>` (adaptive sampling) | `useMovablePoint({ constrain })`, or a plain `<input type=range>` | Dormant since 2025-03. React 19 support UNVERIFIED (peer is `react >=18`). `<LaTeX>` is experimental and broken in Safari. |
| function-plot 1.25.4 | ~59 KB | No (imperative D3 `target`) | `fnType:'vector'` + `graphType:'text'` | String expressions (`'x^2'`) | None built in; re-render on an input change | Lightweight. Infinite zoom and pan. |
| JSXGraph 1.14.0 | ~259 KB | No | `arrow` + labels | `functiongraph` | Native `slider` element | Most capable and heaviest. LGPL or MIT. |
| Plain SVG | 0 | Native | Manual `<marker>` | Sample it yourself | `<input type=range>` | Best for static figures |

**Recommendation: Mafs for interactive diagrams, plain SVG for static ones.**
- Evaluate LLM-supplied expressions with `expr-eval-fork` ≥3.0.0. **Never use `eval` or `new Function`.** Upstream `expr-eval` 2.0.2 has a critical RCE (CVE-2025-12735): https://www.bleepingcomputer.com/news/security/popular-javascript-library-expr-eval-vulnerable-to-rce-flaw/.
- `mathjs` 15.2.0 also works but costs ~197 KB gz.

Mafs API sources: https://mafs.dev/guides/display/mafs. The `<Mafs>` defaults are `height=500`, `pan=true`, `zoom=false` (`zoom` also accepts `{min,max}`) and viewBox `x:[-3,3], y:[-3,3]`. The `useMovablePoint` constrain option takes `"horizontal" | "vertical" | ([x,y]) => [x,y]`.

```tsx
// src/components/MathDiagram.tsx
import { Fragment, useMemo, useState } from 'react';
import { Mafs, Coordinates, Plot, Vector, Text, Theme, Line, useMovablePoint } from 'mafs';
import 'mafs/core.css';                        // optional 'mafs/font.css' adds ~220 kB
import { Parser } from 'expr-eval-fork';       // UNVERIFIED: ESM named import (README shows the CJS form)

export type DiagramSpec = {
  view?: { x: [number, number]; y: [number, number] };
  vectors?: { tip: [number, number]; tail?: [number, number]; label?: string; color?: string }[];
  functions?: { expr: string; color?: string }[];          // variables: x and slider.name
  slider?: { name: string; min: number; max: number; step: number; value: number };
};
const parser = new Parser();

export function MathDiagram({ spec }: { spec: DiagramSpec }) {
  const s = spec.slider;
  const [k, setK] = useState(s?.value ?? 0);
  const allowed = new Set(['x', ...(s ? [s.name] : [])]);
  const fns = useMemo(() => (spec.functions ?? []).flatMap(f => {
    try {
      const ex = parser.parse(f.expr);
      return ex.variables().every(v => allowed.has(v)) ? [{ ...f, ex }] : [];
    } catch {
      return [];
    }
  }), [spec.functions, s?.name]);
  const scope = s ? { [s.name]: k } : {};
  return (
    <figure>
      <Mafs height={320} viewBox={spec.view ?? { x: [-5, 5], y: [-5, 5] }} zoom={{ min: 0.5, max: 4 }}>
        <Coordinates.Cartesian />
        {fns.map((f, i) => (
          <Plot.OfX key={i} y={x => Number(f.ex.evaluate({ ...scope, x }))} color={f.color ?? Theme.blue} />
        ))}
        {(spec.vectors ?? []).map((v, i) => (
          <Fragment key={i}>
            <Vector tail={v.tail ?? [0, 0]} tip={v.tip} color={v.color ?? Theme.pink} />
            {v.label && <Text x={v.tip[0]} y={v.tip[1]} attach="ne" size={16}>{v.label}</Text>}
          </Fragment>
        ))}
      </Mafs>
      {s && (
        <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {s.name} = {k.toFixed(2)}
          <input type="range" min={s.min} max={s.max} step={s.step} value={k}
                 onChange={e => setK(Number(e.target.value))} style={{ flex: 1 }} />
        </label>
      )}
    </figure>
  );
}

// In-plot slider: a knob confined to the segment y=-4, x in [-3,3], snapping to 0.25
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
export function ScalarMultipleDemo() {
  const knob = useMovablePoint([1, -4], {      // start ON the constraint (open issue: initial point is not constrained)
    constrain: ([x]) => [clamp(Math.round(x * 4) / 4, -3, 3), -4],
  });
  const c = knob.x;
  return (
    <Mafs height={300} viewBox={{ x: [-4, 4], y: [-5, 4] }}>
      <Coordinates.Cartesian />
      <Line.Segment point1={[-3, -4]} point2={[3, -4]} />
      <Vector tip={[c, c / 2]} />
      <Text x={c} y={c / 2} attach="ne" size={16}>{`${c}·v`}</Text>
      <Plot.OfX y={x => c * Math.sin(x)} />
      {knob.element}
    </Mafs>
  );
}
```

**Plain-SVG fallback for static vectors**
- Define one `<marker id={useId()} viewBox="0 0 10 10" refX="9" refY="5" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z"/></marker>`.
- Map `(x, y) → (cx + x·s, cy − y·s)` and draw `<line markerEnd={`url(#${id})`}>` + `<text>` for each vector.
- Use `useId()` so that several diagrams on one page don't share marker ids.

---

## UNVERIFIED items

- None of the snippets were executed or type-checked, because no packages were installed. The py-fsrs and PyMuPDF signatures come from `main`-branch source, assumed identical to the 6.3.2 and 1.28.2 releases.
- Mafs on React 19.3 and TS 6: the peer range allows it, but it is untested. The upstream repo has had no commits since 2025-03-30.
- What actually happens when KaTeX 0.16 HTML meets 0.18+ CSS is inferred from the 0.18.0 class-prefix change.
- The `expr-eval-fork` ESM named export, the exact type of `g.node()` in `@dagrejs/dagre` 3.x, and the behaviour of `|` inside math in GFM tables.
- Axler-specific assumptions: whether the PDF has page labels, how the definition/theorem boxes are drawn (`re` fill vs rounded path), and the math font names and margin-note column.
- The `Optimizer`'s minimum review-log requirements.