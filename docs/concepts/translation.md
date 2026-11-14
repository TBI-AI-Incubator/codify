---
title: Translation
description: How Codify's translation of legislation compares with published United Nations translation practice, where the parallels hold and where Codify stops short.
sidebar:
  order: 5
---

This page explains the design of Codify's translation of legislation by setting it beside the published practice of the United Nations. The comparison is Codify's own; the United Nations had no part in Codify. [Translate](../guide/translate.md) shows the steps.

## The core idea

A translated law is useful only if it can be cited and compared. A reader must be able to point at section 5(1) in French and know it is section 5(1) in English, and a defined term must read the same in every provision. A translation that loses either can be read but not cited. Codify therefore puts two constraints on the model: keep every provision's address, and use controlled terms. The United Nations meets comparable needs with translators, editors and tools.

## Parallels

### One identity across languages

**At the United Nations.** Every UN document has a symbol common to all its language versions, and one publication date that applies to all of them ([Ziemski, Junczys-Dowmunt and Pouliquen, 2016](https://aclanthology.org/L16-1561/), section 4). A reader finds the French version of a document by its symbol.

**In Codify.** A translation is a new [expression](frbr.md#work-expression-manifestation) of the same work. It keeps the work URI and the source's date and changes only the language (`codify/translate/write.py`, `_patch_identification`). The work URI plays the part of the UN symbol.

![One work, /akn/xa/act/1992/7, the Legislation Act, 1992, with one URI for its whole life, and its expressions on a date axis. From 1992-05-02, the English expression /akn/xa/act/1992/7/eng@1992-05-02, as enacted, its URI fixed and still citable, in the file final.akn.xml. From 2012-01-01, the English expression /akn/xa/act/1992/7/eng@2012-01-01, the current text, amended by /akn/xa/act/2011/5, in the file consolidated-2012.akn.xml. Translated from it and stored as its child, the French expression /akn/xa/act/1992/7/fra@2012-01-01, with the same date, not authoritative and never current, in the file translated-fra.akn.xml. Each box is one expression, one text in one language from one date; its shaded strip is its file, a manifestation.](../figures/v5-family-tree.svg "One law, three dated texts in two languages, each saved as a file; identifiers and file names are from the worked example and its amendment and translation steps.")

### Alignment by provision

**At the United Nations.** The United Nations Parallel Corpus pairs documents by symbol, keeps their paragraph structure, and adds sentence alignment afterwards with statistical aligners. It also publishes a subcorpus aligned across all six official languages ([Ziemski et al., 2016](https://aclanthology.org/L16-1561/)). Alignment is recovered after translation.

**In Codify.** Alignment is fixed before translation. Codify walks the source into units keyed by [eId](akoma-ntoso.md#identifiers-eid-and-wid), the model returns text per unit, and the writer patches that text into a copy of the source (`codify/translate/anchors.py`, `walk_source`; `codify/translate/write.py`, `apply_translation_to_akn`). The model copies each eId back with its text and cannot add or remove elements, so `part_I__sec_1` names the same provision in both languages by construction. The unit is the provision, not the sentence.

![Across languages, a translation: the eId part_I__sec_1 names section 1 in two expressions of the Legislation Act, 1992. In the English source, /akn/xa/act/1992/7/eng@2012-01-01, it is headed Short title and commencement, with (1) This Act may be cited as the Legislation Act, 1992. In the French text, /akn/xa/act/1992/7/fra@2012-01-01, which is not authoritative, it is headed Titre abrégé et entrée en vigueur, with (1) La présente loi peut être citée sous le titre : Legislation Act, 1992. The French was written by hand for the example, not by a model. The French text records its source in FRBRtranslation, with fromLanguage eng, href /akn/xa/act/1992/7/eng@2012-01-01, by #codify and authoritative false.](../figures/v7-translate.svg "One eId names the same provision in a source text and its translation; texts are from the worked example and its translation step, with the French written by hand.")

### Terminology fixed first

**At the United Nations.** Translators standardise terms across the six official languages, keep series of documents consistent, and store verified terms in UNTERM, the multilingual terminology database ([DGACM, Translation](https://www.un.org/dgacm/en/node/1133)). Editors apply standard UNTERM terms and maintain its English entries ([DGACM, Editing](https://www.un.org/dgacm/en/node/1136)).

**In Codify.** Before any provision is translated, one model call reads the whole act and returns binding notes: defined terms, terms of art and named entities, which every later call receives (`codify/translate/notes.py`, `build_translation_notes`). The prompt states that it follows the practice of the EU and UN translation services. Terms you supply as *pinned terms* replace the model's entry for the same source term (`apply_pinned_terms`). After translation, the audit lists every defined term whose target form is missing from the output (`codify/translate/audit.py`). It matches the exact string, so an inflected form can be flagged in error.

### Reuse of agreed text

**At the United Nations.** eLUNa, the web-based translation tool at the core of the gText project, identifies previously translated sentences and terminology, reuses earlier translations, links to UN documents in bilingual form, and offers machine translation for new sentences ([DGACM, gText](https://www.un.org/dgacm/en/node/1475)).

**In Codify.** Reuse is narrower. Within one chapter, batches run in order, and later batches receive up to three earlier provisions that state an obligation, in source and target, as agreed examples to keep the drafting register consistent (`codify/translate/exemplars.py`, `codify/translate/batching.py`). This works only for English, Arabic and Hebrew sources. *Regression pins* hold a reviewer's correction: a phrase that every later translation of a provision must contain, checked by `python -m codify.translate.regression_pins` (`codify/translate/regression_pins.py`). Pins live in files shipped with the package, under `codify/translate/pins/`, and `translate_document` does not check them; you run the check.

### Which text is authentic

**At the United Nations.** The Charter's Chinese, French, Russian, English and Spanish texts are equally authentic ([Charter, Article 111](https://www.un.org/en/about-us/un-charter/chapter-19)). Under the [Vienna Convention on the Law of Treaties](https://legal.un.org/ilc/texts/instruments/english/conventions/1_1_1969.pdf), Article 33, a treaty authenticated in several languages is equally authoritative in each unless the treaty or the parties make one text prevail, and a version in another language is authentic only if the treaty or the parties say so. The UN has six official languages: Arabic, Chinese, English, French, Russian and Spanish ([Official languages](https://www.un.org/en/our-work/official-languages)).

**In Codify.** Codify's translations are machine translations with no legal authority. The expression says so in its own identification block, with the element Akoma Ntoso provides for it: `<FRBRtranslation>`, marked `authoritative="false"`, as in the figure under [Alignment by provision](#alignment-by-provision).

Stored as a child of the source version, a translation never becomes the law's current version, so a request without a language is answered from the source text (`codify/storage/versions.py`, `latest_version_for_law`). Each translation records a hash of the source it was made from, and `stale_translation_ids` reports translations whose source has since changed.

## Limits

- The core has no step for a person to revise the output. Quality control is a deterministic audit and an optional model judge that scores sampled provisions (`codify/translate/judge.py`). Have a qualified legal translator revise any text you publish.
- There is no terminology database. The glossary is built per act and per run; pinned terms are passed in by the caller and are not stored.
- There is no translation memory. Codify does not look up earlier translations of the same sentence in other laws.
- Alignment works only where the source has eIds. An element without one is skipped and flagged.
- Every translation is marked `authoritative="false"` and attributed to `#codify`. There is no parameter to record an official translation or a human translator.

## Status

| Capability | Status |
| --- | --- |
| Translate a document by eId, with a binding glossary and pinned terms | **Library only**: `codify.translate.translate_document` |
| Declare language and source in the FRBR identity | **Library only**: written by `codify.translate.write.apply_translation_to_akn` |
| Detect a stale translation | **Library only**: `codify.storage.stale_translation_ids` |
| Check regression pins | **Available**: `python -m codify.translate.regression_pins` |
| A shared terminology database or translation memory | **Not in the core** |

## Next

[AKN profiles and known differences](akn4eu-divergences.md): the choices Codify makes inside Akoma Ntoso 3.0.
