---
title: Acquire sources
description: Collect the official source files for your corpus, record where each one came from, and sort the files with a text layer from the scans.
sidebar:
  order: 3
---

You end with a directory of official source files, a provenance record for each, and a count of text-layer files against scans that need optical character recognition (OCR).

## Before you start

- A source checkout with the environment synced ([Install](../start/install.md)).
- A jurisdiction config for your legal system ([Write a jurisdiction config](write-a-jurisdiction-config.md)). The examples use the synthetic jurisdiction `xa`.
- The right to copy the sources. Read the publisher's licence and terms before you fetch in bulk.

## The idea in brief

A digitised law is only as trustworthy as its link to the published text. Codify names each law as a *work* with an address such as `/akn/xa/act/1992/7`, and a gazette PDF is one file of one *expression* of it, its text in one language at one date ([FRBR](../concepts/frbr.md)). Keep the bytes you received unchanged, with a hash and their address, so every later form traces to one official source and can be derived again.

Take the most structured source on offer. [Akoma Ntoso](../concepts/akoma-ntoso.md) XML, the open standard Codify produces, converts without a model. A PDF with a text layer (text stored beside the page image) needs a model to find the structure; a scan needs a model to read every page first, and each read can fail.

## Choose a route

| What you hold or can reach | Route | Model needed later |
| --- | --- | --- |
| PDF, `.txt` or `.md` files on disk | No acquisition code | Yes |
| Akoma Ntoso XML files on disk | `codify load`, or upload to `codify serve`; not `ingest-one` | No |
| A publisher that serves Akoma Ntoso over HTTP at a predictable address | The `akn_native` adapter | No |
| An EU act on EUR-Lex or the Publications Office | `POST /runs/ingest-url` under `eu`, or the web app's URL field ([Serve and publish](serve-and-publish.md)) | No |
| Any other website or API | Write an acquirer | Depends on the format |

<details><summary>How the XML routes differ</summary>

`codify load` stores a file as it is. An upload to `codify serve` rewrites its addresses to Codify's form and fills in missing element ids. `ingest-one` treats any file not `.txt` or `.md` as a PDF, so it cannot take XML.

</details>

## Steps

### Lay out the corpus directory

Keep one file per publication, as issued, unedited. The worked example:

```bash
ls data/fixtures/synthetic/xa
```

```text
freedom-of-information-act-2001.pdf
legislation-act-1992.pdf
legislation-amendment-act-2011.pdf
legislation-hcontainer.akn.xml
```

Put the Gregorian year in each file name, apart from any other number, and do not end a name with `.ar`, `.en` or `.he` before the extension.

<details><summary>Why file names matter to <code>scan-corpus</code></summary>

- It takes the last run of four digits from 1800 to 2099 as the year, even inside a longer number (`No18123` gives 1812), to apply the config's date-bounded rules and legal eras.
- It skips a name ending `.ar`, `.en` or `.he` as a derived export unless you pass `--include-derived`.

</details>

### Record provenance

Record a hash and the PDF's own metadata for every file:

```python
import hashlib
from pathlib import Path

from codify.acquisition.pdf_metadata import extract_pdf_metadata

for path in sorted(Path("data/fixtures/synthetic/xa").glob("*.pdf")):
    data = path.read_bytes()
    info = extract_pdf_metadata(data)
    digest = hashlib.sha256(data).hexdigest()
    print(path.name, info.get("page_count"), info.get("pdf_producer"), digest[:16])
```

```text
freedom-of-information-act-2001.pdf 1 None 59c979a6ae22f88a
legislation-act-1992.pdf 5 None f7deab7277ef29d6
legislation-amendment-act-2011.pdf 1 None 9203bbb91a7b1e1c
```

Store them beside the corpus with each file's source address and the date you took it. It also returns `pdf_title`, `pdf_author` and `pdf_creation_date`; for a file it cannot open it logs `pdf_metadata_read_failed` and returns `{}`.

### Sort text-layer files from scans

`scan-corpus` reads every `.txt` and `.pdf` under a directory, with no model or database:

```bash
uv run codify scan-corpus data/fixtures/synthetic/xa --jurisdiction xa
```

```text
no scannable sources under data/fixtures/synthetic/xa (unreadable 0, no text layer 3, derived exports 0)
```

All three PDFs are scans: the line goes to stderr, stdout is empty and the exit code is 1. Continue to [OCR](ocr.md).

| Count | Meaning | Action |
| --- | --- | --- |
| `no text layer` | The PDF yields fewer than 200 characters | A scan: send it through OCR |
| `unreadable` | The PDF parser rejected the file, or a text file is not UTF-8 | Obtain the file again or convert it |
| `derived exports` | The name ends `.ar`, `.en` or `.he` | Rename it, or pass `--include-derived` |

<details><summary>Output when text sources are present</summary>

It prints a JSON summary on stdout, opening with the same counts:

