# Changelog

Dates are cut dates. A version is released when its tag exists; the first tag is
the launch tag.

## Unreleased

Breaks, in that the structurer's output changes for documents it already read:

1. The UK keyword-less scan (`gb`, `gb-eng`, `gb-wls`, `gb-sct`, `gb-nir`) reads
   the shapes a statutory instrument prints in a PDF. An article opening on its
   first paragraph (`2.—(1)`, `2.-(1)`, with a space or a bold number) is a
   unit. A stray middle dot or tilde after a number does not hide it, and a
   Welsh month after a number reads as a date, not a heading. A short run of
   sections, each numbered 100 or more and the smallest over eight times the
   larger of the two beside the run, is dropped as page numbers. Where
   `amendments.trigger_phrases` is declared, embedded amendment marking covers
   the UK sections before the first schedule, which restarts the numbering. An
   em dash or a soft hyphen ends a lead-in, and the quoted unit follows it
   directly, under at most a heading line. A caption before the first
   provision read is front matter when it is a cover note (a line inside it
   that opens on a schedule keyword is its prose), or lists a schedule in a
   contents list (it sits under a numbered entry or another such caption and
   the text prints it again). Front matter ends no body and anchors nothing;
   the last contents entry before a schedule's caption is no section, and a
   schedule paragraph printed above its caption keeps its anchor. A line
   of a keyword and a number inside a schedule, or beside its caption, is the
   citation of the provision that introduces it, not a section.
2. Every Riigi Teataja act whose source carries an abbreviation (`lyhend`) gets a
   new work URI. The URI now slugs the full title on every path: the transformer,
   `build_ee_index` and `EeDatadumpAcquirer.fetch`, which share
   `codify.acquisition.adapters.ee.identity`. `fetch` reads the URI from the
   member, not from the index or the ref. Only the constitution, known by document
   type `põhiseadus`, abbreviation `PS` or its exact title, takes
   `/akn/ee/act/1992/pohiseadus`. A title that merely contains `põhiseadus` no
   longer does.

New, additive:

- `akn_native` acquirer: a ref carrying `extra["pdf_fallback"]` takes the PDF that a
  document of metadata alone names, instead of failing as PDF-only. The PDF is the
  untitled alternative in the ref's language (English unless `languages` names Welsh), on
  the document's own host, upgraded to https, and it must open with `%PDF-`. A redirect is
  followed only while it stays on that host over https. A ref for a
  language other than English takes that PDF even where the document has text, which is
  English. An English ref to a document with text, and a ref without the flag, behave as
  before.
- `codify.pipeline.formats.boe` converts a Boletín Oficial del Estado item's
  XML to AKN without a model: books, titles, chapters, sections, articles
  (ordinal, cardinal and `bis` numbers), apartados and letters, the four kinds
  of disposition as named `hcontainer`s, preamble and enacting formula,
  conclusions and annexes. Text quoted for amendment stays inside its article.
  `dispatch` routes a BOE item there by its root. `BoeAcquirer`, registered as
  the new `gazette_xml` source kind, fetches the item by its ELI through the
  polite client, and hands on the PDF when the XML carries no text.
- Migration `0023_source_spans` and `codify.storage.spans`: a multi-act
  source's split is stored as spans over its pages, one generation at a time.
  Each span is an act, a held region, a skipped non-act or front matter; its
  cuts are a page, an offset into that page's text and a marker line.
  `write_span_generation` retires the live generation and writes the next,
  deleting nothing. `versions.source_span_id` names a child version's span, and
  `get_page_reads`, `get_page_read` and `count_disputes_by_page_read` read a
  child through the page reads its span links (`source_span_pages`), with the
  source's own page numbers. `codify.pipeline.span_cuts` turns a `Segmentation`
  into cuts, rebuilds a span's text from page texts, and finds a cut again after
  a re-read by its marker (`None` when the page no longer carries it).
  `locate_generation` finds a whole split again, each end where the next
  region now starts, or `None` when a start is lost or the starts run
  backwards. A child's repair evidence is trimmed to its span. A page read a span links cannot be deleted (`RESTRICT`):
  a host's cleanup must spare linked reads. The `versions` column is added under
  a 5-second lock timeout, its foreign key validated and its index built without
  blocking writes.
  `segmentation.skip_heading_patterns` names instruments that are not acts
  (notices, appointments): their segments are cut as skipped, kept and listed.
