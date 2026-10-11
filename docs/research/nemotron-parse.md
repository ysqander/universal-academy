# NVIDIA document parsing models (Nemotron Parse) for Universal Academy: research notes (as of 2026-10-05)

## 0. Bottom line

- **Use `nvidia/nemotron-parse-2.0`** on `https://integrate.api.nvidia.com/v1/chat/completions`. It is an OpenAI-compatible chat-completions call. You send one page image as an `image_url` (a base64 data URI works) plus a **text part that holds the control-token prompt**. It does **not** take `tools`.
  - The answer is a **raw string**: `<x_..><y_..>markdown/LaTeX<x_..><y_..><class_X>` blocks, in reading order.
  - The coordinates are normalized to a **padded 1664×2048 canvas**, not to your image. I checked this against NVIDIA's own sample outputs (§2.5).
- The **legacy hosted `nvidia/nemotron-parse`** (v1.x) still uses the tools interface (`markdown_bbox` | `markdown_no_bbox` | `detection_only`). It returns clean JSON with image-normalized bboxes. Floating elements (tables, figures, captions) come back out of reading order, so treat it as the fallback.
- **For a math textbook, Nemotron Parse alone is not enough, and neither is GPT vision alone.** Recommended split:
  - **PyMuPDF** gives exact geometry for born-digital features: colored definition/theorem boxes, vector figures, the exact prose text layer.
  - **Nemotron Parse 2.0** gives layout classes, bboxes, reading order and first-pass LaTeX. It is free on the trial tier and deterministic.
  - **GPT** adds the meaning: Definition/Theorem/Proof/Example labels, normalizing math to LaTeX, and repairing broken LaTeX, anchored to Nemotron block IDs.
  - Details in §6, code in §7.
- The other hosted NVIDIA doc models (page-elements-v3, OCR v2, Nano 12B v2 VL, Nemotron 3 Nano Omni) are **not good primary parsers for math** (§4).

---

## 1. Model landscape (verified 2026-10-05)