```bash
uv run codify scan-corpus tests/fixtures/corpus --jurisdiction xz
```

```json
{
  "corpus": {
    "jurisdiction": "xz",
    "root": "tests/fixtures/corpus",
    "include_derived": false
  },
  "documents": 5,
  "documents_excluded_as_our_own_export": 0,
  "documents_unreadable": 0,
  "documents_without_text_layer": 0,
  ...
```

`text_verdicts` separates readable prose from text whose letters did not survive extraction, using a word list per language (`codify.quality.lexicons.languages_with_lists()` names them; other languages are not scored). The rest measures your config against the text ([Write a jurisdiction config](write-a-jurisdiction-config.md)).

</details>

### Fetch native Akoma Ntoso from a publisher

There is no fetch command; call the adapter from Python. Each adapter is a `source_adapters` entry in the jurisdiction config:

```json
"source_adapters": [
  {
    "kind": "akn_native",
    "name": "example-publisher",
    "url_template": "https://legislation.example{frbr_uri}/data.xml",
    "rate_limit_per_minute": 30,
    "licence": "example-open-licence"
  }
]
```

The template takes `{frbr_uri}`, `{country}`, `{doctype}`, `{year}` and `{number}` from the work's address. Check the address without a request:

```python
from codify.acquisition import DocumentRef, make_polite_client
from codify.acquisition.adapters.akn_native import AknNativeAcquirer
from codify.jurisdictions import SourceAdapter

adapter = SourceAdapter(
    kind="akn_native",
    name="example-publisher",
    url_template="https://legislation.example{frbr_uri}/data.xml",
    rate_limit_per_minute=30,
)
client = make_polite_client(
    adapter, user_agent="xa-law-office/1.0 (+mailto:legislation@gov.example)"
)
acquirer = AknNativeAcquirer("xa", adapter, client=client)
ref = DocumentRef(jurisdiction_code="xa", doctype="act", year=1992, number="7")
print(acquirer.source_url(ref))
```

```text
https://legislation.example/akn/xa/act/1992/7/data.xml
```