- `codify.pipeline.segment`: splits a source holding several acts, such as a
  gazette issue, without a model call. `segment` returns a `Segmentation`:
  `single` (the text unchanged), `decided` (one segment per act) or
  `abstained` (what the evidence could not settle is held whole, with a
  reconciliation table in plain words). A boundary is a declared act heading
  that a closing phrase, a numbering restart, a contents entry on its page or
  a page start agrees with. An adopted text, an attachment, a repeat of the
  open act's heading, a quotation or a missing enacting formula vetoes one. A
  heading no signal agrees with is read as a citation where it stands, named
  in the reconciliation table, and neither splits nor holds. `segment_volume`
  reads a bound volume a page at a time, cuts it into issues by the same rule
  and segments each issue. Configured by the jurisdiction's `segmentation`
  block: `act_heading_patterns`, `issue_heading_patterns`, `contents_keywords`
  and `printed_page_pattern`, each pattern refused at load if it does not
  compile or can match empty text.
- `codify ingest-one` writes `page_spans.json` (the page count, where each
  page with body text sits in `source.txt`, and each page's header and footer) and `segmentation.json` (the
  segmenter's outcome, acts, held regions and reconciliation table) into the
  bundle. `codify segment <bundle>` re-runs the segmenter over that stored
  text under the current config, with no model.
- Migration `0024_current_versions`: `versions.is_current` marks each law's
  current original version, kept by trigger on insert, delete and any update
  of the columns that order versions, and unique per law.
  `latest_versions_global` and `latest_versions_for_jurisdiction` read it when
  no language is asked for. `CurrentScope` (built from jurisdiction codes by
  `current_scope`) names those versions by jurisdiction, year range and
  doctype; `hybrid_search`, `query_tokens_for` and `count_embedded_versions`
  take one in place of an id list, `count_in_scope` counts it, and `retrieve`
  uses one for a jurisdiction searched in its original language. The search
  then joins the flag instead of binding every id, which the planner estimated
  element by element: seconds of planning per query on a large corpus.
- Migration `0025_atlas_build_kind` adds `atlas_build` to the run kinds; the
  downgrade refuses while rows of that kind exist.
- Migration `0026_provisions_created_at_idx` indexes `provisions` on
  `(version_id, created_at)` and drops `provisions_version_id_idx`, which the
  new key's prefix makes redundant. `max(created_at)` for one version is then
  a single index descent instead of a read of every provision.
- Migration `0027_query_index_hygiene` drops the unread GIN over
  `provisions.text_search` (the column stays), an HNSW over
  `version_unit_embeddings` that no migration created and no query orders by,
  and `sections_version_id_idx`, `annex_rows_version_idx` and
  `law_links_source_idx`, whose columns lead a wider index on the same table.
  It indexes `registry_works.law_id`, `repair_proposals.run_id` and
  `runs.params->>'retried_from'`, and vacuums `provisions` after 2% new rows.
- `prune_dead_terms` scans for orphaned terms without a lock, then takes the
  `laws`/`provisions` share lock only to re-check those candidates through the
  `search_tsv`/`title_tsv` GIN indexes. It refuses with `55P03` if DDL holds
  either table, and raises unless the session is READ COMMITTED.
- `list_laws` chooses its page before reading version dates, so only the
  page's laws are dated; the ingested sort still aggregates every version.
- The hybrid search's keyword arm keeps the top `pool` matches before
  numbering them, a bounded sort instead of a sort of every match.
- The keyword arm reads a scope of at most 10,000 laws and 150,000 provisions
  before ranking its matches; a larger scope, or the whole corpus, keeps the
  BM25 index walk. The walk filtered to the scope only as it went, so terms
  common in the corpus but rare in the scope read past most of it.
- `structuring.recurring_furniture`: for a layout engine that files footnotes
  and page-top captions under header or footer. Furniture is then a bare page
  number or a short block another page repeats, a running head split across
  blocks included. A block that opens with a declared attachment caption is not
  a running head, nor evidence that another is. A one-off block stays in the text, and so does a number of
  one or two digits among its page's text; a page loses no more lines of a number
  than its layout holds blocks of it. A footnote is a block the engine filed as a
  footer or reference and the run of lines at the foot of its page that says what
  it says; it moves to the foot as its own paragraph, and where no such run is
  found it stays where it is and `footnote_run_unplaced` is logged. A text block
  at the foot that opens on a marker is a provision, whatever its place suggests.
  `combine_text_for_structure` takes the jurisdiction as `country`.
- `attachments[].always_opens`: a caption that titles no table in the body, so
  numbering that continues past it does not read it as a table caption, and the
  keyword reading of its line is dropped.
- `codify.pipeline.formats.riigi_teataja_annexes`: `annex_files` reads the file
  each Riigi Teataja annex embeds, base64, beside its link, keyed by the eId of
  the attachment the converter writes for it. `fill_annex_text` writes a
  transcript into those attachments, one `transcribedSource` `hcontainer` per
  page after the link; an attachment already holding one is left alone.
- `SourceAdapterKind` gains `oecd_compendium` (Tier 2), with
  `OecdCompendiumAcquirer` over the Compendium of OECD Legal Instruments' JSON
  API: `discover` lists the instruments in force with their publish date, and
  `fetch` returns the bodies in the languages the ref names (English and French
  when it names none), the first as primary, each wrapped in a head whose
  `oecd.*` meta tags name the instrument and date its latest revision, the last
  amendment in the record's change history (follow-up reports and entry into
  force are not revisions). `last_modified` is the listing's
  publish date. The record's committee, themes, related instruments, adherents
  and monitoring reports travel as `upstream_metadata`. A packaged `oecd` config
  names the adapter, so `get_acquirer("oecd")` resolves it.
