---
title: Interoperate
description: Export your laws as Akoma Ntoso files, check them before another system takes them, and bring in AKN that a publisher or another tool produced.
sidebar:
  order: 11
---

You end with every stored version of a jurisdiction's laws as a schema-checked, citable
[Akoma Ntoso](../concepts/akoma-ntoso.md) (AKN) file, and with AKN from another system imported.

## Before you start

- A migrated database holding the worked example,
  `data/fixtures/synthetic/xa/legislation-act-1992.pdf`, structured in
  [First ingest](../start/first-ingest.md) and stored with `uv run codify load bundle/`.
- `uv run codify serve` running against it, on `http://127.0.0.1:8000` unless you pass
  `--port` ([Serve and publish](serve-and-publish.md)).
- `curl`, `jq`.

## The idea in brief

Two AKN systems can exchange files when they agree on the schema, fixed by OASIS; the names,
meaning the [FRBR URIs](../concepts/frbr.md) and the
[eIds](../concepts/akoma-ntoso.md#identifiers-eid-and-wid) (element identifiers) by which a
receiver recognises a law and keeps citations working; and the profile, a narrower rule set
such as AKN4EU. Codify writes AKN 3.0 and claims no profile;
[AKN profiles and known differences](../concepts/akn4eu-divergences.md) lists its choices.

Export returns the AKN text the pipeline stored, not a regeneration from rows.

## Export one version

```bash
CODIFY=http://127.0.0.1:8000
```

1. List the jurisdiction's laws with their work URIs:

   ```bash
   curl -s "$CODIFY/laws?jurisdiction=xa" | jq -r '.items[] | "\(.id)  \(.frbr_work_uri)"'
   ```

   ```text
   21278932-9b16-4320-b50a-c832f3c61806  /akn/xa/act/1992/7
   ```

2. List the law's versions. Each is one expression: one language, as the text stood on one
   date.

   ```bash
   curl -s "$CODIFY/laws/21278932-9b16-4320-b50a-c832f3c61806" | jq -r '.versions[] | "\(.id)  \(.expression_uri)"'
   ```

   ```text
   20cb9d70-142f-47fc-993b-98d884a79ad8  /akn/xa/act/1992/7/eng@1992-05-02
   ```

3. Write the version's `akn_xml` to a file:

   ```bash
   curl -s "$CODIFY/versions/20cb9d70-142f-47fc-993b-98d884a79ad8" | jq -r .akn_xml > legislation-act-1992.xml
   ```

For the worked example, the file is the bundle's `final.akn.xml` plus a final newline from `jq`; without a database, hand
over that file ([bundle reference](../reference/bundle.md)).

## Check a file before you hand it over

A PDF or text ingest runs the structural validator, not the OASIS schema check
([two kinds of validation](../concepts/akoma-ntoso.md#two-kinds-of-validation)), so check every
exported file. Save this as `check_export.py`:

```python
import sys
from collections import Counter
from pathlib import Path

from codify.akn import NS, parse_xml, validate_akn
from codify.frbr import is_citable_work_uri

xml = Path(sys.argv[1]).read_text(encoding="utf-8")
validate_akn(xml, strict=True)
root = parse_xml(xml)
work = root.find(".//akn:FRBRWork/akn:FRBRuri", NS).get("value")
expression = root.find(".//akn:FRBRExpression/akn:FRBRuri", NS).get("value")
eids = Counter(el.get("eId") for el in root.iter() if el.get("eId"))
print("schema: valid (strict)")
print("work:", work, "citable" if is_citable_work_uri(work) else "NOT citable")
print("expression:", expression)
print("eIds:", len(eids), "repeated:", [e for e, n in eids.items() if n > 1])
print("derived refs:", len(root.findall(".//akn:ref[@class='derived']", NS)))
```

```bash
uv run python check_export.py legislation-act-1992.xml
```

```text
schema: valid (strict)
work: /akn/xa/act/1992/7 citable
expression: /akn/xa/act/1992/7/eng@1992-05-02
eIds: 167 repeated: []
derived refs: 5
```

<details>
<summary>What each line tells the receiving system</summary>

| Line | Why it matters |
| --- | --- |
| `schema` | `strict=True` uses the official OASIS schema. The default, a lenient schema bundled with Cobalt (Laws.Africa's Python library for AKN), accepts repeated eIds and year-only dates, which the official schema refuses. |
| `work` | Citable means a four-digit year in the date segment and a real number. The year `0001` or a number starting `draft-` marks a URI minted because the year or number could not be read: the ingest finishes, but the URI names no real law. A full date in the year segment is also refused (see [FRBR](../concepts/frbr.md#the-uri-grammar)). Correct it before export. |
| `expression` | The language and date the receiving system files this text under. |
| `eIds` | Repeated eIds make the document schema-invalid and break citations to the repeated provision. |
| `derived refs` | Links Codify inferred from wording, marked `class="derived"`. Agree with the receiving system whether to keep or strip them. |

</details>

## Export a whole jurisdiction

There is no export command or bulk route, so page through the read routes. Save this as
`export.py`:

```python
import json
import sys
from pathlib import Path
from urllib.request import urlopen

from lxml import etree

from codify.akn import validate_akn
from codify.frbr import is_citable_work_uri

server, jurisdiction, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)


def get(path: str) -> dict:
    with urlopen(f"{server}{path}") as response:
        return json.load(response)


offset = 0
while True:
    page = get(f"/laws?jurisdiction={jurisdiction}&limit=100&offset={offset}")
    for summary in page["items"]:
        law = get(f"/laws/{summary['id']}")
        for version in law["versions"]:
            xml = get(f"/versions/{version['id']}")["akn_xml"]
            name = version["expression_uri"].strip("/").replace("/", "_").replace("@", "_")
            (out / f"{name}.xml").write_text(xml, encoding="utf-8")
            try:
                validate_akn(xml, strict=True)
                schema = "valid"
            except etree.DocumentInvalid as exc:
                schema = str(exc).splitlines()[0]
            citable = "citable" if is_citable_work_uri(law["work_uri"]) else "NOT citable"
            print(f"{name}.xml  {citable}  {schema}")
    if len(page["items"]) < 100:
        break
    offset += 100
```

```bash
uv run python export.py "$CODIFY" xa export
```

```text
akn_xa_act_1992-05-14_7_eng.xml  NOT citable  valid
akn_xa_act_1992_7_eng_1992-05-02.xml  citable  valid
```

This run followed the uploads below, which leave two laws; the first is schema-valid but not
citable, so the script checks both.

<details>
<summary>Page size, and why not the MCP server</summary>

The page-size limit of `/laws` is in the [HTTP API reference](../reference/http-api.md). Use
HTTP rather than the `get_version` tool of the MCP server (the interface for AI agents), which
returns XML only with `include_xml` and then cuts long documents at a fixed size
([MCP tools reference](../reference/mcp-tools.md)).

</details>

## Bring in AKN from another system

Upload the file to `POST /runs/ingest` with the jurisdiction and an optional `title` (without
one, the law is listed under its work URI). It is stored without a model or structuring.

<details>
<summary>Which files take which path, and what the import repairs</summary>

A file ending `.xml`, `.akn` or `.fmx` under any jurisdiction except `eu` skips the model. Two
publisher-specific XML formats, recognised by their content, go to their own converters; any
other file takes the published-AKN path, which keeps the document as published apart from the
repairs below. XML from the EU Publications Office, as AKN4EU or FORMEX (the Office's own
format), takes a separate path under the jurisdiction `eu` (see
[AKN profiles and known differences](../concepts/akn4eu-divergences.md#akn4eu)).

On the way in, the published-AKN path:

- maps `/akn/...` URIs, legislation.gov.uk `/id/{type}/{year}/{number}` identifiers and EU
  `/european/...` links to Codify's `/akn/...` form, rewriting the top-level `FRBRWork` and
  `FRBRExpression` URIs and leaving the manifestation and nested component blocks as published;
- keeps the first of any repeated eId and renames later ones `{eId}_dup2`, `_dup3`, reported as
  `deduped_eids`;
- gives an eId to any provision without one (`codify-synth-1` in the body), reported as
  `synthesised_eids`;
- runs the structural validator; it does not run the schema check.

</details>

For the EU lane, upload the file under `eu` with the same request, or give the server the act's
address with `POST /runs/ingest-url` ([Serve and publish](serve-and-publish.md#ingest-a-document-through-the-server)).
With an embeddings endpoint set, the server embeds what it imports, so the law is searchable at once.

To practise, upload the shipped extract of the worked example's Act (synthetic `xa`, Atlantis)
as the [Bluebell](../concepts/bluebell.md) parser writes it, before Codify sets its identity. Its placeholder identity has shapes
other tools also produce: the publication date in the work URI's year segment, an undated
expression, and the title `Untitled`.

```bash
curl -s -F file=@data/fixtures/synthetic/xa/legislation-hcontainer.akn.xml -F jurisdiction=xa -F "title=Legislation Act, 1992" "$CODIFY/runs/ingest" | jq '{id, status}'
```

```text
{
  "id": "b4cc1a2c-033c-4acc-9f7b-53036eead204",
  "status": "queued"
}
```

```bash
curl -s "$CODIFY/runs/b4cc1a2c-033c-4acc-9f7b-53036eead204" | jq '{status, result, error}'
```

```text
{
  "status": "succeeded",
  "result": {
    "kind": "complete",
    "work_uri": "/akn/xa/act/1992-05-14/7",
    "expression_uri": "/akn/xa/act/1992-05-14/7/eng",
    "version_id": "9bd6212e-f552-4733-a483-6edf0fb21c66",
    "law_id": "ae8d271b-ef91-41dd-8fe9-a0072f4065e7"
  },
  "error": null
}
```

The event stream, `GET /runs/{id}/stream`, carried one warning:

```text
event: validation_issued
data: {"kind": "validation_issued", "issue": {"check": "uncitable_work_uri", "severity": "warning", "uri": "/akn/xa/act/1992-05-14/7", "message": "Work URI '/akn/xa/act/1992-05-14/7' carries no citable year and number, so the document cannot be cited or resolved by its identity."}}
```

AKN allows a full date in the work's date segment; Codify cites only a four-digit year. The
upload keeps an `/akn/...` work URI as written, so the Act is now stored twice, the second time
under an identity Codify will not cite.

### Fix an identity before you import

Date the expression by what the text is. This extract is the Act as enacted, which the worked
example stores under its assent date, 2 May 1992; dating it by gazette publication, 14 May,
would record a version that never existed.

Copy the fixture, save the script beside it as `restate.py`, and run `uv run python restate.py`:

```bash
cp data/fixtures/synthetic/xa/legislation-hcontainer.akn.xml .
```

```python
import sys
from pathlib import Path

from codify.akn import NS, parse_xml
from codify.frbr import build_frbr_expression_uri, build_frbr_work_uri, is_citable_work_uri
from codify.pipeline.enrich.cobalt import set_work_uri

source = Path("legislation-hcontainer.akn.xml").read_text(encoding="utf-8")
work = build_frbr_work_uri("xa", "act", 1992, "7")
expression = build_frbr_expression_uri(work, "eng", "1992-05-02")
fixed = set_work_uri(source, work, expression, "1992-05-02")
if fixed is None:
    sys.exit("identity already matches; nothing to write")
Path("legislation-hcontainer.fixed.xml").write_text(fixed, encoding="utf-8")

root = parse_xml(fixed)
for level in ("FRBRWork", "FRBRExpression", "FRBRManifestation"):
    print(level, root.find(f".//akn:{level}/akn:FRBRuri", NS).get("value"))
print(is_citable_work_uri(work))
```

```text
FRBRWork /akn/xa/act/1992/7
FRBRExpression /akn/xa/act/1992/7/eng@1992-05-02
FRBRManifestation /akn/xa/act/1992/7/eng@1992-05-02
True
```

![Two columns of identity values from the fixture, before and after set_work_uri. Work: FRBRthis changes from /akn/xa/act/1992-05-14/7 to /akn/xa/act/1992/7/!main; FRBRuri changes from /akn/xa/act/1992-05-14/7 to /akn/xa/act/1992/7, the citable work URI; FRBRalias stays Untitled, to edit by hand; FRBRdate stays 1992-05-14. Expression: FRBRthis and FRBRuri change from /akn/xa/act/1992-05-14/7/eng to /akn/xa/act/1992/7/eng@1992-05-02; FRBRthis gets no /!main suffix, unlike the work's, so edit it by hand; FRBRdate changes from 2026-08-24 to 1992-05-02. Manifestation: FRBRthis, FRBRuri and FRBRdate change exactly as the expression's do. The schedule's work FRBRthis becomes /akn/xa/act/1992/7/!schedule_1.](../figures/v17-identity.svg "The fixture's identity as restate.py leaves it; the bold value is the work URI that makes the Act citable.")

`set_work_uri` returns `None` when the identity already matches. `build_frbr_work_uri` applies
your config's `frbr.uri_patterns`, so the URI has the shape your own ingests produce. The work's `FRBRalias` stays `Untitled` and
the expression's `FRBRthis` gets no `/!main` suffix; edit them by hand if the receiving system
reads them.

Upload the fixed file:

```bash
curl -s -F file=@legislation-hcontainer.fixed.xml -F jurisdiction=xa "$CODIFY/runs/ingest" | jq -r .id
```

The run's `result`, read by that id:

```text
{"kind":"complete","work_uri":"/akn/xa/act/1992/7","expression_uri":"/akn/xa/act/1992/7/eng@1992-05-02","version_id":"20cb9d70-142f-47fc-993b-98d884a79ad8","law_id":"21278932-9b16-4320-b50a-c832f3c61806"}
```

These are the worked example's law and version, and the stream carries no `uncitable_work_uri`: storage keeps one
version per expression URI, so nothing new was stored. Without the worked example, the file
becomes the Act's original expression.

`codify load` with `--jurisdiction` and `--title` stores a bare AKN file as given, with no
identifier mapping, eId renaming or validator; upload AKN you did not produce instead.

## Check your result

- Every exported file: `schema: valid (strict)`, a `citable` work URI, an empty `repeated` list.
- Every import: `GET /runs/{id}` shows `succeeded`, the stream no `uncitable_work_uri`; an already stored expression URI returns
  the existing `version_id`.

## What can go wrong

| What you see | Cause | What to do |
| --- | --- | --- |
| `lxml.etree.DocumentInvalid: ... Duplicate key-sequence ['part_I__sec_1__point_1'] in unique identity-constraint ...` from `check_export.py` | Two provisions share an eId. | Correct it at source and re-ingest. |
| `NOT citable` in the export, or `uncitable_work_uri` in a run | The year or number was not read, or the file uses a shape Codify refuses, such as a full date in the year segment. | Correct the identity, as above, or the source metadata. |
| `identity already matches; nothing to write` from `restate.py` | `set_work_uri` returned `None`. | Upload the original file. |
| Run `failed` with `akn_native: XMLSyntaxError: XML declares a DOCTYPE; refusing to parse it (line 0)` | DTDs are refused so that no entity can be resolved. | Remove the `<!DOCTYPE ...>` line. |
| Run `failed` with `ForeignJurisdictionCollision: /akn/xa/act/1992/7 already names a law outside xu; refusing to write` | Work URIs are unique across the database; this one belongs to another jurisdiction. | Upload under that jurisdiction, or give the document its own identity. |
| `codify load` ends `sqlalchemy.exc.IntegrityError: ... duplicate key value violates unique constraint "provisions_version_id_akn_eid_key"` | `load` does not rename repeated eIds; nothing is stored. | Upload to `POST /runs/ingest`, which renames them. |
| The receiving system refuses annexes | Codify writes each schedule inline as an `attachment`, never a referenced document. | See [AKN profiles and known differences](../concepts/akn4eu-divergences.md). |

## Status and limits

| Capability | Status |
| --- | --- |
| Export one version's AKN | **Available**: `GET /versions/{id}`; the web reader's download button; the bundle's `final.akn.xml` |
| Export a whole jurisdiction | **Not in the core**: no command, route or function; page through the read routes as above |
| Schema check of an exported file | **Library only**: `codify.akn.validate_akn(xml, strict=True)` |
| Import published AKN, with identifier mapping and eId repair | **Available**: `POST /runs/ingest` with an `.xml` or `.akn` file |
| Import AKN as given | **Available**: `codify load file.xml --jurisdiction ... --title ...` |
| Restate a document's identity | **Library only**: `codify.pipeline.enrich.cobalt.set_work_uri` |
| Resolve a law by its FRBR URI over HTTP (`GET /akn/...`) | **Not in the core**: the routes take ids; the URI is in each response |
| Import from, or export to, the Indigo platform | **Not in the core**: Codify uses the same open-source libraries, Bluebell and Cobalt, but has no Indigo test or connector |
| Write the legislation.gov.uk AKN format, or check AKN4EU conformance | **Not in the core** |
| ELI identifiers, RDF, PDF or Word output | **Not in the core** |

## Next

[Architecture](../concepts/architecture.md): how the pipeline fits together, and which module
does each part.