| Model (hosted id) | Endpoint | Interface | Released | Weights license | Fit for math pages |
|---|---|---|---|---|---|
| **`nvidia/nemotron-parse-2.0`** | `integrate.api.nvidia.com/v1/chat/completions` | Prompt control tokens in a text part; raw token string out | Build 09/11/2026; HF repo created 2026-06-30. The HF card text says "August 3, 2026" ([NIM card](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-parse-2_0), [HF](https://huggingface.co/nvidia/NVIDIA-Nemotron-Parse-2.0)) | OpenMDW-1.1, plus CC-BY-4.0 for the tokenizer | **Best NVIDIA option.** Layout, bboxes, reading order, LaTeX, Formula/Picture classes |
| `nvidia/nemotron-parse` (v1.x, weights "overjoyed-adder") | same | `tools` = `markdown_bbox` / `markdown_no_bbox` / `detection_only`; JSON in `tool_calls` | Build 10/28/2025 ([overview](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-parse)) | NVIDIA Community Model License (hosted card) | Fallback. Floating elements out of order |
| `nvidia/nemoretriever-parse` (old name, formerly "eclair") | same | API reference page still exists ([link](https://docs.api.nvidia.com/nim/reference/nvidia-nemoretriever-parse-infer)) | older | — | Don't use. UNVERIFIED whether the endpoint is still live |
| HF only: `NVIDIA-Nemotron-Parse-v1.1` (2025-11-17), `-v1.1-TC` (faster), `-v1.2` (2026-02-17) | self-host (vLLM / Transformers, Linux + NVIDIA GPU) | prompt tokens | — | v1.1: NVIDIA Open Model License; v1.2: NVIDIA Nemotron Open Model License | Superseded by 2.0 |
| `nemotron-page-elements-v3` (YOLOX, 54M) | `ai.api.nvidia.com/v1/cv/nvidia/nemotron-page-elements-v3` | `{"input":[{"type":"image_url","url":"data:image/png;base64,..."}]}` | Build 03/02/2026 | NVIDIA Open Model License | **Poor.** Classes are only table / chart / infographic / title / header-footer / text: no Picture, no Formula ([HF](https://huggingface.co/nvidia/nemotron-page-elements-v3)) |
| `nemotron-ocr-v2` | `ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v2` | same `input` body; `text_detections[]` with quad points | HF 04/15/2026, Build 06/12/2026 | NVIDIA Open Model License | **Poor.** Plain word/line OCR, no LaTeX ([HF](https://huggingface.co/nvidia/nemotron-ocr-v2)) |
| `nvidia/nemotron-nano-12b-v2-vl` | integrate chat completions | general VLM | 10/28/2025 | NVIDIA Open Model License | Possible GPT substitute. OCRBench 85.6, DocVQA 94.39. Max 12 tiles of 512², e.g. 1536×2048 ([card](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-nano-12b-v2-vl)). LaTeX quality UNVERIFIED |
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | integrate chat completions | general omni model | 04/28/2026 | NVIDIA Open Model Agreement | Possible NVIDIA-only cleanup LLM. OCRBenchV2-EN about 65.8–67.0 ([card](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-nano-omni-30b-a3b-reasoning)). Math UNVERIFIED |

Notes:
- "Nemotron Nano 2 VL" is `nemotron-nano-12b-v2-vl`.
- "nemoretriever-ocr" is the earlier name of the Nemotron OCR line.
- Watch the id mismatch: hosted `nvidia/nemotron-parse-2.0` ≠ self-hosted NIM `nvidia/nemotron-parse-v2.0` (container `nvcr.io/nim/nvidia/nemotron-parse-v2.0:latest`, [build page](https://build.nvidia.com/nvidia/nemotron-parse-2.0)) ≠ HF `nvidia/NVIDIA-Nemotron-Parse-2.0`.

---

## 2. Hosted API: Nemotron Parse 2.0 (recommended)

Sources:
- OpenAPI spec embedded in [docs.api.nvidia.com/nim/reference/nvidia-nemotron-parse-2_0-infer](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-parse-2_0-infer)
- [Status polling](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-parse-2_0-statuspolling)
- [HF card and repo files](https://huggingface.co/nvidia/NVIDIA-Nemotron-Parse-2.0)

### 2.1 Endpoint and auth
- `POST https://integrate.api.nvidia.com/v1/chat/completions`
- Headers: `Authorization: Bearer nvapi-...` and `Content-Type: application/json`
- Model: `"nvidia/nemotron-parse-2.0"`
- Async: an HTTP **202** comes with an `NVCF-REQID` header. Poll `GET https://integrate.api.nvidia.com/v1/status/{requestId}` until you get 200.
- Errors: 422 (validation) and 500, returned as NVCF problem+json.
- The server is vLLM. The sample response carries `"system_fingerprint": "vllm-0.24.0-4447e481"`.

### 2.2 Request schema (exact, from the OpenAPI spec)
- `NIMLLMChatCompletionRequest` has **`additionalProperties: false`**. The allowed keys are:
  - `messages` (required, ≥1)
  - `model` (default `nvidia/nemotron-parse-2.0`)
  - `max_tokens` (1–**8192**, default **3500**)
  - `temperature` (0–2, default 0)
  - `top_p` (0–1, default 1)
  - `frequency_penalty` and `presence_penalty` (−2 to 2)
  - `seed`, `stop`, `stream`
- The following are **not accepted**: `tools`, `tool_choice`, `top_k`, `repetition_penalty`, `skip_special_tokens`. You can pass those only when self-hosting with vLLM.
- Message content description, verbatim:
  > Images are passed with `type`=`image_url`; set `image_url.url` to an image URL or a base64 data URI like `data:image/{format};base64,{base64encodedimage}`. The model processes a single document image per request: include at most one `image_url` part.

### 2.3 Prompt (task) control tokens
Send these in the `text` content part:

| Purpose | Prompt |
|---|---|
| HF default: bbox + class + markdown, figures as one block | `</s><s><predict_bbox><predict_classes><output_markdown><predict_no_text_in_pic>` |
| Also OCR the text inside figures (the hosted API example uses this) | `</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>` |
| Layout only (class + bbox, no text) | `</s><s><predict_bbox><predict_classes><output_no_text><predict_no_text_in_pic>` |

- Since v1.2 the 4th token is **required**. Leaving it out "significantly degrades" quality ([v1.2 card](https://huggingface.co/nvidia/NVIDIA-Nemotron-Parse-v1.2)).
- The build.nvidia.com playground shows three tasks: `markdown_bbox`, `markdown_bbox_with_text_in_pictures`, `detection_only`. The mapping of those names to the prompts above is my inference.
- The NIM card's statement that the "default prompt is `</s><output_markdown>`" contradicts the HF card. Use the HF prompt.

### 2.4 Image input: size and resolution
- Recommended resolution is max **1664×2048** (W×H) and min **1024×1280**, RGB.
- Preprocessing, verified in `hf_nemotron_parse_processor.py`:
  - The image is downscaled only if it is larger than 1664×2048, keeping the aspect ratio. It is **never upscaled**.
  - It is then **center-padded with white** to exactly 1664×2048.
- **Recommendation:** render each PDF page so it exactly fits the canvas: `zoom = min(1664/page.rect.width, 2048/page.rect.height)`. For a 7×10 in page that gives 1434×2048 px, about 205 DPI. The server then does no resize, text sizes are what the model was trained on, and bbox un-padding is exact.
- **Payload size limit is UNVERIFIED.**
  - The `ai.api.nvidia.com/v1/cv/...` sample code asserts `len(image_b64) < 180_000` ("use the assets API").
  - The integrate chat-completions samples for nemotron-parse have no such assert.
  - The v1 schema mentions NVCF `asset_id` "when … the payload exceeds 200KB".
  - Plan to send PNG, fall back to JPEG q90 on HTTP 413, and implement NVCF assets only if needed.

### 2.5 Response format
- `choices[0].message.content` is one string. Blocks are separated by `\n\n`, and each has the form `<x_{x0}><y_{y0}>{text}<x_{x1}><y_{y1}><class_{Class}>`.
  - Parse with the HF regex `<x_(\d+(?:\.\d+)?)><y_(\d+(?:\.\d+)?)>(.*?)<x_(\d+(?:\.\d+)?)><y_(\d+(?:\.\d+)?)><class_([^>]+)>` (from [postprocessing.py](https://huggingface.co/nvidia/NVIDIA-Nemotron-Parse-2.0/blob/main/postprocessing.py)).
  - `usage.prompt_tokens` is 6: image tokens are not counted.
- **Coordinates:** 1024 quantized bins per axis, from `<x_0.0>` to `<x_0.999>` (k/1024 rounded). They are **normalized to the padded 1664×2048 canvas**.
  - To convert: `x_img = (x·1664 − pad_left)/w_resized` and `y_img = (y·2048 − pad_top)/h_resized`, where pad = (canvas − resized)//2. This is the same as HF `transform_bbox_to_original`.
  - **Verified:** applying this to the 2.0 sample for `example_1.jpg` (1104×1537) gives Page-header `[0.547, 0.038, 0.930, 0.054]`. The v1 hosted tool output for the same image gives `[0.542, 0.036, 0.933, 0.056]`, already image-normalized. They match.
- **Class labels:** read from the 2.0 `tokenizer.json`. There are 15:
  - `Text, Title, Section-header, List-item, TOC, Bibliography, Footnote, Page-header, Page-footer, Picture, Formula, Code, Table, Caption, Chart`
  - `Chart` is new in 2.0.
  - postprocessing.py maps a legacy `Inline-formula` to `Formula`.
  - There are **no** Definition, Theorem, Box or Margin-note classes.
- **Math:**
  - The v1.1 card says text is markdown and "math expressions as LaTeX, enclosed in \[..\] or \(..\)". Expressions that are only characters plus sub/superscripts stay as markdown. Postprocessing handles `<sup>`/`<sub>` HTML tags, so expect `_v_`, `<sub>` and `<sup>` alongside LaTeX.
  - Display equations come out as `Formula` blocks.
  - The 2.0 card only says the output can be "markdown, LaTeX, HTML, JSON, or plain text". **The exact math delimiter behavior of 2.0 is UNVERIFIED.**
  - Tables come out as LaTeX `tabular`. The HF repo can convert them to HTML, markdown, JSON or CSV.
- **Reading order:** emitted order is reading order. From v1.2 on, "natural reading order for all classes", including footnotes, tables, pictures and captions.
- **Things I observed in NVIDIA's own 2.0 samples:**
  - Source line breaks and end-of-line hyphenation are kept ("op- erations", "syn- chronizes"). The v1 output had them removed.
  - With `<predict_text_in_pic>` the figure in example 2 came out as separate `Text` label blocks with **no `Picture` block**, and "Figure 1. …" was classed `Text`.
  - **So use `<predict_no_text_in_pic>` when you need figure crops** (UNVERIFIED on the hosted API).

### 2.6 Limits, licensing, quality
- **Rate limits:** the build.nvidia.com page data says "Up to 40 rpm" and "10,000 requests per day", and "Rate limits may vary by model". Forum posts confirm the 40 RPM free tier ([forum](https://forums.developer.nvidia.com/t/request-for-nvidia-build-api-rate-limit-increase-40-rpm-200-rpm/377433)). Free credit amounts are UNVERIFIED; third-party guides claim about 1,000.
- **Licensing:**
  - The hosted service is governed by the NVIDIA API Trial Terms of Service, which is trial/dev oriented. Whether it allows production use is UNVERIFIED; read the PDF.
  - The 2.0 weights are [OpenMDW-1.1](https://openmdw.ai/license/1-1/), "ready for commercial or non-commercial use".
  - Self-host via `vllm serve nvidia/NVIDIA-Nemotron-Parse-2.0` (vLLM v0.20–v0.26, Linux + NVIDIA GPU only, **not macOS**). Self-hosting lets you pass `repetition_penalty=1.1, top_k=1, skip_special_tokens=False` and the RepetitionStop logits processor.
- **Model:** 905M parameters (C-RADIO ViT-H encoder + 10-block mBART decoder). Generation default `max_new_tokens` is 9000 on HF; the hosted cap is 8192.
- **Benchmarks, 2.0 vs v1.2:**
  - ParseBench 0.6391 vs 0.5782
  - OmniDocBench Notes handwriting edit distance 0.3395 vs 0.9739
  - IndicVisionBench 0.7203 vs 0.0612
  - MOSCAR 0.9102 vs 0.4410
- **v1.1 on OmniDocBench** ([arXiv 2511.20478](https://arxiv.org/abs/2511.20478); edit distance, lower is better): overall 0.131, text 0.052, **formula 0.288**, table 0.118, order 0.066. For comparison: GPT-4o 0.233, Gemini 2.5 Pro 0.148, MinerU2.0 0.133, dots.ocr 0.125.
- **Throughput (self-hosted):** about 4 pages/s; about 5 pages/s for TC.

### 2.7 Verbatim examples (Nemotron Parse 2.0, hosted)

Request body example (OpenAPI `requestBody.example`):
```json
{"model":"nvidia/nemotron-parse-2.0","messages":[{"role":"user","content":[{"type":"text","text":"</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"},{"type":"image_url","image_url":{"url":"https://assets.ngc.nvidia.com/products/api-catalog/nemoretriever-parse/example_1.jpg"}}]}],"max_tokens":1024,"temperature":0}
```

curl, rendered from the docs page code template with its default values:
```bash
curl https://integrate.api.nvidia.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $NVIDIA_API_KEY" \
  -d '{
  "model": "nvidia/nemotron-parse-2.0",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "https://assets.ngc.nvidia.com/products/api-catalog/nemoretriever-parse/example_1.jpg"
          }
        }
      ]
    }
  ],
  "temperature": 0,
  "top_p": 1,
  "max_tokens": 1024,
  "stream": false
}'
```

Python (openai SDK), rendered from the same template:
```python
from openai import OpenAI

client = OpenAI(
  base_url = "https://integrate.api.nvidia.com/v1",
  api_key = "$NVIDIA_API_KEY"
)

messages = [
  {
    "role": "user",
    "content": [
      {
        "type": "text",
        "text": "</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"
      },
      {
        "type": "image_url",
        "image_url": {
          "url": "https://assets.ngc.nvidia.com/products/api-catalog/nemoretriever-parse/example_1.jpg"
        }
      }
    ]
  }
]

completion = client.chat.completions.create(
  model="nvidia/nemotron-parse-2.0",
  messages=messages,
  temperature=0,
  top_p=1,
  max_tokens=1024,
  stream=False
)

print(completion.choices[0].message.content)
```

Sample response (OpenAPI `x-nvai-meta.examples[0].responseJson`, verbatim):
```json
{
  "id": "chatcmpl-94444bbf3f421c5f",
  "object": "chat.completion",
  "created": 1786657564,
  "model": "nvidia/nemotron-parse-2.0",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "<x_0.5312><y_0.1531>**CUDA C++ Programming Guide, Release 12.8**<x_0.7852><y_0.1648><class_Page-header>\n\n<x_0.2217><y_0.1984>\\begin{tabular}{cc}\n**Thread Scope** & **Description**\\\\cuda::thread\\_scope::thread\\_scope\\_thread & Only the CUDA thread which initiated asynchronous op- erations synchronizes.\\\\cuda::thread\\_scope::thread\\_scope\\_block & All or any CUDA threads within the same thread block as the initiating thread synchronizes.\\\\cuda::thread\\_scope::thread\\_scope\\_device & All or any CUDA threads in the same GPU device as the initiating thread syn- chronizes.\\\\cuda::thread\\_scope::thread\\_scope\\_system & All or any CUDA or CPU threads in the same system as the initiating thread syn- chronizes.\\\\\n\\end{tabular}<x_0.7842><y_0.418><class_Table>\n\n<x_0.2217><y_0.432>These thread scopes are implemented as extensions to standard C++ in the CUDA Standard C++ li- brary.<x_0.7852><y_0.4555><class_Text>\n\n<x_0.2217><y_0.482>## 5.6. Compute Capability<x_0.5039><y_0.5055><class_Section-header>\n\n<x_0.2217><y_0.5242>The _compute capability_ of a device is represented by a version number, also sometimes called its “SM version”. This version number identifies the features supported by the GPU hardware and is used by applications at runtime to determine which hardware features and/or instructions are available on the present GPU.<x_0.7852><y_0.5703><class_Text>\n\n<x_0.2217><y_0.5766>The compute capability comprises a major revision number X and a minor revision number Y and is denoted by X.Y.<x_0.7852><y_0.5992><class_Text>\n\n<x_0.2217><y_0.6062>Devices with the same major revision number are of the same core architecture. The major revision number is 9 for devices based on the _NVIDIA Hopper GPU_ architecture, 8 for devices based on the _NVIDIA Ampere GPU_ architecture, 7 for devices based on the _Volta_ architecture, 6 for devices based on the _Pascal_ architecture, 5 for devices based on the _Maxwell_ architecture, and 3 for devices based on the _Kepler_ architecture.<x_0.7852><y_0.6641><class_Text>\n\n<x_0.2217><y_0.6703>The minor revision number corresponds to an incremental improvement to the core architecture, possibly including new features.<x_0.7852><y_0.693><class_Text>\n\n<x_0.2217><y_0.7>_Turing_ is the architecture for devices of compute capability 7.5, and is an incremental update based on the _Volta_ architecture.<x_0.7852><y_0.7227><class_Text>\n\n<x_0.2217><y_0.7289>_CUDA-Enabled GPUs_ lists of all CUDA-enabled devices along with their compute capability. _Compute Capabilities_ gives the technical specifications of each compute capability.<x_0.7852><y_0.7516><class_Text>\n\n<x_0.2217><y_0.7695>**Note:** The compute capability version of a particular GPU should not be confused with the CUDA version (for example, CUDA 7.5, CUDA 8, CUDA 9), which is the version of the CUDA _software platform_. The CUDA platform is used by application developers to create applications that run on many generations of GPU architectures, including future GPU architectures yet to be invented. While new versions<x_0.7852><y_0.8156><class_Text>\n\n<x_0.2217><y_0.8438>**5.6. Compute Capability**<x_0.3633><y_0.8547><class_Page-footer>\n\n<x_0.7705><y_0.8445>19<x_0.7852><y_0.8539><class_Page-footer>",
        "refusal": null,
        "annotations": null,
        "audio": null,
        "function_call": null,
        "reasoning": null
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": null,
      "token_ids": null,
      "routed_experts": null
    }
  ],
  "service_tier": null,
  "system_fingerprint": "vllm-0.24.0-4447e481",
  "usage": {
    "prompt_tokens": 6,
    "total_tokens": 683,
    "completion_tokens": 677,
    "prompt_tokens_details": null
  },
  "prompt_logprobs": null,
  "prompt_token_ids": null,
  "prompt_text": null,
  "kv_transfer_params": null
}
```

Self-hosted NIM test call (build page "Deploy" tab, verbatim):
```bash
curl -X 'POST' \
'http://0.0.0.0:8000/v1/chat/completions' \
-H 'accept: application/json' \
-H 'Content-Type: application/json' \
-d '{
    "model": "nvidia/nemotron-parse-v2.0",
    "messages": [{"role":"user", "content":[
        {
            "type": "text",
            "text": "</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"
        },
        {
          "type": "image_url",
          "image_url": {"url": "https://assets.ngc.nvidia.com/products/api-catalog/nemotron-parse/example_2.jpg"}
        }
    ]}],
    "max_tokens": 256
}'
```

---

## 3. Hosted API: legacy `nvidia/nemotron-parse` (v1.x, tools mode)

Source: [docs.api.nvidia.com/nim/reference/nvidia-nemotron-parse-infer](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-parse-infer)

- **Request:**
  - Same endpoint.
  - The image goes either as an `image_url` content part, or as a string `<img src="data:image/{fmt};base64,..." />` (PNG, JPG or JPEG).
  - The task is selected through `tools:[{"type":"function","function":{"name": TOOL}}]` (plus an optional matching `tool_choice`).
  - `TOOL` is one of `markdown_bbox`, `markdown_no_bbox`, `detection_only`.
  - The OpenAPI schema itself omits `tools` and sets `additionalProperties:false`, yet NVIDIA's own samples send `tools`. Trust the samples; live behavior UNVERIFIED.
- **Response:**
  - `choices[0].message.tool_calls[0].function.arguments` is a JSON **string**: `[[{"bbox":{"xmin","ymin","xmax","ymax"},"text","type"}]]`.
  - The bboxes are already **normalized to the original image** (verified, §2.5).
  - In both NVIDIA samples, Table, Picture and Caption are emitted **after** the body text. This matches the v1.2 card's note that v1.2 added natural reading order for floating classes, so this endpoint behaves like v1.1. Which minor version is actually served is UNVERIFIED.
  - The v1.1 HF card links `build.nvidia.com/nvidia/nemotron-parse` as its NIM.

Python template (docs page, verbatim):
```python
import os
import sys
import base64
import json
import mimetypes
import requests

nvai_url = "https://integrate.api.nvidia.com/v1/chat/completions"

headers = {
    "Authorization": f"Bearer $API_KEY_REQUIRED_IF_EXECUTING_OUTSIDE_NGC",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

tools = [
    "markdown_bbox",
    "markdown_no_bbox",
    "detection_only",
]

def _read_image_as_base64(path):
    with open(path, "rb") as f:
        data = f.read()
    b64 = base64.b64encode(data).decode("ascii")
    # guess mime from extension; default to jpeg
    mime, _ = mimetypes.guess_type(path)
    if mime is None:
        mime = "image/jpeg"
    return b64, mime

def _generate_content(task_id, b64_str, mime):
    if task_id < 0 or task_id >= len(tools):
        print(f"task_id should be within [0, {len(tools)-1}]")
        sys.exit(1)
    tool_name = tools[task_id]
    media_tag = f'<img src="data:{mime};base64,{b64_str}" />'
    content = f"{media_tag}"
    tool_spec = [{"type": "function", "function": {"name": tool_name}}]
    return content, tool_spec, tool_name

if __name__ == "__main__":
    """
    Usage: python test.py <test_image> <task_id>
    """
    if len(sys.argv) != 3:
        print("Usage: python test.py <test_image> <task_id>"
              "\nFor example: python test.py car.jpg 0")
        sys.exit(1)

    image_path = sys.argv[1]
    task_id = int(sys.argv[2])

    b64_str, mime = _read_image_as_base64(image_path)
    content, tool_spec, tool_name = _generate_content(task_id, b64_str, mime)

    inputs = {
        "model": "nvidia/nemotron-parse",
        "messages": [
            {
                "role": "user", 
                "content": content
            }
        ],
        "tools": tool_spec,
        "tool_choice": {"type": "function", "function": {"name": tool_name}},
        "max_tokens": 8192,
    }

    response = requests.post(nvai_url, headers=headers, json=inputs, timeout=120)
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        print("Status:", response.status_code)
        print("Body:", response.text)
        raise

    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
```

Sample response, `markdown_bbox` on example_1.jpg (verbatim):
```json
{
  "id": "chatcmpl-1074badc476a4030bcfeb234170b91b6",
  "object": "chat.completion",
  "created": 1761100218,
  "model": "nvidia/nemotron-parse",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "refusal": null,
        "annotations": null,
        "audio": null,
        "function_call": null,
        "tool_calls": [
          {
            "id": "chatcmpl-tool-27b642d737d94a4c8d8f159a8344d08a",
            "type": "function",
            "function": {
              "name": "markdown_bbox",
              "arguments": "[[{\"bbox\": {\"xmin\": 0.5422449275362318, \"ymin\": 0.03609420949902409, \"xmax\": 0.9328985507246378, \"ymax\": 0.05581470396877032}, \"text\": \"**CUDA C++ Programming Guide, Release 12.8**\", \"type\": \"Page-header\"}, {\"bbox\": {\"xmin\": 0.07575942028985506, \"ymin\": 0.407718932986337, \"xmax\": 0.9328985507246378, \"ymax\": 0.44196330513988286}, \"text\": \"These thread scopes are implemented as extensions to standard C++ in the CUDA Standard C++ library.\", \"type\": \"Text\"}, {\"bbox\": {\"xmin\": 0.07575942028985506, \"ymin\": 0.4754081977878985, \"xmax\": 0.513136231884058, \"ymax\": 0.508586597267404}, \"text\": \"## 5.6. Compute Capability\", \"type\": \"Section-header\"}, {\"bbox\": {\"xmin\": 0.07575942028985506, \"ymin\": 0.5305722836694861, \"xmax\": 0.9328985507246378, \"ymax\": 0.5950636304489265}, \"text\": \"The _compute capability_ of a device is represented by a version number, also sometimes called its \\\"SM version\\\". This version number identifies the features supported by the GPU hardware and is used by applications at runtime to determine which hardware features and/or instructions are available on the present GPU.\", \"type\": \"Text\"}, {\"bbox\": {\"xmin\": 0.07575942028985506, \"ymin\": 0.600260247234873, \"xmax\": 0.9328985507246378, \"ymax\": 0.6335718932986337}, \"text\": \"The compute capability comprises a major revision number _X_ and a minor revision number _Y_ and is denoted by _X.Y_.\", \"type\": \"Text\"}, {\"bbox\": {\"xmin\": 0.07575942028985506, \"ymin\": 0.6398344827586208, \"xmax\": 0.9328985507246378, \"ymax\": 0.719915679895901}, \"text\": \"Devices with the same major revision number are of the same core architecture. The major revision number is 9 for devices based on the _NVIDIA Hopper GPU_ architecture, 8 for devices based on the _NVIDIA Ampere GPU_ architecture, 7 for devices based on the _Volta_ architecture, 6 for devices based on the _Pascal_ architecture, 5 for devices based on the _Maxwell_ architecture, and 3 for devices based on the _Kepler_ architecture.\", \"type\": \"Text\"}, {\"bbox\": {\"xmin\": 0.07575942028985506, \"ymin\": 0.7251122966818477, \"xmax\": 0.9328985507246378, \"ymax\": 0.7584239427456083}, \"text\": \"The minor revision number corresponds to an incremental improvement to the core architecture, possibly including new features.\", \"type\": \"Text\"}, {\"bbox\": {\"xmin\": 0.07575942028985506, \"ymin\": 0.76375380611581, \"xmax\": 0.9328985507246378, \"ymax\": 0.7979981782693559}, \"text\": \"_Turing_ is the architecture for devices of compute capability 7.5, and is an incremental update based on the _Volta_ architecture.\", \"type\": \"Text\"}, {\"bbox\": {\"xmin\": 0.07575942028985506, \"ymin\": 0.8031947950553024, \"xmax\": 0.9328985507246378, \"ymax\": 0.836506441119063}, \"text\": \"_CUDA-Enabled GPUs_ lists of all CUDA-enabled devices along with their compute capability. _Compute Capabilities_ gives the technical specifications of each compute capability.\", \"type\": \"Text\"}, {\"bbox\": {\"xmin\": 0.07575942028985506, \"ymin\": 0.8574261548471047, \"xmax\": 0.9328985507246378, \"ymax\": 0.9229834743005856}, \"text\": \"**Note:** The compute capability version of a particular GPU should not be confused with the CUDA version (for example, CUDA 7.5, CUDA 8, CUDA 9), which is the version of the CUDA _software platform_. The CUDA platform is used by application developers to create applications that run on many generations of GPU architectures, including future GPU architectures yet to be invented. While new versions\", \"type\": \"Text\"}, {\"bbox\": {\"xmin\": 0.07575942028985506, \"ymin\": 0.956295120364346, \"xmax\": 0.9328985507246378, \"ymax\": 0.974949642160052}, \"text\": \"**5.6. Compute Capability** 19\", \"type\": \"Page-footer\"}, {\"bbox\": {\"xmin\": 0.0802376811594203, \"ymin\": 0.09951958360442419, \"xmax\": 0.9300623188405798, \"ymax\": 0.3910631099544567}, \"text\": \"\\\\begin{tabular}{cc}\\n**Thread Scope** & **Description** \\\\\\\\ cuda::thread_scope::thread_scope_thread & Only the CUDA thread which initiated asynchronous operations synchronizes. \\\\\\\\ cuda::thread_scope::thread_scope_block & All or any CUDA threads within the same thread block as the initiating thread synchronizes. \\\\\\\\ cuda::thread_scope::thread_scope_device & All or any CUDA threads in the same GPU device as the initiating thread synchronizes. \\\\\\\\ cuda::thread_scope::thread_scope_system & All or any CUDA or CPU threads in the same system as the initiating thread synchronizes. \\\\\\\\ \\\\end{tabular}\", \"type\": \"Table\"}]]"
            }
          }
        ],
        "reasoning_content": null
      },
      "logprobs": null,
      "finish_reason": "stop",
      "stop_reason": null
    }
  ],
  "service_tier": null,
  "system_fingerprint": null,
  "usage": {
    "prompt_tokens": 5,
    "total_tokens": 672,
    "completion_tokens": 667,
    "prompt_tokens_details": null
  },
  "prompt_logprobs": null,
  "kv_transfer_params": null
}
```

In the second v1 sample (example_2.jpg), inline math came back as `\\(20\\pm 0.5\\)` and `\\(^\\circ\\)C`, which shows the `\( \)` delimiters.

---

## 4. Other NVIDIA models: endpoints and whether they help here

- **Page elements v3**
  - Endpoint: `POST https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-page-elements-v3`
  - Body: `{"input":[{"type":"image_url","url":"data:image/png;base64,..."}]}`. The sample code asserts base64 < 180,000 chars; use the assets API for larger images.
  - Response: `{"data":[{"index":0,"bounding_boxes":{"table":[{"x_min":…,"y_min":…,"x_max":…,"y_max":…,"confidence":…}]}}]}`
  - Input is resized to 1024×1024.
  - No Picture or Formula classes, so it is useless for figures and equations.
  - A deprecation date of 05/18/2026 appeared in one search summary but is UNVERIFIED; the page is still live.
- **OCR v2**
  - Endpoint: `POST https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v2`, same body.
  - Response: `{"data":[{"index":0,"text_detections":[{"text_prediction":{"text","confidence"},"bounding_box":{"points":[{x,y}×4]}}]}]}`
  - English and multilingual variants; 34.7–40.7 pages/s on A100.
  - No LaTeX, so it is wrong for math.
- **Nemotron Nano 12B v2 VL** (`nvidia/nemotron-nano-12b-v2-vl`) and **Nemotron 3 Nano Omni** (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`)
  - Both are general VLMs on integrate chat completions, with the same OpenAI-style `image_url` content.
  - They could stand in for GPT in the cleanup step if you want everything on NVIDIA. Their LaTeX and semantic-labeling quality on textbook math is UNVERIFIED; expect them to be weaker than a frontier GPT.
- NeMo Retriever already wires the hosted Nemotron Parse as `NEMOTRON_PARSE_HTTP_ENDPOINT=https://integrate.api.nvidia.com/v1/chat/completions` with `extract_method="nemotron_parse"` ([docs](https://docs.nvidia.com/nemo/retriever/26.3.0/extraction/nemoretriever-parse/)). That is useful prior art, but too heavy a dependency for this app.

---

## 5. Fit assessment: math textbook page (Axler-style)

| Page feature | Nemotron Parse 2.0 | OpenAI vision model (GPT) | PyMuPDF (born-digital PDF) |
|---|---|---|---|
| Bounding boxes and reading order | **Strong.** Native, 1/1024 granularity, reading order since v1.2 | **Weak.** OpenAI's docs say the model "struggles with tasks requiring precise spatial localization" ([guide](https://developers.openai.com/api/docs/guides/images-vision)) | Exact positions for text and drawings; no semantic order |
| Prose text | Good (v1.1 OmniDocBench text edit distance 0.052). 2.0 keeps hyphenation and line breaks | Good, but may paraphrase or "fix" text | **Exact** (text layer) |
| Inline and display math to LaTeX | First pass. Formula class; v1.1 formula edit distance 0.288 (better than GPT-4o-era pipelines). Simple math may come out as markdown italics or `<sub>`; dense `align`, matrices and `cases` are a risk for a <1B decoder; 2.0 delimiters UNVERIFIED | **Strongest** at transcribing and repairing LaTeX | Lossy: CM/AMS glyphs with no structure |
| Colored definition/theorem boxes | No box class; contents come out as Text/Section-header (could be misread as Picture or Table, UNVERIFIED) | Understands them semantically ("Definition 1.2") but gives no geometry | **Exact.** `get_drawings()` gives filled rectangles with their fill color |
| Vector line-art figures with labels | `Picture` bbox with `<predict_no_text_in_pic>`. With `text_in_pic` the figure can split into Text fragments (seen in NVIDIA's sample) | Can describe figures and read labels; no crop box | **Exact.** `cluster_drawings()` regions; crop with `get_pixmap(clip=…, dpi=300)` |
| Margin notes | No class; likely Text or Footnote, placement in the order UNVERIFIED | Can recognize them semantically | x-position outside the body column |
| Determinism, cost, rate | temperature 0, free trial, 40 RPM, ≤8192 output tokens | Paid per page; not deterministic | Free, local |

**Conclusion**

Neither model alone covers the job:
- **Nemotron alone** has no semantic types (Definition, Theorem, Proof, Example), its math quality on textbooks is unproven, and the hosted API has no repetition guard (`repetition_penalty` is rejected by the schema).
- **GPT alone** cannot give trustworthy bboxes. You need those for figure crops, for "show me in the book" highlights, and for keeping boxed content together. GPT also risks silently paraphrasing.

**Do it in this order:** take geometry from PyMuPDF and Nemotron, then have GPT produce the semantics and LaTeX repair, constrained to Nemotron's block IDs.

---

## 6. Recommended hybrid pipeline (per chapter)

1. **Render and get vector truth with PyMuPDF.** Use `import pymupdf`; `import fitz` is deprecated as of 1.28.
   - For each page, render with `zoom=min(1664/W, 2048/H)` and produce a PNG.
   - Collect `page.get_text("dict")` spans. Span fonts like CMMI/CMSY flag math.
   - From `page.get_drawings()`, keep filled non-white rectangles wider than about 100 pt. These are candidate def/thm boxes. The fill color maps to a box kind, which you calibrate once per book.
   - Use `page.cluster_drawings()` for vector figure regions and `page.get_images()` for raster figures.
2. **Run Nemotron Parse 2.0 once per page.**
   - Prompt: `…<output_markdown><predict_no_text_in_pic>`, `max_tokens=8192`.
   - Throttle to about 30 RPM with 2–4 requests in flight. A 40-page chapter takes about 1.5 minutes and is free.
   - Cache the raw string in SQLite, keyed by `sha256(png)+model+prompt`.
3. **Merge deterministically.**
   - Map bboxes to PDF points (`bbox × page.rect`) and give each block an id like `p{page}_b{i}`.
   - If at least 80% of a block's area lies inside a colored rectangle, tag it with that `box_id`.
   - Flag `margin=True` when the block center lies outside the body column (the body column is the median x-span of Text blocks).
   - Figures = Nemotron `Picture` ∪ drawing clusters that have few text spans. Crop each at 300 DPI as `fig_{page}_{n}.png`.
   - Replace prose text with PyMuPDF text taken from the same bbox, only for blocks without math. This removes OCR and hyphenation artifacts.
4. **GPT semantic pass** (per page, or a 2–3 page window so boxes that cross a page break merge).
   - Input:
     - the page image
     - block JSON with `{id, type, bbox, box_id, margin, text}`
     - the figure ids
   - Structured Output:
     ```
     segments[{kind ∈ definition|theorem|lemma|proposition|corollary|proof|example|exercise|remark|notation|prose|figure|margin_note,
               number, title, block_ids[], body_md_latex, figure_ids[], continues_prev}]
     ```
   - Rules for the prompt:
     - Transcribe, don't paraphrase.
     - Normalize all math to `\( \)` / `\[ \]`.
     - Every block_id must appear exactly once. Check this in code.
5. **Validate LaTeX.**
   - Run every math span through KaTeX (Node sidecar or the `katex` CLI).
   - On failure, send only that block's 300-DPI crop to GPT and ask for re-transcription. Crop-level calls are cheaper and more accurate.
6. **Downstream.** Knowledge-graph and micro-skill extraction runs on the clean `segments`. Each item carries `(page, bbox)` provenance, so the React side can draw source highlights on the rendered page.
7. **Fallbacks.**
   - No NVIDIA key or rate-limited: use GPT with the page image plus PyMuPDF clusters.
   - Nemotron output truncated (`finish_reason=="length"`): split the page horizontally at a gap between blocks and re-run each half.

---

## 7. Code

### `parse_page(png_bytes)` for hosted Nemotron Parse 2.0 (httpx)

Tested offline against NVIDIA's published sample responses using a mocked transport, including the 202 → status-polling path. Not run live: no key was used.

```python
"""Nemotron Parse 2.0 (hosted, build.nvidia.com) page parser.

parse_page(png_bytes) -> list[{"type", "bbox", "text"}]
  bbox = [x0, y0, x1, y1] normalized to the *input image* (0..1, origin top-left),
  so multiply by page.rect.width/height to get PDF points.
"""
from __future__ import annotations

import base64
import io
import os
import re
import time
import warnings

import httpx
from PIL import Image

NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_STATUS_URL = "https://integrate.api.nvidia.com/v1/status/{req_id}"
MODEL = "nvidia/nemotron-parse-2.0"
# HF-recommended default for 2.0 (figures come back as one Picture block, no inner labels).
PROMPT = "</s><s><predict_bbox><predict_classes><output_markdown><predict_no_text_in_pic>"
CANVAS_W, CANVAS_H = 1664, 2048  # 2.0 processor: resize-to-fit then white center-pad to 1664x2048

_BLOCK_RE = re.compile(
    r"<x_(\d+(?:\.\d+)?)><y_(\d+(?:\.\d+)?)>(.*?)<x_(\d+(?:\.\d+)?)><y_(\d+(?:\.\d+)?)><class_([^>]+)>",
    re.DOTALL,
)


def _fit_to_canvas(png_bytes: bytes, fmt: str = "PNG") -> tuple[bytes, str, int, int]:
    """Scale so the page exactly fits 1664x2048 (aspect kept). Server-side resize is then a
    no-op, small pages land in the model's trained text-size regime, and the bbox
    un-padding below is exact."""
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    s = min(CANVAS_W / im.width, CANVAS_H / im.height)
    w, h = min(CANVAS_W, round(im.width * s)), min(CANVAS_H, round(im.height * s))
    if (w, h) != im.size:
        im = im.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt == "JPEG":
        im.save(buf, "JPEG", quality=90, optimize=True)
        mime = "image/jpeg"
    else:
        im.save(buf, "PNG", optimize=True)
        mime = "image/png"
    return buf.getvalue(), mime, w, h


def _canvas_to_image(b, w: int, h: int) -> list[float]:
    """Model coords are normalized to the padded 1664x2048 canvas -> normalize to the image."""
    pad_l, pad_t = (CANVAS_W - w) // 2, (CANVAS_H - h) // 2
    x0 = (b[0] * CANVAS_W - pad_l) / w
    y0 = (b[1] * CANVAS_H - pad_t) / h
    x1 = (b[2] * CANVAS_W - pad_l) / w
    y1 = (b[3] * CANVAS_H - pad_t) / h
    return [round(min(1.0, max(0.0, v)), 5) for v in (x0, y0, x1, y1)]


def parse_raw(content: str, w: int, h: int) -> list[dict]:
    out = []
    for m in _BLOCK_RE.finditer(content):
        x0, y0, text, x1, y1, cls = m.groups()
        cls = "Formula" if cls == "Inline-formula" else cls  # legacy alias (HF postprocessing.py)
        text = text.replace("<tbc>", "").replace("\\<|unk|\\>", "").strip()
        out.append({
            "type": cls,  # Text|Title|Section-header|List-item|TOC|Bibliography|Footnote|Page-header|
                          # Page-footer|Picture|Formula|Code|Table|Caption|Chart
            "bbox": _canvas_to_image((float(x0), float(y0), float(x1), float(y1)), w, h),
            "text": text,  # markdown; math as \( \) / \[ \]; tables as LaTeX tabular
        })
    return out  # emitted order == model's predicted reading order


def parse_page(
    png_bytes: bytes,
    *,
    api_key: str | None = None,
    prompt: str = PROMPT,
    max_tokens: int = 8192,  # hosted schema max is 8192 (default 3500 is too small for dense pages)
    timeout: float = 180.0,
    max_retries: int = 5,
) -> list[dict]:
    api_key = api_key or os.environ["NVIDIA_API_KEY"]  # "nvapi-..."
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    fmt = "PNG"
    with httpx.Client(timeout=timeout) as client:
        for attempt in range(max_retries):
            img, mime, w, h = _fit_to_canvas(png_bytes, fmt)
            payload = {
                "model": MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:{mime};base64,{base64.b64encode(img).decode()}"}},
                    ],
                }],
                "max_tokens": max_tokens,
                "temperature": 0,
            }
            r = client.post(NVIDIA_URL, headers=headers, json=payload)
            deadline = time.monotonic() + timeout
            while r.status_code == 202 and time.monotonic() < deadline:  # NVCF async: poll NVCF-REQID
                time.sleep(1.0)
                r = client.get(NVIDIA_STATUS_URL.format(req_id=r.headers["NVCF-REQID"]), headers=headers)
            if r.status_code == 413 and fmt == "PNG":  # payload too large -> smaller JPEG
                fmt = "JPEG"
                continue
            if r.status_code in (429, 500, 502, 503, 504):  # free tier: ~40 RPM
                time.sleep(min(60.0, 2.0 ** attempt + 1))
                continue
            r.raise_for_status()
            choice = r.json()["choices"][0]
            if choice.get("finish_reason") == "length":  # truncated; complete blocks still parse
                warnings.warn("Nemotron Parse output truncated (max_tokens); consider splitting the page")
            return parse_raw(choice["message"]["content"] or "", w, h)
    raise RuntimeError(f"Nemotron Parse failed after {max_retries} attempts: {r.status_code} {r.text[:300]}")
```

### PyMuPDF helpers (tested with PyMuPDF 1.28)
```python
import pymupdf

def render_for_nemotron(page: pymupdf.Page) -> bytes:
    z = min(1664 / page.rect.width, 2048 / page.rect.height)          # 7x10in page -> 1434x2048
    return page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False).tobytes("png")

def colored_boxes(page):  # definition/theorem callouts in born-digital PDFs
    return [(d["rect"], d["fill"]) for d in page.get_drawings()
            if d.get("fill") and d["fill"] != (1.0, 1.0, 1.0) and d["rect"].width > 100]

def to_pdf_rect(page, bbox):  # parse_page bbox (0..1) -> PDF points
    W, H = page.rect.width, page.rect.height
    return pymupdf.Rect(bbox[0] * W, bbox[1] * H, bbox[2] * W, bbox[3] * H)

def crop_png(page, rect, dpi=300) -> bytes:  # figure assets / GPT re-transcription crops
    return page.get_pixmap(clip=rect, dpi=dpi).tobytes("png")

# figure candidates = Nemotron "Picture" blocks ∪ page.cluster_drawings() regions
```

---

## 8. UNVERIFIED items to settle in week 1 (5–10 Axler pages, about 1 hour with a key)

1. Do math delimiters and inline-math handling in **2.0** match the v1.1 card (`\( \)`/`\[ \]`), and how often does simple math come out as markdown italics or `<sub>`?
2. With `<predict_no_text_in_pic>`, do vector figures reliably yield a single `Picture` block on the hosted API?
3. How are colored theorem boxes and margin notes classified and placed in the reading order?
4. Inline base64 size limit on integrate chat completions: does a 300–600 KB PNG pass, or is NVCF assets needed above about 200 KB?
5. Does the hosted v1 endpoint still accept `tools`, and which minor version does it serve?
6. Do the API Trial Terms permit a personal app's ongoing use, and what is the current free-credit policy beyond 40 RPM / 10k per day?
7. Latency per page on the hosted 2.0 endpoint (not measured).

Artifacts (scratchpad only, outside the project): `<scratchpad>/np/nemotron_parse.py` (module above) and `<scratchpad>/np/test_np.py` (offline test against the published samples).