- `OecdCompendiumAcquirer.details` reads one instrument's record the way `fetch`
  carries it, plus its English title, for an instrument of any status and with
  or without a body text.
- `codify.pipeline.formats.oecd_html` segments that body into AKN without a
  model: preamble citations and recitals, the enacting line, English or French
  verb sections with paragraphs numbered across them, parts, titles, chapters and
  articles in codes and conventions, and `WE ...` declarations. The work is
  dated by adoption and the expression by the latest revision, so a revised text
  is a new expression. A verb section (`INVITES`, `INSTRUCTS` and the like)
  whose addressee is not the Adherents, or each such item of a section naming
  none, carries `refersTo="#nonAdherentDuty"` with its contents. An `Annex` line closes the operative text;
  each annex becomes an attachment. `dispatch` routes a local HTML source
  carrying the acquirer's head to it.
- Body elements carry `refers_to`, read from and written back to `refersTo`.
- `pool_excluded_refers_to` in a jurisdiction config: units at or under an
  element whose `refersTo` names a listed value are stored with
  `excluded_from_pool` and reason `marked`, by the mapper, by
  `retokenise_version_provisions` (which reads the stored AKN) and by the
  comparator's `is_excluded`. `marked_ids` takes a document's marked units once;
  `is_excluded` and `provision_text` accept it, so a marked part of an unmarked
  unit stays out of the text the comparator embeds and prompts with. The marker
  survives translation, which rewrites
  headings. A `marked` row is still law: `LAW_REASONS` lets the reference
  resolver and the in-document link pass land on it, as they never land on a
  placeholder. The `oecd` config lists `#nonAdherentDuty`.
- `codify.core.tracing.record_agent_usage` prices an agent run's model responses
  as one direct Langfuse generation, for an agent whose endpoint reports no cost.
