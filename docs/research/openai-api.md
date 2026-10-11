# OpenAI API for Universal Academy: models, prices and Python usage (checked 2026-10-05)

## 0. Summary

All four jobs should go through the **Responses API** with **Structured Outputs** (`client.responses.parse(..., text_format=PydanticModel)`), using SDK **`openai==3.24.0`** (released 2026-10-02).

| Job | Model | `reasoning.effort` | Why | Cheaper / stronger option |
|---|---|---|---|---|
| (a) Page image → Markdown+LaTeX, plus figure bboxes | `gpt-6-luna` | `low` (try `medium`) | $0.10 / $0.50 per 1M tokens. In third-party OCR evals it scores about the same as Sol. | Luna is weak at box localization (see §2), so take figure bboxes from PyMuPDF. Use `gpt-6.1-sol` only on pages that fail checks. |
| (b) Prerequisite graph (10–25 skills) from 20–40 pages | `gpt-6.1-sol` | `high` (try `xhigh`) | Docs call it "near-Astra performance at a lower cost". It runs once per chapter. | `gpt-6-astra` at `medium`/`high` costs about 4–5× more (still about $2 a chapter). `reasoning.mode: "pro"` is another upgrade. |
| (c) 15–25 lesson packages in parallel | `gpt-6.1-sol` | `medium` | Good quality, and cached reads cost 0.05× | `service_tier="flex"` halves the price. `gpt-6-luna` at `xhigh` is about 20× cheaper; evaluate it before switching. |
| (d) Re-solve questions on their own | `gpt-6-luna` | `high` | Very cheap | Send disagreements to `gpt-6.1-sol`. The Batch API or Flex gives another 50% off. |