`await acquirer.fetch(ref)` returns an `AcquiredDocument`. Write `doc.primary().content` to the corpus and ingest it by the Akoma Ntoso XML route in [Choose a route](#choose-a-route). Set `rate_limit_per_minute`, and pass your own `user_agent` with a contact address or set `ACQUISITION_USER_AGENT`; with neither, building the client raises `MissingUserAgent` before any request is sent.

<details><summary>What the fetch returns, the manifest, and the client's behaviour</summary>

The `AcquiredDocument` holds bytes with media type and SHA-256, the work address from the document's identification block (else from the ref), the expression address if named, the source address, `ETag`, `Last-Modified` and the adapter's licence. `{source_path}` in a template is filled only for one built-in publisher's path scheme; elsewhere the template resolves to nothing.

List what to fetch in `data/jurisdictions/<code>/corpus.json`, an object with `jurisdiction_code`, a `refs` list and an optional `notes` string, and no other top-level keys. `load_manifest("<code>")` reads it, or returns `None` if absent:

```json
{
  "jurisdiction_code": "xa",
  "refs": [
    {"jurisdiction_code": "xa", "doctype": "act", "year": 1992, "number": "7"}
  ]
}
```

`get_acquirer("<code>")` builds the acquirer from the first `source_adapters` entry. Import `codify.acquisition.adapters` first: that import registers the built-in adapters. The client from `make_polite_client`:

| Behaviour | Detail |
| --- | --- |
| Identity | The default names the Codify project. Pass your own, with a contact address; the config has no field for it. |
| `robots.txt` | Read once per host, kept 24 hours. A disallowed address is not requested. If `robots.txt` cannot be read, the host is refused for up to five minutes. Both return status 451 ([What can go wrong](#what-can-go-wrong)). |
| Pace | With `rate_limit_per_minute` set, one request per interval per host, across every client in the process. On 429, 436 or 503 the interval doubles, or takes `Retry-After`, up to 300 seconds, then narrows as requests succeed. Five attempts per request. |
| No rate set | No pacing, although `akn_native` reports 60 per minute on `acquirer.politeness`. Always set the rate. |
| Credentials | None sent: `auth_scheme` and `auth_env_var` are checked at load, never read. Set the header on the client you pass. |
| Addresses | Each host, on every redirect hop, is resolved once and refused if private, loopback or link-local. |

</details>

### Write an acquirer for any other source

Every registered kind except `akn_native` refuses jurisdictions it was not written for. For your own portal, subclass `BaseAcquirer` and implement `fetch`:

```python
import asyncio
from pathlib import Path
from typing import ClassVar

from codify.acquisition import AcquiredDocument, Body, DocumentRef
from codify.acquisition.base import BaseAcquirer
from codify.frbr import build_frbr_work_uri
from codify.jurisdictions import SourceAdapter


class GazetteShare(BaseAcquirer):
    """Scanned gazette PDFs the publishing office already holds on disk."""

    JURISDICTION: ClassVar[str] = "xa"

    async def fetch(self, ref: DocumentRef) -> AcquiredDocument:
        path = Path(self._base) / ref.extra["file"]
        return AcquiredDocument(
            ref=ref,
            frbr_work_uri=build_frbr_work_uri("xa", ref.doctype, ref.year, ref.number),
            bodies=[Body(media_type="application/pdf", content=path.read_bytes())],
            source_url=path.resolve().as_uri(),
            ocr_required=True,
        )


adapter = SourceAdapter(
    kind="pdf_gazette", name="gazette-share", base_url="data/fixtures/synthetic/xa"
)
ref = DocumentRef(
    jurisdiction_code="xa",
    doctype="act",
    year=1992,
    number="7",
    extra={"file": "legislation-act-1992.pdf"},
)
doc = asyncio.run(GazetteShare(adapter).fetch(ref))
body = doc.primary()
print(doc.frbr_work_uri, body.media_type, len(body.content), body.sha256[:16], doc.ocr_required)
```

```text
/akn/xa/act/1992/7 application/pdf 1200079 f7deab7277ef29d6 True
```

For a network source, build `self._client = make_polite_client(adapter, profile=self.politeness)` in `__init__` and request through it. Call your class directly.

<details><summary>Registering your acquirer with <code>get_acquirer</code></summary>

`register_adapter(kind, factory)` lets `get_acquirer` find it, but `kind` must already be in the config schema, and registering replaces that kind's built-in adapter process-wide. Import `codify.acquisition.adapters` before you register, or its first import replaces your factory.

</details>

## Check your result

- Every file has a provenance record whose hash matches the file.
- The `scan-corpus` counts account for every `.pdf` and `.txt` file. Count `.md` files yourself; it does not read them.
- Each fetched document's `doc.frbr_work_uri` is the address you expect.

## What can go wrong

| What you see | Cause | What to do |
| --- | --- | --- |
| `UnknownAdapterError: no source_adapters configured for 'xa'` | No `source_adapters` entry | Add one, or construct the acquirer directly |
| `UnknownAdapterError: no Acquirer registered for kind='akn_native' ('xa'); register it in codify.acquisition.adapters` | The adapters package was not imported | `import codify.acquisition.adapters` first |
| `ValueError: no pdf_gazette acquirer for 'xa'` | The kind belongs to another publisher's adapter | Write your own acquirer |
| `ValueError: akn_native adapter 'p' (xa) has no url_template` | The entry lacks `url_template` | Add it |
| `FileNotFoundError: <url> -> 404 (document may be PDF-only upstream)` | The publisher has no XML for this document | Take the PDF and ingest it as a file |

<details><summary>Refusals from the network client</summary>

| What you see | Cause | What to do |
| --- | --- | --- |
| Status 451, header `x-codify-blocked-by: robots.txt`, body `blocked by <host>/robots.txt for user-agent '...'` | The publisher disallows the address | Ask the publisher. `robots_respect: false` in the entry turns the check off; use it only with their agreement. |
| Status 451, header `x-codify-blocked-by: robots.txt-unavailable`, body `robots.txt for <host> could not be read (...)` | `robots.txt` unreadable | Wait five minutes, or ask the publisher |
| `SSRF blocked: 127.0.0.1 in 127.0.0.0/8 (http://127.0.0.1/law.pdf)` | The host resolves to a private address | Fetch from the public address, or read the file from disk |

</details>

## Status and limits

| Capability | Status |
| --- | --- |
| Ingest files you already hold (`ingest-one`, `load`, upload to `codify serve`) | **Available** |
| Sort text-layer files from scans (`scan-corpus`) | **Available** |
| Index builders and a crawler written for specific publishers' archives, listed in the [CLI reference](../reference/cli.md) | **Available** |
| PDF metadata and hash (`extract_pdf_metadata`) | **Library only** |
| Fetch native Akoma Ntoso (`akn_native`, `get_acquirer`) | **Library only** |
| Fetch and ingest one EU act by its address (`POST /runs/ingest-url` under `eu`) | **Available** |
| Rate-limited client that honours `robots.txt` (`make_polite_client`) | **Library only** |
| Corpus manifest (`corpus.json`, `load_manifest`) | **Library only** |
| Fetch log in the database (`record_acquisition`); no command writes it | **Library only** |
| Conditional requests from stored `ETag` values (the `etag_lookup` argument) | **Library only** |
| A general fetch or acquire command | **Not in the core** |
| Sending credentials from `auth_scheme` and `auth_env_var` | **Not in the core** |
| Scheduled re-fetching and change detection | **Not in the core** |

The [jurisdiction config reference](../reference/jurisdiction-config.md) lists the `source_adapters` fields.

## Next

[Read scanned pages (OCR)](ocr.md)