- `codify.core.pricing` prices `gpt-5.6-sol` at Azure's Global Standard short-context
  rate.

Fixed:

- `codify.core.pricing` prices `gemini-3.6-flash` at its intro rate to 2026-12-31,
  $0.75 in and $3.75 out per million tokens, which it had at double.
- `oecd_html` reads declarations, ministerial conclusions, arrangements and
  agreements that raised "no operative text". It recognises:
  - `WE, the Ministers…` and `NOUS, MINISTRES…` openers
  - first-person, third-person-plural and past-tense verbs (`DECLARE that we:`,
    `DÉCLARONS`, `WELCOMED`)
  - numbered paragraphs that open on a verb, and numbered lists inside a
    paragraph
  - Roman article numbers, `1)` paragraphs, `a.` points under a `1.` paragraph
  - a sentence per item under a colon lead-in
  - an excerpt that opens with a quotation mark

  A text with no operative verb is all body. Paragraphs straight under the body
  or an annex sit in unnumbered sections that open at each heading line. Where
  the text has articles, a group with no heading is an `hcontainer`. A paragraph
  the source does not number gets no `<num>`.
- `oecd_html` reads `PARTIE`, `CHAPITRE` and `TITRE` as it reads `PART`,
  `CHAPTER` and `TITLE`. A French part's lead-in no longer falls into the
  section before it and takes that section's `#nonAdherentDuty` mark.
- No source word is dropped:
  - consecutive heading lines all reach the unit they head;
  - a heading between list items ends the list;
  - a heading left at the end is kept as a paragraph;
  - a caption with no annex under it (a contents line) is kept as text;
  - `APPENDIX` opens an attachment as `ANNEX` does;
  - a `PREAMBLE` label stays in the preamble. The addressee rule also
  marks `WE CALL ON…` paragraphs, `5. CALL on…` paragraphs and items under a
  section in the declarants' own voice.

- Riigi Teataja converter: a line break ends one `<p>` and the text after it
  starts the next, which Bluebell reads as a line too. Every `sisuTekst` of a
  unit is written, not only the first: text before a paragraph's lõiked is its
  `intro`, text after them its `wrapUp`, text between two units follows the
  first, and a top-level `punkt` keeps its points. Headings keep the text after inline markup, with superscript digits.
  Unmapped inline markup keeps its text, and a space between two inline
  elements stays. A native `tabel` or an HTML `table` becomes an AKN `table`
  with row eIds, unless it only places text: lines of up to 100 characters
  beside a column blank in every row, with no spanned cell, or every cell
  reading `allkirjastatud` (signed). Those stay out of the body, as signatures
  already do, and the conversion logs how many it left out.
- The reader projection reads every `<p>` of an `intro` or `wrapUp`, not only
  the first; one that holds a table is read like content, in source order.
- Provision text, intro and wrap-up included, keeps table rows, cells and a
  cell's paragraphs apart.
- `ensure_unique_eids`: a renamed duplicate never takes an eId the document
  already publishes, so a reference to an id such as `x_dup2` keeps naming the
  element it named.
- `segment`: where several headings name one listed act, the contents entry
  goes to the one on its listed page, so a prose mention elsewhere no longer
  makes the source abstain.
- Scaffold: a `line_anchored` document whose first two units, in text order,
  are 1 and 2 as the UK keyword-less scan read them before any schedule no
  longer fails as a void boundary policy; those units show the layout held, and
  `anchor_boundary_policy_held` is logged.
- `recurring_furniture`: a line that opens an attachment is never furniture, so
  a schedule's caption printed inside a repeated head block stays, and a head
  that prints the previous page's caption again does not repeat it; a caption
  block printed on a head's row is not part of the row.
- `basic_unit_numbers` folds digits through `normalise_digits`, so an article
  numbered in Devanagari, Bengali or Thai digits is in the unit set.
- Riigi Teataja: an annex links its file under the text's own `globaalID`. The
  first `aktViide` names the act's original publication, which does not hold a
  consolidated text's later annex files, and older texts write it as a URL. A
  text without a `globaalID` keeps the annex title unlinked.