Do not use: `gpt-5-mini`/`gpt-5-nano` 2025-08-07 snapshots (shut down Dec 11, 2026), `o4-mini` and `gpt-4.1-nano` (shut down Oct 23, 2026), and `gpt-5.4-nano` (shut down Apr 1, 2027) ([deprecations](https://developers.openai.com/api/docs/deprecations)).

---

## 1. Current model lineup (primary source: developers.openai.com)

OpenAI's [models page](https://developers.openai.com/api/docs/models) says: *"If you're not sure where to start, use GPT-6 Astra… Choose GPT-6.1 Sol to balance intelligence and cost, or GPT-6 Luna for cost-sensitive, high-volume workloads."* It also says all latest models take text and image input and produce text output.

Release dates, from secondary sources: Astra on 2026-09-03, Sol and Luna on 2026-09-22, and GPT-6.1 Sol on 2026-09-29 ([SiliconANGLE](https://siliconangle.com/2026/09/29/openais-gpt-6-1-sol-delivers-astra-like-performance-at-a-dramatically-lower-price/)). The SDK changelog backs up the last date: v3.21.0 (2026-09-29) says "add GPT-6.1 Sol model identifier" ([CHANGELOG](https://github.com/openai/openai-python/blob/main/CHANGELOG.md)).

### GPT-6 family (recommended)

| | `gpt-6-astra` | `gpt-6.1-sol` | `gpt-6-luna` |
|---|---|---|---|
| Positioning | "Most capable model for the most demanding work" | "Near-Astra performance for complex work at a lower cost" | "Most efficient model for focused, high-volume tasks" |
| Context / max input / max output | 1,050,000 / 922,000 / 128,000 | same | same |
| Knowledge cutoff | Apr 30, 2026 | Apr 30, 2026 | May 18, 2026 |
| Modalities | text + image in, text out | same | same |
| `reasoning.effort` | `low`, `medium`, `high`, `xhigh`, `max` (`none` returns HTTP 400) | `low`, `medium` (default), `high`, `xhigh`, `max`. No `none` or `minimal`. | `none`, `low`, `medium` (default), `high`, `xhigh`, `max` |
| Endpoints | Responses, Chat Completions, Batch | same (tool calling only works in Responses) | same (Chat Completions supports function calling only with effort `none`) |
| Tier 1 → Tier 5 RPM / TPM | 500/500K → 15,000/40M | 500/500K, 5K/1M, 5K/2M, 10K/4M, 15K/40M | 500/500K, 5K/2M, 5K/4M, 10K/10M, 30K/180M |

Sources: [gpt-6-astra](https://developers.openai.com/api/docs/models/gpt-6-astra), [gpt-6.1-sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [gpt-6-luna](https://developers.openai.com/api/docs/models/gpt-6-luna), [Using GPT-6](https://developers.openai.com/api/docs/guides/latest-model).

`gpt-6-sol` has been replaced by `gpt-6.1-sol` (its page says "See GPT-6.1 Sol for the newer Sol model"). The GPT-5.6 tier names map to the old sizes: `gpt-5.6-terra` "roughly corresponds to the mini model tier" and `gpt-5.6-luna` to the "nano model tier". There are no "-mini" or "-nano" IDs in the GPT-6 family; Sol and Luna fill those roles.

### Prices per 1M tokens ([pricing](https://developers.openai.com/api/docs/pricing)), short context (≤272K input tokens)

| Model | Input | Cached input | Cache write | Output | Batch = Flex (in / out) |
|---|---:|---:|---:|---:|---:|
| `gpt-6-astra` | 10.00 | 1.00 | 12.50 | 50.00 | 5.00 / 25.00 |
| `gpt-6.1-sol` | 2.00 | **0.10** (5%) | 2.50 | 10.00 | 1.00 / 5.00 |
| `gpt-6-luna` | 0.10 | 0.01 | 0.125 | 0.50 | 0.05 / 0.25 |
| `gpt-6-sol` (replaced) | 2.00 | 0.20 | 2.50 | 10.00 | 1.00 / 5.00 |
| `gpt-5.6-sol` | 4.00 | 0.40 | 5.00 | 20.00 | 2.00 / 10.00 |
| `gpt-5.6-terra` | 2.00 | 0.20 | 2.50 | 12.00 | 1.00 / 6.00 |
| `gpt-5.6-luna` | 0.20 | 0.02 | 0.25 | 1.20 | 0.10 / 0.60 |
| `gpt-5.5` | 5.00 | 0.50 | – | 30.00 | 2.50 / 15.00 |
| `gpt-5.4-mini` | 0.75 | 0.075 | – | 4.50 | 0.375 / 2.25 |
| `gpt-5.4-nano` (deprecated) | 0.20 | 0.02 | – | 1.25 | 0.10 / 0.625 |

Pricing modifiers:
- **Long context:** above 272K input tokens, the whole request is billed at 2× input/cache and 1.5× output.
- **Fast mode:** 2×. It was called "Priority processing" until 2026-07-30; use `service_tier="fast"` or `"priority"`.
- **Ultrafast:** `gpt-6-astra` only, $60 / $300.
- **Regional processing / data residency:** +10%.
- **Reasoning tokens:** billed as output tokens.

---

## 2. Model choice and cost estimates for each job

The cost figures are estimates. Reasoning-token counts in particular are UNVERIFIED; measure them with `resp.usage.output_tokens_details.reasoning_tokens`.

### (a) Vision transcription: `gpt-6-luna`, effort `low`, `detail: "original"`

**Image tokens.** For `gpt-6-astra` and `gpt-5.6-*`, images are cut into 32×32 patches and multiplied by 1.2 ([vision guide](https://developers.openai.com/api/docs/guides/images-vision#calculating-costs)). For a 1700×2200 PNG:
- `original`: 54×69 = 3,726 patches, which is **≈4,472 tokens**. The image is not resized (the limits are 65,535 px per side and 30,000 patches).
- `high`: 2,500-patch budget, resized to 1376×1780, which is **≈2,890 tokens**.

**UNVERIFIED:** the sizing table and the [image cost calculator](https://developers.openai.com/api/docs/guides/image-cost-calculator) do not list `gpt-6-luna` or `gpt-6.1-sol`. I assume Astra's rules apply. Confirm with `usage.input_tokens` on a test page.

**Which detail level.** The docs say: *"For tasks that require fine visual detail or precise coordinates, such as OCR… use `"detail": "original"` when supported."* If an image does get resized, map the returned coordinates back to the original size. At 1700×2200 under `original` there is no resizing, so ask for coordinates normalized to 0–1.

**Cost.** About 5.5K input tokens ($0.00055) plus 2–4K output tokens ($0.001–0.002) comes to **≈$0.002–0.003 per page, or about $0.10 for 40 pages**. Doing the same with `gpt-6.1-sol` costs about $0.04–0.05 per page.

**Quality evidence (third party, not OpenAI).** [Roboflow Vision Evals](https://playground.roboflow.com/models/compare/gpt-6-1-sol-vs-gpt-6-luna) (updated 2026-09-29):

| Task | Luna (low / high effort) | Sol 6.1 (low / high effort) |
|---|---|---|
| OCR | 90.6 / 91.9 | 92.0 / 91.7 |
| Object detection (bounding boxes) | 65.5 / 68.0 | 80.8 / 81.6 |
| Cost and latency per sample | $0.0004, 9.9 s | $0.0061, 14.3 s |

The vision guide also lists "spatial reasoning… precise spatial localization" as a known weakness.

**Design implication.** Use Luna for the text. For a born-digital PDF, take figure bboxes from PyMuPDF (vector drawing and image extents) and have the model only attach labels and captions. Alternatively, send pages that fail a check (for example an empty bbox, or unbalanced `$`) to `gpt-6.1-sol`.

### (b) Knowledge graph: `gpt-6.1-sol`, effort `high`

20–40 pages is roughly 30–60K input tokens, well under 272K, so short-context pricing applies.
- Input: about $0.06–0.12.
- Output (graph JSON plus reasoning, say 15–40K tokens): about $0.15–0.40.
- Total: **about $0.25–0.5 per chapter.**

The [model-selection guide](https://developers.openai.com/api/docs/guides/model-selection) suggests "GPT-6.1 Sol · Extra high" for "decisions built from conflicting evidence", and "If cost and latency aren't a concern, you can default to Astra". Astra at `high` would cost about $1.5–2.5 per chapter.

Reasoning mode (standard or pro) is separate from effort. `reasoning={"mode": "pro", "effort": "medium"}` is supported on GPT-5.6 and GPT-6, and is billed at normal token rates but uses more tokens ([reasoning guide](https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode)).

### (c) Lessons: `gpt-6.1-sol`, effort `medium`, with an explicitly cached shared prefix

Put the instructions and chapter Markdown in one developer `input_text` block with an explicit cache breakpoint. Make one call first to write the cache, then send the rest in parallel. Reads on Sol cost 0.05× input.

For a 50K-token prefix: one write ≈ $0.125, then 24 reads ≈ $0.005 each. Output of 8–15K tokens per lesson ≈ $0.08–0.15. **Total about $2.5–4 per chapter, or about half that with `service_tier="flex"`.**

### (d) Verification: `gpt-6-luna`, effort `high`

About 2K input and 2–6K output per question is about $0.003, so 125 questions cost **about $0.40**. Use Batch (`/v1/responses` is supported, 24-hour window, −50%) or Flex.

Put `work` before `final_answer` in the schema, because Structured Outputs keeps the schema's key order. Do not include the original solution in the prompt.

---

## 3. Python SDK

- **Package:** `openai` **3.24.0** (2026-10-02), Python ≥3.10 ([PyPI](https://pypi.org/project/openai/)). Install with `pip install "openai>=3.24,<4"`.
- **Breaking change in 3.0.0 (2026-08-12):** *"HTTPX2 is now the default HTTP client, and `httpx` is no longer installed automatically."* Any custom `http_client`, transport, or mock must use `httpx2` ([httpx2.md](https://github.com/openai/openai-python/blob/main/httpx2.md)).
- **TLS:** HTTPX2 uses the **OS trust store**, and `certifi` is no longer installed. Slim Docker images need `ca-certificates`, or set `SSL_CERT_FILE`.
- **Client defaults** (checked in the installed package):
  - `max_retries=2`
  - timeout `Timeout(connect=5, read=600, write=600, pool=600)`
  - connection limits: 1,000 connections, 100 keep-alive
  - it reads `OPENAI_API_KEY`
  - `OpenAI(...)` / `AsyncOpenAI(...)` also take `organization`, `project`, `base_url`, `data_residency`, `timeout`, `max_retries`, `http_client`
- **Retries** (from `_base_client.py`):
  - Retried: connection errors, 408, 409, 429, ≥500, and anything with `x-should-retry: true`.
  - Backoff: 0.5 s × 2ⁿ, capped at 8 s, with up to 25% jitter.
  - `Retry-After` / `retry-after-ms` is honored up to 120 s. Larger values mean no retry.
  - Stream consumption is never retried.
- **Exceptions:**

  | Exception | Status |
  |---|---|
  | `BadRequestError` | 400 |
  | `AuthenticationError` | 401 |
  | `PermissionDeniedError` | 403 |
  | `NotFoundError` | 404 |
  | `UnprocessableEntityError` | 422 |
  | `RateLimitError` | 429 |
  | `InternalServerError` | ≥500 |
  | `APIConnectionError` / `APITimeoutError` | no status |

  All inherit from `APIError`; `APIStatusError` has `.status_code`, `.code` and `.request_id`. Successful objects have `._request_id` ([README](https://github.com/openai/openai-python/blob/main/README.md)).
- **aiohttp transport:** `pip install "openai[aiohttp]"`, then `AsyncOpenAI(http_client=DefaultAioHttpClient())`.

### Responses API parameters (from the `responses.create` / `responses.parse` signatures in 3.24.0)

`model, input, instructions, reasoning, text, max_output_tokens, store, service_tier, prompt_cache_key, prompt_cache_options, prompt_cache_retention, previous_response_id, tools, tool_choice, stream, background, truncation, metadata, safety_identifier, temperature, top_p, top_logprobs, include, …`. `parse()` adds `text_format`.

- **`reasoning`**: `{"effort": "none"|"minimal"|"low"|"medium"|"high"|"xhigh"|"max", "mode": "standard"|"pro", "summary": "auto"|"concise"|"detailed", "context": "auto"|"current_turn"|"all_turns"}`. Supported values differ by model (§1).
- **Verbosity**: `text={"verbosity": "low"|"medium"|"high"}`, default `medium`. It can be combined with `text_format`. Passing `text={"format": ...}` together with `text_format` raises `TypeError` in the SDK. `parse()` also accepts a top-level `verbosity=` keyword, but that sends a top-level field; use `text={...}` instead.
- **GPT-6 restrictions:** when effort is not `none`, *"remove `temperature`, `top_p`, and `top_logprobs`"*. Replace `prompt_cache_retention` with `prompt_cache_options={"ttl": "30m"}` ([migration](https://developers.openai.com/api/docs/guides/latest-model#migration-quickstart)).
- **Output budget:** `max_output_tokens` covers reasoning plus visible output. If the limit is hit, `resp.status == "incomplete"` and `resp.incomplete_details.reason == "max_output_tokens"`, and this can happen before any visible text. OpenAI suggests reserving "at least 25,000 tokens for reasoning and outputs" at first ([reasoning guide](https://developers.openai.com/api/docs/guides/reasoning#allocating-space-for-reasoning)).
- **Storage:** responses are **stored by default**. Pass `store=False` for single-turn jobs ([migrate guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)).
- **Image input:** `{"type": "input_image", "image_url": "data:image/png;base64,...", "detail": "low"|"high"|"original"|"auto"}`. Detail defaults to `auto`, and for Astra and GPT-5.6 `auto` behaves like `original`. Accepted formats are PNG, JPEG, WEBP and non-animated GIF, up to 512 MB and 1,500 images per request, with at most 30,000 patches per image (larger images are rejected, not resized).
- **Listing models:** `client.models.list()` pages automatically (`[m.id for m in client.models.list()]`; for async, `[m.id async for m in aclient.models.list()]`). `client.models.retrieve("gpt-6-luna")` returns one model.

### Prompt-caching cost trap on GPT-5.6 and later (includes Luna and Sol 6.1)

From the [prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching):
- **Writes cost 1.25×.** Reads cost 0.1×, or 0.05× on `gpt-6.1-sol`.
- **Implicit mode is the default.** It places a breakpoint *at the end of the latest user message*, so a one-off request (each page image, each verification) with ≥1,024 tokens of input pays **+25% on input for a cache entry that is never reused**.
- **Fix for one-off calls:** `prompt_cache_options={"mode": "explicit"}` with no breakpoints means no cache writes.
- **Fix for the lesson fan-out:** use explicit mode and add `"prompt_cache_breakpoint": {"mode": "explicit"}` to the shared developer `input_text` block. Top-level `instructions` cannot hold a breakpoint.
- **Minimum cacheable prefix:** 1,024 tokens.
- **TTL:** `"30m"` is the only value.
- **Rate limits:** cached tokens still count toward TPM.
- **Prewarming:** `prompt_cache_options={"prewarm": True}` writes the cache without generating output.

### Rate limits and concurrency

- **How TPM is estimated:** *"the maximum of `max_tokens` and the estimated number of tokens"* ([rate limits](https://developers.openai.com/api/docs/guides/rate-limits)). On Tier 1, `gpt-6.1-sol` allows 500K TPM, so 25 parallel calls with a 50K prefix and `max_output_tokens=32000` will hit 429s. Either limit concurrency to about 4–6 or lower `max_output_tokens`.
- **Error codes:**
  - `429 rate_limit_error / slow_down` means traffic ramped up too fast.
  - `503 service_unavailable_error / server_is_overloaded` means the model is overloaded; the SDK raises `InternalServerError`.
  - Follow `Retry-After`. Above 1M TPM, grow traffic by no more than 50% every 15 minutes.
- **Flex:** a `429 Resource Unavailable` is **not charged**. Retry, or fall back to `service_tier="auto"`. Raise the timeout to about 900 s ([flex](https://developers.openai.com/api/docs/guides/flex-processing)).

---

## 4. Structured Outputs: strict JSON Schema rules ([guide](https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas))

1. **Every property must be in `required`, and every object must set `additionalProperties: false`.** The SDK's `to_strict_json_schema` (used by `parse`/`text_format`) does both automatically. I checked this in 3.24.0.
2. **Optional fields:** use a nullable union that is still required, e.g. `Optional[str]` / `str | None` → `{"anyOf":[{"type":"string"},{"type":"null"}]}`. The SDK removes `default: None`.
3. **Avoid non-None defaults.** `x: int = 3` is sent as `"default": 3`, and `default` is not a documented supported keyword. Whether the API rejects it is UNVERIFIED.
4. **No `dict[str, X]`.** Pydantic turns it into `additionalProperties: {schema}`, which breaks rule 1. Use `list[KeyValue]` instead.
5. **Supported keywords:**
   - strings: `pattern`, `format` (date-time, time, date, duration, email, hostname, ipv4, ipv6, uuid)
   - numbers: `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`
   - arrays: `minItems`, `maxItems`
   - also enums, `anyOf`, `$defs`/`$ref`, and recursion (`#`)

   `minLength`/`maxLength` (what `Field(max_length=…)` emits) are not on the supported list, so check lengths in Python.
6. **Unsupported:** `allOf`, `not`, `if`/`then`/`else`, `dependentRequired`, `dependentSchemas`. Fine-tuned models also lose `patternProperties` and the string/number/array constraints.
7. **The root must be an object, not `anyOf`.** A discriminated union at the root is not allowed.
8. **Size limits:** ≤5,000 properties, ≤10 nesting levels, ≤120,000 characters across names, enums and consts, ≤1,000 enum values.
9. **Output follows the schema's key order.** Put reasoning or work fields before answer fields.
10. **Refusals and parsing:** a refusal gives `output_parsed is None` and a `refusal` content item. `parse` only parses messages with `phase == "final_answer"` or no phase, and invalid JSON raises a validation error ([helpers.md](https://github.com/openai/openai-python/blob/main/helpers.md)).
11. **LaTeX:** backslashes come back JSON-escaped and are decoded by Pydantic, so `"\\frac"` in the raw JSON becomes `\frac` in Python.
12. **Use Structured Outputs, not JSON mode.** JSON mode is `{"type": "json_object"}`; the docs prefer `json_schema` with `strict: true`.

---

## 5. Minimal examples

I ran these offline against `openai==3.24.0` and `pydantic==2.13.5` with an `httpx2.MockTransport`, which checks the request body and that `output_parsed` is filled. I did **not** call the live API, because no key was used.

### 5.1 Text → Pydantic (knowledge graph)

```python
from typing import Literal, Optional
from openai import OpenAI
from pydantic import BaseModel, Field

client = OpenAI()  # uses OPENAI_API_KEY

class Skill(BaseModel):
    id: str = Field(description="kebab-case slug, unique in this graph")
    title: str
    summary: str
    prerequisites: list[str] = Field(description="ids of DIRECT prerequisite skills in this graph")
    source_pages: list[int]
    difficulty: Literal[1, 2, 3, 4, 5]

class SkillGraph(BaseModel):
    skills: list[Skill]
    notes: Optional[str]          # nullable but still required (strict mode)

resp = client.responses.parse(
    model="gpt-6.1-sol",
    reasoning={"effort": "high"},
    text={"verbosity": "low"},     # may be combined with text_format
    instructions="Decompose the chapter into 10-25 atomic micro-skills forming a DAG.",
    input=chapter_markdown,
    text_format=SkillGraph,
    max_output_tokens=40000,
    store=False,
)
if resp.status == "incomplete":
    raise RuntimeError(resp.incomplete_details.reason)
graph = resp.output_parsed        # SkillGraph | None (None => refusal)
print(resp.usage.output_tokens_details.reasoning_tokens)
```

The same request as a raw JSON schema with `create()`:

```python
resp = client.responses.create(
    model="gpt-6.1-sol",
    input="...",
    text={"format": {"type": "json_schema", "name": "skill_graph", "strict": True,
                     "schema": SkillGraph.model_json_schema()}},  # must already be strict-compliant
)
data = json.loads(resp.output_text)
```

If you hand-write the schema, add `required` and `additionalProperties: false` at every level yourself, or use `openai.lib._pydantic.to_strict_json_schema(SkillGraph)`. That is a private helper.

### 5.2 Image + text → Pydantic (page transcription)

```python
import base64
from typing import Literal, Optional
from openai import OpenAI
from pydantic import BaseModel, Field

client = OpenAI()

class BBox(BaseModel):
    x0: float = Field(description="left edge, fraction of page width 0-1")
    y0: float = Field(description="top edge, fraction of page height 0-1")
    x1: float
    y1: float

class Figure(BaseModel):
    label: Optional[str]           # e.g. "Figure 1.3"
    caption: Optional[str]
    bbox: BBox

class PageTranscription(BaseModel):
    markdown: str = Field(description="Markdown; inline math $...$, display $$...$$")
    figures: list[Figure]
    page_kind: Literal["content", "exercises", "front_matter", "blank"]

b64 = base64.b64encode(open("page_012.png", "rb").read()).decode()
resp = client.responses.parse(
    model="gpt-6-luna",
    reasoning={"effort": "low"},
    input=[
        {"role": "developer", "content": "Transcribe the textbook page exactly. Preserve theorem/definition boxes as blockquotes ..."},
        {"role": "user", "content": [
            {"type": "input_text", "text": "Page 12."},
            {"type": "input_image", "image_url": f"data:image/png;base64,{b64}", "detail": "original"},
        ]},
    ],
    text_format=PageTranscription,
    prompt_cache_options={"mode": "explicit"},   # one-off request: avoid the 1.25x cache-write charge
    max_output_tokens=16000,
    store=False,
)
page = resp.output_parsed
```

### 5.3 Async fan-out with concurrency, retries and a cached shared prefix (lessons)

```python
import asyncio, openai
from openai import AsyncOpenAI

client = AsyncOpenAI(max_retries=5, timeout=900.0)   # SDK retries 408/409/429/5xx, honors Retry-After
SEM = asyncio.Semaphore(6)                             # size to your TPM tier (see §3)

async def parse_call(model, effort, input, schema, **kw):
    async with SEM:
        try:
            r = await client.responses.parse(model=model, reasoning={"effort": effort},
                                             input=input, text_format=schema, store=False, **kw)
        except openai.BadRequestError as e:            # bad schema / unsupported param / bad image
            raise RuntimeError(f"400 {e.code}: {e.message}") from e
        except openai.RateLimitError as e:             # raised after retries are exhausted
            if e.code == "insufficient_quota":
                raise RuntimeError("out of credits") from e
            raise
    if r.status == "incomplete":
        raise RuntimeError(r.incomplete_details.reason)
    if r.output_parsed is None:
        raise RuntimeError("refusal / no parsed output")
    return r.output_parsed

def lesson_input(chapter_md: str, skill_json: str):
    return [
        {"role": "developer", "content": [{
            "type": "input_text",
            "text": LESSON_INSTRUCTIONS + "\n<chapter>\n" + chapter_md + "\n</chapter>",
            "prompt_cache_breakpoint": {"mode": "explicit"},     # cache the stable prefix only
        }]},
        {"role": "user", "content": f"Write the lesson for:\n{skill_json}"},
    ]

async def generate_lessons(chapter_md, skills_json):
    kw = dict(prompt_cache_options={"mode": "explicit"}, max_output_tokens=24000)
    first = await parse_call("gpt-6.1-sol", "medium", lesson_input(chapter_md, skills_json[0]), Lesson, **kw)  # writes cache
    rest = await asyncio.gather(*(parse_call("gpt-6.1-sol", "medium", lesson_input(chapter_md, s), Lesson, **kw)
                                  for s in skills_json[1:]), return_exceptions=True)
    return [first, *rest]
```

To check cache use, look at `r.usage.input_tokens_details.cached_tokens` and `cache_write_tokens`. For Flex, add `service_tier="flex"` and, on `RateLimitError`, retry once without it.

---

## 6. UNVERIFIED and open items

- **Image handling for `gpt-6-luna` and `gpt-6.1-sol`:** neither appears in the vision sizing table, the token-multiplier table or the calculator. Detail levels, the 1.2× multiplier and patch rules are assumed to match Astra. Measure `usage.input_tokens` on one page.
- **Schema keywords:** whether the API rejects `default` or `minLength`/`maxLength` in strict mode on base models is untested; the docs only leave them off the supported list.
- **Benchmarks:** no primary-source math results (AIME, FrontierMath) for Luna or Sol 6.1 were found. The vision numbers are Roboflow's, not OpenAI's. The openai.com announcement pages returned 403.
- **Cost figures:** all per-job costs and reasoning-token amounts are estimates.
- **`insufficient_quota`:** I did not confirm that OpenAI sends `x-should-retry: false` on this error, so the SDK might retry it. Check `e.code` as in §5.3.
- **Live testing:** none of the examples were sent to the live API; they were checked offline as described in §5. The offline check scripts are in a scratch folder outside the project (`<scratchpad>/oai/`, not in the repo): `verify_examples.py`, `ua_llm.py`, `test_ua_llm.py`.