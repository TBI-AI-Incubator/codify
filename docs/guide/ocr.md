---
title: Read scanned pages (OCR)
description: Turn PDF pages into text, see which engine read each page and why, and find every page that could not be read.
sidebar:
  order: 4
---

You end with the text of every page of the worked example, a record of how each page was read, and a way to find any page that was not.

## Before you start

- The worked example, `data/fixtures/synthetic/xa/legislation-act-1992.pdf`: a five-page scan with no text layer.
- Poppler on your `PATH`, which renders PDF pages to images ([Install](../start/install.md)).
- A chat model that accepts images, behind an OpenAI-compatible endpoint, named by `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` ([Models and costs](../operate/models-and-costs.md)). The first step needs none; without `LLM_BASE_URL`, `ingest-one` stops with an error that names it.

A `.txt` or `.md` source skips this stage and goes to [Structure](structure.md).

## The idea in brief

Codify has no OCR engine of its own. A page's text comes from the text layer (text a PDF stores beside the page image) or from a model that transcribes the page image: a chat model, or the optional [second engine](#choose-the-engines). The layer is exact, so it wins unless there is evidence against it.

A law missing a page is still well-formed XML and the run can still succeed, so the pipeline records how and why each page was read and marks any page it could not read; the validator, the checks on the finished [Akoma Ntoso](../concepts/akoma-ntoso.md) (AKN) XML, then grades the document as blocking.

## Steps

### See what the text layer holds

Run the extraction with no model client, so nothing is sent:

```python
import asyncio
from pathlib import Path

from codify.pipeline.enrich.ocr import extract_text_from_pdf

pdf = Path("data/fixtures/synthetic/xa/legislation-act-1992.pdf").read_bytes()
pages = asyncio.run(extract_text_from_pdf(pdf, client=None, country="xa"))
for page in pages:
    print(page.page_number, page.method, repr(page.divert_reason), len(page.text))
```

```text
2026-11-14 00:20:59 [info     ] page_diverted_to_ocr           page=1 reason=too_short split_words=None total=5
...
2026-11-14 00:20:59 [warning  ] ocr_unavailable_kept_raw_text  page=1
...
1 text_extraction 'too_short' 0
2 text_extraction 'too_short' 0
3 text_extraction 'too_short' 0
4 text_extraction 'too_short' 0
5 text_extraction 'too_short' 0
```

Every page was diverted as `too_short`. With no client, each keeps its raw text, here none.

### Run the PDF lane

`ingest-one` runs the whole pipeline and writes a *bundle*, a directory of every stage's output. `--model` defaults to `LLM_MODEL`, else `gemini-3.7-flash`; this run used `gemini/gemini-3.7-flash`, as its manifest records:

```bash
uv run codify ingest-one data/fixtures/synthetic/xa/legislation-act-1992.pdf --jurisdiction xa --out bundle/
```

The log, on stderr:

```text
2026-11-14 00:15:03 [error    ] layout_pass_failed             error='azure_foundry_ocr not configured; set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY' error_type=OcrNotConfigured model=mistral-ocr-4-0 pages=5
2026-11-14 00:15:03 [info     ] page_diverted_to_ocr           page=1 reason=too_short split_words=None total=5
...
2026-11-14 00:15:05 [info     ] page_vision_ocr_start          anchor_chars=0 page=1 total=5
...
2026-11-14 00:15:19 [info     ] page_vision_ocr_done           chars=2237 finish=stop page=1
...
```

The first line is expected with no second engine.