## 0.6.0 (2026-10-02)

Eight breaks, so the minor moves, as `VERSIONING.md` prescribes while the major
is zero; the rest is additive.

Breaks, in that the structurer's output changes for documents it already read:

1. A declared closing phrase also matches where the source breaks the line
   between two of its words (one line break, any spaces or tabs), so the
   attestation after it leaves the body. Conclusion lifting, signatory grouping
   and the page-region signal read phrases the same way. A blank phrase no
   longer matches.
2. Losses are reported instead of reading as success, so a run that finished
   clean before can now grade `warning` or `blocking`, or fail:
   - A page the model refused on a content filter (partial text included), or
     an inked page read empty, is recorded: `PageResult.finish_reason`,
     `PageExtracted.finish_reason` and `PageExtracted.unreadable`, an
     `unreadable_page` error finding, `pages_unreadable` in the bundle
     manifest, and an editorial remark in the AKN where the page stood. The
     structurer now receives the unreadable-page marker and places the remark
     itself; `combine_text_for_structure` no longer strips it.
   - Body-fill records what it achieved (`ScanTrace.body_fill`). Bodies copied
     from the source after the model failed them, or restored because the model
     changed a table, are a `body_fill_verbatim` warning; bodies left empty a
     `body_fill_incomplete` error. When every call failed and the model wrote
     no body, the structurer raises `BodyFillError`, or under
     `halt_policy="land"` records a `body_fill_failed` halt. The scan trace is emitted once, after body-fill.
   - `coverage.json` `ratio` is the lowest of the anchor, body-fill and page
     ratios; the anchor figure moves to `anchor_ratio`. The manifest carries the
     structural grade.
   - A jurisdiction config that is absent or does not validate raises where it
     is read. Anchor-scan helpers, title helpers, the region vocabulary, the
     enrich passes and the repair dossier used to catch it and return defaults.
     A config file that cannot be read or decoded raises
     `JurisdictionConfigError`. The PDF and text lanes read the config first and
     emit `Failed(stage="config")` before any extraction or model call.
   - `validate_akn(provenance="extracted")`, which the PDF and text lanes pass,
     grades every numbering gap `warning` and adds a leading-gap `number_gap`
     when the first article or section is numbered above 1. A gap after a
     provision carrying an unreadable-page remark is no longer called a repeal.
     Numbering checks now cover `rule` units as well as articles and sections.
3. `validate_akn(provenance="native")`, which the lanes reading publisher XML
   (native AKN, FORMEX and the other publisher formats) pass, skips
   `identity_year_implausible` and `identity_year_unconverted_hijri`: a
   publisher's own date is not a misparse.
   Callers that do not pass `provenance` see the previous checks.
4. A quotation that opens on prose and that nothing closes now ends before the
   next keyword-led container or basic-unit heading, blank line or not. A
   dropped closer on a quoted name in a preamble no longer masks the first
   chapter and article below it. A quotation opening on a heading still masks
   to the blank line, since an amendment may quote several provisions.
5. A content-filter finish reason that carries a suffix
   (`content_filter: RECITATION`) counts as a block on page reads and schema
   calls, so the fallback model is tried. Such a page used to read as empty and
   such a call to fail validation.
6. Series-number citations ("Act No. 5") are wrapped only where the
   jurisdiction config declares them in `numbering.series_citations`; nothing
   is keyed on a country code. A config that relied on the built-in list must
   declare its series, and `Blg.` is no longer a default connector.
7. The amount checks recognise dirhams, riyals, liras and francs, with their
   Arabic and Hebrew forms, beside the currencies they read before, so the
   amount-in-words warning and the numeric extractor report amounts they
   skipped.
8. The six real jurisdiction configs 0.5.0 shipped are revised: compound
   heading terms split into a term and its aliases, enacting formulae scoped by
   document class, diacritics restored, URI patterns kept only for declared
   classes, and keys no model declares removed. A heading written in an alias
   now anchors, so output for those jurisdictions can change.

New, additive:

- Configs for 96 more jurisdictions across every region and legal tradition,
  taking the real jurisdictions shipped from 6 to 102. The registry is
  regenerated, and a test checks it against the configs on disk.

- `segmentation.act_heading_patterns` in the jurisdiction config: line-start
  regexes for a line opening an act. Where a jurisdiction declares them and
  its closing phrases, a duplicate-number fold whose dropped run holds a
  closing phrase and then an act heading raises an `act_boundary_suspected`
  span, which is blocking: the structurer refuses, or under
  `halt_policy="land"` records an `act_boundary_suspected` halt. The source
  reads as two acts numbered from 1, and keep-last kept only the later one.
  The fold itself is unchanged.
- `ingest-one --fallback-model`, defaulting to
  `LITELLM_CONTENT_FILTER_FALLBACK_MODEL`: the model retried on any
  content-filter refusal, so page reads, metadata and body-fill can all run on
  it.
- `validate_akn` takes `provenance`, `unreadable_pages` and `body_fill`;
  `codify.jurisdictions.CONFIG_FAULTS`; `codify.core.llm.content_filtered`.
- `document_classes.<class>.number_source` in the jurisdiction config:
  `"stated"` (the default, unchanged behaviour) or `"title_identity"`, which
  numbers the class's work URI from its title digest (`t-...`) and ignores any
  extracted number, for classes whose serials repeat across issuers and so
  collide. Declaring it requires `frbr.title_identity`.
- `numbering.series_citations` and `numbering.series_citation_connectors` in
  the jurisdiction config (break 6).
- A same-document anchor with no stored row of its own resolves to the nearest
  stored row: the element's own text row, its first stored part, or the row
  enclosing it, never a part of quoted text or a placeholder. `ResolveStats`
  counts `resolved_container` and `resolved_enclosing`; `nearest_stored_row`
  and `element_index` are public. Unresolved rows carry no resolver version, so
  the next pass re-examines them.
- The EUR-Lex HTML lane reads older acts whose markup leaves the opening
  paragraph unclosed; they used to fail with no article blocks.
- Migration 0021 adds `static_site_export` to the run kinds; the downgrade
  refuses while rows of that kind exist.

Repository: tests and examples that used jurisdictions the repository does not
ship run on the fictional ones; CI runs the integration suite against Postgres;
a shorter README with usage and interface guides; simplified notebooks; a
corpus ownership design; Dependabot skips TypeScript and `@types/node` major
bumps. Dependencies: pydantic-ai 2, with the repair agent keeping its early
end; openai 3; google-genai 2.

## 0.5.0 (2026-09-20)

One structuring change, so the minor moves as `VERSIONING.md` prescribes while the
major is zero; the rest is additive.

Breaks, in that the structurer's output changes for documents it already read:

1. The anchor scan reads marginal-note layouts: where a jurisdiction declares
   `display.heading_type: marginal_note`, a bare basic-unit number under a
   heading line (the shape a scanned gazette transcribes to) is a section, with
   that line as its heading. The coverage denominator counts the same markers, so
   a scan that lost them reads below 1.0 instead of measuring nothing. The
   bundled `xa` scan now structures its twenty sections rather than six parts.

New:

- A web app over the server, `apps/web`: laws, reader, search, jurisdictions and
  ingest with a live run stream; types generated from `contract/openapi.json`.
- Two reader routes on the server, `/versions/{version_id}/document` and
  `/laws/{law_id}/versions`; the run stream carries a `stored` event with the new
  version and law ids; search matches carry law and version context.
- Notebooks under `docs/notebooks/`: Codify 101, 201, 301 and 301a.

Repository: the publish action reads current metadata; Dependabot version
updates with a release cooldown; pinned actions advanced; pypdf 6.18.1.

## 0.4.0 (2026-09-18)

The first published release. Everything after the bundle: a store, a server, a
contract and an MCP surface, over the library that was already there. No
structuring behaviour changes. Under `VERSIONING.md` the minor moves while the
major is zero.