![How Codify reads a PDF, in six stages, with the worked example's path. Dashed stages need the second engine. 1, whole-document engine (Mistral OCR in 25-page chunks): not asked for, ocr_model null; when it reads every page, the read ends there and stages 2 to 5 are skipped. 2, layout pass for regions and a rival read: failed with layout_pass_failed, no second engine. 3, text-layer gate, which keeps a sound layer: 5 of 5 pages diverted as too_short, 0 kept. 4, vision read, one page image per chat-model request: 5 pages read, 965 to 2,704 characters each. 5, backstop, which runs on every page and gives a page with an empty read the rival read: not needed, no empty read. 6, combine, page texts joined in order: source.txt, 10,990 characters. A kept text layer skips stages 4 and 5. Stages 1, 2 and 4 call a model.](../figures/v18-ocr-read.svg "The six read stages in order, with the worked example's real outcome beside each: every page failed the gate as too_short and went to the vision read.")

<details>
<summary>The read, step by step, and every divert reason</summary>

1. **Whole-document engine.** Used when `--ocr-model` starts `mistral-ocr` or `mistral-document` and the second engine is configured. If every page comes back, the read ends.
2. **Layout pass.** When configured, the second engine also reads every page's running headers and footers; its transcription is the *rival read*.
3. **Text-layer gate.** A page's layer is kept unless a reason in the table below fires.
4. **Vision read.** Diverted pages render at 300 dpi in windows of up to eight pages, read in parallel. A window over 70 million pixels gets a lower resolution and logs `render_dpi_clamped`; an image over 4,096 pixels on a side is scaled down. The model is told to transcribe verbatim, keep reading order across columns, write tables as rows, keep marginal notes beside their section, and drop running headers and footers. A reply under 200 characters with a stock blank-page phrase, such as "this page is blank", is emptied.
5. **Backstop.** The rival read is taken only if the page image is not blank.
6. **Combine.** Joining drops control characters, Markdown heading marks, lines of bare marker characters, LaTeX superscripts, and the furniture your config declares in `furniture_inline_patterns` (mastheads, stamps and other print that is not law).

| `divert_reason` | Fires when | Needs the second engine |
| --- | --- | --- |
| `too_short` | Fewer than 50 characters remain after dropping lines matching `furniture_line_patterns` | No |
| `garbled` | An Arabic-script layer shows the letter statistics of a broken font map | No |
| `presentation_forms` | More than 30% of the characters are Arabic presentation forms | No |
| `text_layer_divergent` | Layer and rival read share too few words (divergence above 0.5) | Yes |
| `letter_spaced` | The layer spells a word as spaced letters and the rival keeps it whole (Latin script) | Yes |
| `visible_annotation` | The page has a visible free-text note, form field or stamp, which a layer omits | No |

</details>

### Read the page evidence

`events.jsonl` holds one `page_extracted` event per page:

```json
{"kind":"page_extracted","page":1,"method":"ocr","text_len":2237,"divert_reason":"too_short","finish_reason":"stop","unreadable":"","skipped":""}
{"kind":"page_extracted","page":2,"method":"ocr","text_len":2552,"divert_reason":"too_short","finish_reason":"stop","unreadable":"","skipped":""}
...
```

`manifest.json` summarises:

```json
  ...
  "ocr_model": null,
  "fallback_model": null,
  "elapsed_seconds": 70.9,
  "pages": 5,
  "pages_diverted_to_ocr": {
    "1": "too_short",
    "2": "too_short",
    "3": "too_short",
    "4": "too_short",
    "5": "too_short"
  },
  "pages_read_once": 5,
  "pages_unreadable": {},
  ...
```

<details>
<summary>Where each piece of page evidence lives</summary>

| Where | What it tells you |
| --- | --- |
| `pages_diverted_to_ocr` | Pages whose text layer was not used, and why |
| `pages_read_once` | Pages only one engine read, so rival-read checks could not run |
| `pages_unreadable` | Pages with missing content, and why: the refusing finish reason, or `empty_read` |
| `page_spans.json` | Per page, the engine (`text_extraction`, `vision_ocr` or `mistral_ocr`) and its offsets in `source.txt` |
| `source.txt` | The combined text |
| `pages/page-NNN.png` | Each page at 200 dpi, to compare by eye |

</details>

The [bundle reference](../reference/bundle.md) covers every file.

### Score each read

Deterministic signals judge a page read without a model. No command runs them; call `score_page` on a bundle:

```python
import json
import sys
from pathlib import Path

from codify.jurisdictions import load_config
from codify.pipeline.enrich.scripts import pack_for_config
from codify.quality.page_read import ink_ratio, score_page

bundle = Path(sys.argv[1])
text = (bundle / "source.txt").read_bytes().decode("utf-8")
spans = json.loads((bundle / "page_spans.json").read_text())["pages"]
pack = pack_for_config(load_config("xa"))
for span in spans:
    image = (bundle / "pages" / f"page-{span['page']:03d}.png").read_bytes()
    verdict = score_page(text[span["start"] : span["end"]], ink=ink_ratio(image), pack=pack)
    signals = {k: round(v, 3) for k, v in verdict.signals.items() if v is not None}
    print(span["page"], verdict.verdict, verdict.reasons, signals)
```

Saved as `score_pages.py`, `uv run python score_pages.py bundle` prints:

```text
1 accept [] {'chars_per_ink': 52967.429, 'repeat_density': 0.0, 'presentation_form_share': 0.0}
2 accept [] {'chars_per_ink': 53958.145, 'repeat_density': 0.0, 'presentation_form_share': 0.0}
3 accept [] {'chars_per_ink': 54706.478, 'repeat_density': 0.0, 'presentation_form_share': 0.0}
4 accept [] {'chars_per_ink': 53260.125, 'repeat_density': 0.011, 'presentation_form_share': 0.0}
5 accept [] {'repeat_density': 0.013, 'presentation_form_share': 0.0}
```

The verdict is `accept`, `reread` or `escalate`. Page 5 has no `chars_per_ink` because under 2% of its image is dark; see the details below.

<details>
<summary>Signals, thresholds and the blank-page floor</summary>

| Signal | Flags | Verdict |
| --- | --- | --- |
| `chars_per_ink` below 5,000 | Too little text for the ink: a summary, not a transcription | `escalate` |
| `repeat_density` above 0.5 | The model looped on one clause | `escalate` |
| `presentation_form_share` above 0.3 | Arabic returned as display glyphs | `escalate` |
| `script_purity` below 0.85 | The read drifted into another script. Needs a script pack (in `codify/pipeline/enrich/scripts/`) for the config's `display.script`. | `escalate` |
| `divergence` above 0.7 | The engines disagree. Needs a rival read. | `reread` |
| `numeral_parity` below 0.8 | One read dropped numbers the other kept. Needs a rival read. | `reread` |

A page with nothing measurable is `reread`, reason `unmeasured`. The source marks the thresholds provisional. Below the 2% dark-pixel floor density is not judged, and the same floor separates a failed read from a blank page: page 5 would have been dropped as blank had its read come back empty ([What can go wrong](#what-can-go-wrong)).

</details>

### Choose the engines

| Setting | Effect |
| --- | --- |
| `--model`, `LLM_MODEL` | The model for vision reads, unless `--ocr-model` names another chat model |
| `--ocr-model`, `LLM_OCR_MODEL` | Another chat model for the page reads, or a `mistral-ocr` model for the whole-document engine |
| `--fallback-model`, `LLM_CONTENT_FILTER_FALLBACK_MODEL` | A model that retries any read the first model's content filter refused |
| `AZURE_OPENAI_ENDPOINT` with `AZURE_OPENAI_API_KEY` | Turns on the second engine, Mistral OCR on Azure AI Foundry, called at the endpoint's host |
| `AZURE_FOUNDRY_OCR_RPM` | Requests per minute to the second engine; default 40 |

Only a second engine catches a text layer that looks sound and is wrong, since that takes two independent reads, and it rescues pages the chat model refused. Without it, `pages_read_once` equals the page count.

<details>
<summary>Skip a trailing section you never ingest</summary>

Declare its heading in `segmentation.trailing_section_patterns`. Pages from it onward go to no model: they keep their text-layer text unchecked, still enter `source.txt` and structuring, and are marked `skipped: trailing_section`. The heading must stand alone on a line past the first quarter of the document, and is sought in the text layer only, so this does not work on a scan.

</details>

## Check your result

- `pages_unreadable` in the manifest is `{}`, and `pages.ratio` in `coverage.json` is `1.0`.
- `source.txt` holds no unreadable-page marker. This prints `0`:

  ```bash
  grep -c "unreadable⟧" bundle/source.txt
  ```

- `page_count` in `page_spans.json` equals the number of `pages` entries; a page with no entry gave no text; look at its image.
- Compare `pages/page-001.png` with the start of `source.txt`:

  ```text
  THE ATLANTIS GAZETTE

  No. 412 Published by Authority 14th May, 1992

  No. 7 of 1992

  LEGISLATION ACT, 1992
  ...
  ```

Once structured, the same page reads against `final.akn.xml` like this:

![Page 1 of the worked example's scan, pages/page-001.png, cropped from the masthead to subsection (1) of section 1, beside an abridged extract of final.akn.xml. Six numbered markers pair a printed line with its element. 1: the masthead, THE ATLANTIS GAZETTE, is a p in preface. 2: the line No. 7 of 1992 gives the work URI /akn/xa/act/1992/7, in FRBRuri under meta. 3: the recital ending in the enacting words, WHEREAS to AS FOLLOWS, is another p in preface. The formula in preamble, refersTo #codify, has no marker: Codify inserted it from the jurisdiction config. 4: the heading PART I, PRELIMINARY, is part with eId part_I and heading PRELIMINARY. 5: section 1 is section with eId part_I__sec_1 and num 1. 6: subsection (1) is point with eId part_I__sec_1__point_1.](../figures/v2-page-to-akn.svg "Each numbered marker pairs a line printed on page 1 with the element it became, from the worked example's real run.")

## What can go wrong

| What you see | Cause | What to do |
| --- | --- | --- |
| `layout_pass_failed ... azure_foundry_ocr not configured`, an error on every PDF | No second engine; the run continues with one read per page | Nothing, or configure the second engine |
| `"failed": {"stage": "extract", "error": "PDFInfoNotInstalledError: Unable to get page count. Is poppler installed and in PATH?"}` | Poppler is missing | Install Poppler |
| `pages_unreadable` gives a reason starting `content_filter`; `source.txt` holds `⟦page N unreadable⟧`; the AKN holds `[Page N of the source could not be read]`; the validator reports `unreadable_page` at severity `error` | The provider's content filter refused the page. The command still exits 0; read the manifest's grade. | Set `--fallback-model` to another provider's model, or configure the second engine |
| `pages_unreadable` gives `empty_read` | A page with ink on it came back empty | Run again and compare the page image |
| The run fails at stage `extract` with a provider error | A page read failed three attempts at rate limits, timeouts and gateway errors (the HTTP client may retry within each). One failed page fails the run, keeping no read. | Run again; every page is read again. |
| `mistral_ocr_failed_falling_back_to_vision` in the log | The whole-document engine failed and the chat model read the pages; the manifest's `ocr_model` still names the engine you asked for. | Read `method` in `page_spans.json` |
| A lightly printed page is missing, with no marker and no validator finding | A page under 2% dark pixels that reads empty is dropped as blank; only the warning `ocr_page_low_text`, naming the pages, records it. | Search the log for `ocr_page_low_text` and compare `page_count` with the spans |

## Status and limits

| Capability | Status |
| --- | --- |
| Read PDF pages with a vision model (`ingest-one`, upload to `codify serve`) | **Available** |
| Per-page evidence in a bundle (`ingest-one`) | **Available** |
| Second engine: layout, rival read and divergence checks (`ingest-one` only) | **Available** |
| Fallback model after a content-filter refusal (`ingest-one` only) | **Available** |
| Skip a trailing section (`segmentation.trailing_section_patterns`) | **Available** |
| Page-read scoring (`score_page`, `page_verdict`) | **Library only** |
| Render resolution and divergence threshold (`ocr_dpi`, `text_layer_max_divergence` on `extract_text_from_pdf`) | **Library only** |
| Stored page reads and disputes (the `page_reads` tables); no command writes them | **Library only** |
| A local or offline OCR engine | **Not in the core** |
| Re-reading a single page of a finished run | **Not in the core** |
| Second engine or fallback model under `codify serve` (its client uses `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` only, so a refused page stays unreadable) | **Not in the core** |

## Next

[Structure](structure.md)