New commands:

- `codify load bundle/ --embed` writes a bundle, or a bare AKN file, into
  Postgres and embeds its provisions; `codify search` runs the hybrid retrieval
  over them; `codify compare` aligns two documents or two stored versions.
- `codify serve` is a thin HTTP server over the same library calls: ten read and
  run routes with in-memory runs and an SSE stream, `--openapi` prints the
  schema, and `contract/openapi.json` is the committed copy a drift test guards.
- `codify mcp` exposes seven read tools to an MCP client over stdio or
  streamable HTTP, behind the `mcp` extra.

Library, additive:

- `storage.get_version` and `list_versions` take `with_akn=False` for a
  metadata read that leaves the body unloaded; `get_version_akn_length`.
- Every JSON route answers with a Pydantic model, `codify.serve.schemas`; the run
  stream is server-sent events.
- The EU fetcher resolves every hop, redirects included, through the address
  guard.

Repository: security policy, code of conduct, contributor guide, issue and pull
request templates, and the boundary decision naming structure-preserving
translation as core.

## 0.3.0 (2026-09-18)

Body grammar the jurisdiction config declares, and three behaviour changes that
apply without a declaration. Under `VERSIONING.md` the minor moves while the
major is zero.

Breaks, in that the structurer's output changes for documents it already read:

1. A doctype whose numbered provision is the `section` element now refills a
   section the model left empty, in narrower windows and then verbatim, as it
   always did for articles. 0.2.0 excluded `section` from that recovery, so a
   dropped section shipped empty.
2. A quotation no longer runs over a provision heading: a span nothing closes
   ends at the blank line before the next marker, and a marker whose number
   wraps onto the next line bounds a reversed quotation. 0.2.0 let a stray
   curly quote in a definitions block mask the provisions after it.
3. A slashed insertion after a keyword marker (`7/1`) is read as its own
   number, keyed as the parser's `sec_7-1`; 0.2.0 read it as `7` and dropped
   one of the pair as a twin. Keyword-less marker forms (`7.`) are unchanged.
4. Where a jurisdiction declares `closing_phrases`, the scan ends the body at the
   first line opening with one: markers between it and the first attachment
   caption are dropped and counted (`tail_excluded`), the span is emitted verbatim
   as `CONCLUSIONS`, and an attachment caption before it titles a body table
   rather than opening an attachment. 0.2.0 fed the span to the body-fill and
   lifted the attestation out of the last provision afterwards.

New config, additive:

- `structuring.insertion_suffixes`: words that follow a number to mark an
  inserted unit, each mapped to the ASCII form its eId carries (`5 bis`). The
  scan reads the suffix, on the same line or the next, and no longer files the
  unit as a twin of its base.
- `structuring.citation_successors`: words that, following a marker's number on
  its own line, make the line a citation run rather than a provision.
- `structuring.prose_precursors` in a script that runs words together (Thai,
  Lao, Khmer, Myanmar blocks) may follow a letter directly.
- `attachments[].prefix`: a caption that opens a longer title on the same line.

## 0.2.0 (2026-09-17)

Thirteen breaks, so the minor moves, as `VERSIONING.md` prescribes while the
major is zero. Items 1 to 11 are in year resolution, enumerated by running
every year-resolution grid cell and edge row through the 0.1.0 and 0.2.0
callers (`resolve_year`, `gregorian_year`, `resolve_descriptors`) and grouping
every differing cell; each of those lines names the input shape that triggers
it, and a stored row moves under them only where a jurisdiction with a
non-Gregorian calendar has a law stored, a stored law has a year below 1000,
or a work URI carries a one- to three-digit year segment. Items 12 and 13 are
storage changes with no input shape: migration 0020 copies every existing
`provision_embeddings` row into the partitioned table.

1. A date field the model left unlabelled is read as written; 0.1.0 converted
   its year through the jurisdiction's calendar, filing a Gregorian `1968-09-09`
   under 1425 in a Buddhist-Era jurisdiction. Converted now only where a
   declared title grammar shows the year is local.
2. `to_gregorian_year(..., month=)` read a Gregorian month of a Bikram Samvat
   year the wrong way round, filing April to December under the later year.
   Corrected against the calendar (1 Baisakh 2080 was 14 April 2023); the new
   `month_grid` keyword names the grid a month is on, defaulting to Gregorian.
3. A three-digit year is carried as a four-digit segment and stored as its
   number: `622` files under `/0622/` where 0.1.0 minted a URI
   `is_citable_work_uri` refused, and a converted year below 1000 is carried
   the same way instead of dropped.
4. The sentinels `0000` and `0001` in a year field are no year; 0.1.0 returned
   them, and `0000` crashed the document-class match.
5. A five-digit year run (`12024`, in a year or date field) is no year; 0.1.0
   converted it to `11481` and crashed the class match.
6. A date field not shaped as a date (`2024-01-01junk`, a three-digit day,
   `unknown`) states nothing: no year run and no date. 0.1.0 split it on a
   dash and converted what came first, or passed it through as the work date.
   A date-shaped value with a day the month does not hold (`2024-02-31`,
   `2511-04-31`) keeps its year run and emits no date; 0.1.0 emitted it.
7. A year field in native digits (`๒๕๑๑`) is returned in ASCII by
   `resolve_year`; 0.1.0 returned the native digits while storing `2511`.
8. A date labelled with a calendar the jurisdiction does not declare is read
   as written; 0.1.0 retried its year through the jurisdiction's own rule.
9. A work date is emitted only where its year is the URI year; 0.1.0 emitted
   `2511-09-09` beside a URI year of 2020 and `unknown` beside any year.
10. The stored year is the URI year wherever the URI has one; 0.1.0 stored
    `None` where the date field held no date while the URI took the title's
    year, and stored a raw local year where the URI took the converted one.
11. A labelled year converts by the jurisdiction's declared rule; 0.1.0 used
    the generic table for that calendar and ignored a declared epoch.
12. `provision_embeddings` is partitioned by `jurisdiction_id` and carries
    `version_id` (migration 0020): the primary key is `(id, jurisdiction_id)`,
    the unique key `(provision_id, model_id, jurisdiction_id)`, and each
    partition holds its own HNSW index, built from
    `docs/runbooks/vector-index-build.md` on a populated database. A scoped
    search reads one partition's index instead of scanning every embedding in
    scope; the dense arm's `hnsw.iterative_scan` is `relaxed_order`.
13. `upsert_embedding` reads the provision's version and jurisdiction itself.
    No partition is created at run time: a jurisdiction created after 0020
    writes to the DEFAULT partition, which has its own index, until a
    migration promotes it with `codify.storage.partitions.promote_sql`.

Three more groups of differing cells are reachable only through config fields
0.1.0 could not load, so they are capability rather than breaks: a dated line
the source states is read where a date grammar is declared; a labelled local
date converts with its month and the new-year reform where a Gregorian grid
and a reform year are declared; and such a date's month and day carry into the
work date.

## 0.1.0 (2026-09-09)

First version of the standalone library, before any published release or benchmark run.

- Standalone Python library and CLI with selected public-reference and synthetic
  jurisdiction configurations. Missing configurations raise
  `JurisdictionDataMissing`; `try_load_config` supports optional lookup.
- Acquisition, AKN structuring and validation, retrieval, comparison, translation
  and optional PostgreSQL storage are included.
- Commercial lens implementations and catalogues are excluded. Generic
  scheme-match reads accept an optional `remediation_templates` mapping; callers
  requiring enrichment must provide it explicitly.
- Test fixtures and documentation have been adapted for a standalone checkout.
  Database and provider tests remain separate from the default offline CI.

Benchmark numbers from before the split are not results for this version. The
synthetic retrieval evaluator can be run separately against a disposable
PostgreSQL database; it does not measure accuracy on a real statute book.
