# New Zealand (nz): Jurisdiction Profile

## Overview

New Zealand is a unitary state with a Westminster-derived common law system and a distinctive constitutional framework. Unlike most Commonwealth nations, New Zealand has no single codified constitution. The constitutional framework is distributed across the Constitution Act 1986, the New Zealand Bill of Rights Act 1990, the Electoral Act 1993, and parliamentary conventions; the Treaty of Waitangi / Te Tiriti o Waitangi occupies a foundational role that is constitutionally unusual by any comparative standard.

The legislature is unicameral: the House of Representatives (120 seats elected by Mixed Member Proportional representation). The Legislative Council (upper house) was abolished in 1951. Parliament enacts legislation; the Governor-General grants royal assent on behalf of the Crown.

## Bicultural Legal Framework and Māori Language Status

New Zealand's legal system is formally bicultural. The Treaty of Waitangi (1840), signed between the British Crown and Māori chiefs, is treated as a foundational instrument of the constitutional order. It is not directly justiciable as supreme law, but courts interpret legislation to give effect to Treaty principles, and numerous Acts include specific Treaty of Waitangi clauses requiring decision-makers to give effect to or act consistently with Treaty principles.

Te reo Māori (the Māori language) became an official language under the Māori Language Act 1987, replaced by the Māori Language Act 2016 (Te Ture mō te Reo Māori 2016). NZ Sign Language achieved official status under the NZ Sign Language Act 2006.

**Legislative implications:**

- English is the drafting language of all legislation; it is authoritative.
- Some Acts have bilingual Māori/English titles: `Te Ture mō te Reo Māori 2016 / Māori Language Act 2016`. The Māori title should be stored as an `FRBRalias`.
- Some Acts (particularly Treaty of Waitangi legislation) include Māori-language preambles or schedules. The legal status of these Māori texts varies; the Treaty of Waitangi Act 1975 Schedule reproduces both the English and Māori texts of the Treaty, but courts have debated which text controls.
- Macrons (tohutō) must be preserved in Māori text: ā ē ī ō ū (failure to do so is linguistically incorrect and politically sensitive in NZ's bicultural context).

**The Waitangi Tribunal** (established by Treaty of Waitangi Act 1975) is a permanent commission of inquiry with quasi-judicial status. It hears claims about Crown breaches of Treaty principles. Its recommendations are not binding on the Crown but carry significant legal and political weight. It is modelled as a `TLCOrganization` in the core_tlcs.

## Part / Subpart Hierarchy: The Distinctive NZ Pattern

New Zealand Acts use a distinctive five-level hierarchy:

```
PART
  SUBPART (optional mid-level grouping within a Part)
    SECTION (basic citable unit, continuously numbered)
      SUBSECTION (1), (2), (3)...
        PARAGRAPH (a), (b), (c)...
          SUBPARAGRAPH (i), (ii), (iii)...
            ITEM (A), (B), (C)... [rare, complex tax/commercial legislation]
```

**Subpart** is the key NZ-distinctive element. It functions similarly to Australian `Subdivision` or UK `Chapter` within a Part, but uses the term "Subpart". The Bluebell parser supports `SUBPART` natively (maps to AKN `<subpart>`), so no `hcontainer` workaround is needed.

Not all Acts use Subparts. The Companies Act 1993 and Income Tax Act 2007 use them extensively; the Treaty of Waitangi Act 1975 and shorter Acts go PART > SECTION directly. The structuring pipeline must detect which pattern applies from the source document.

Sections are numbered continuously throughout the Act; they do not restart within each Part or Subpart. This is consistent with other Westminster-tradition Anglophone jurisdictions.

## Plain-Language Drafting Style

New Zealand's Parliamentary Counsel Office (PCO) has pioneered a "plain-language drafting" approach since the early 1990s, formalised in their drafting guidelines. Key features:

- **Purpose sections**: Modern Acts typically open with a "Purpose" section (often section 3 or 4) stating the Act's objects in plain language. This has interpretive significance under section 5 of the Interpretation Act 1999.
- **Outline sections**: Many complex Acts include an "Overview" or "Guide" section describing the Act's structure in narrative form. These are not substantive provisions but are part of the enacted text.
- **Short sentences, active voice**: PCO guidelines emphasise sentences under 40 words, active voice, and avoidance of legal archaisms.
- **No provisos**: Modern NZ drafting avoids the "Provided that..." proviso construction. Provisos do appear in older legislation (pre-1990s) and are mapped to the `proviso` hcontainer.

## Digital Presence: Tier 1

The Parliamentary Counsel Office publishes New Zealand legislation through [legislation.govt.nz](https://www.legislation.govt.nz), which is the authoritative source for current consolidated law. This is effectively a **Tier 1** digital access jurisdiction.

**Key capabilities:**

- Free public access to all Acts, Legislative Instruments, and Bills
- Machine-readable XML download available for all instruments
- Point-in-time versions showing the law as it stood on any given date
- Amendment history showing every change with source Act/instrument
- Structured metadata including enactment date, commencement, and reprinting history

**XML structure:** The legislation.govt.nz XML uses its own PCO schema (not AKN-native), but it is richly structured with elements for Part, Subpart, Section, cross-references, and definitions. Direct AKN integration is preferred over PDF OCR for modern NZ legislation; the PDF pipeline should be reserved for historical legislation predating the digital archive or for documents obtained outside legislation.govt.nz.

**URL pattern:** `https://legislation.govt.nz/act/public/{year}/{number}/latest/whole.html` for viewing; XML via the legislation.govt.nz API or bulk download.

## Bluebell Compatibility

All core NZ hierarchy levels have native Bluebell keywords:

| NZ Term      | Bluebell Keyword | AKN Element    | Notes                        |
| ------------ | ---------------- | -------------- | ---------------------------- |
| Part         | `PART`           | `part`         |                              |
| Subpart      | `SUBPART`        | `subpart`      | Native NZ-compatible keyword |
| Section      | `SECTION`        | `section`      | Basic citable unit           |
| Subsection   | `SUBSECTION`     | `subsection`   |                              |
| Paragraph    | `PARAGRAPH`      | `paragraph`    |                              |
| Subparagraph | `SUBPARAGRAPH`   | `subparagraph` |                              |
| Item         | `POINT`          | `point`        | Rare; uppercase letters      |
| Proviso      | `PROVISO`        | (native)       | Older legislation only       |

No `hcontainer` workarounds required for standard NZ hierarchy. The `SUBPART` keyword is the key differentiator from UK/Australian profiles that must use `hcontainer` for equivalent groupings.

## Edge Cases

### 1. Bilingual Māori/English Titles

Some Acts have official Māori titles alongside their English titles. The Māori title should be stored as `<FRBRalias>` in the document's FRBR metadata block. The English title is the primary `<FRBRname>`. Example:

- FRBRname: `Māori Language Act 2016`
- FRBRalias: `Te Ture mō te Reo Māori 2016`

### 2. Treaty of Waitangi Preambles

Acts that affect Māori rights frequently include a Treaty of Waitangi preamble or purpose clause. These are part of the enacted text and carry interpretive weight; they are not recitals to be stripped. They should be mapped to `<preamble>` in the AKN structure. Some Acts also reproduce text from the Treaty itself in a Schedule.

### 3. Waitangi Tribunal's Quasi-Judicial Role

The Waitangi Tribunal issues reports (not judgments). Its findings are not court decisions, but they are cited extensively in Treaty litigation and administrative reviews. For AKN purposes, Tribunal reports are document class `doc` (not `act`); they are not profiled here.

### 4. Schedule Hierarchies

NZ Schedules often contain their own structural hierarchy (Part/clause) that is independent of the main body's section numbering. Some Schedules contain entire secondary instruments, tables, or forms. Schedule clauses are numbered with `cl` prefix in eIds. This requires pipeline handling to distinguish main body sections from Schedule clauses.

### 5. Reprinted vs. Principal Acts

legislation.govt.nz publishes "reprinted" (consolidated) versions with amendment tracking. The reprinted version is the working law but is not separately enacted; it is an editorial consolidation by the PCO. The FRBR Work URI refers to the original principal Act; the latest expression represents the consolidated version. Amendment tracking uses `<activeModifications>` / `<passiveModifications>` in AKN.

### 6. Deemed Regulations

A distinct NZ category: instruments that are not legislative instruments under the Legislation Act 2019 but are given the same effect by other legislation. These require case-by-case classification.

## Courts Hierarchy

- Supreme Court of New Zealand (established 2004; replaced Privy Council as final appellate court)
- Court of Appeal
- High Court
- District Court
- Specialist courts: Environment Court, Employment Court, Māori Land Court, Family Court

The Privy Council (London) was the final appellate court until 2003; Privy Council judgments on NZ law from before that date remain persuasive precedent.

## Key Sources Consulted

- legislation.govt.nz: Parliamentary Counsel Office; authoritative source for NZ legislation
- New Zealand Parliament: parliamentary.nz: legislative process and Bills
- Parliamentary Counsel Office drafting guidelines (publicly available)
- Te Arawhiti (Office for Māori Crown Relations): Treaty of Waitangi framework
- NZ Gazette (gazette.govt.nz): official gazette for regulations and notices
- Companies Act 1993 (NZ): example of Part > Subpart hierarchy
- Resource Management Act 1991: example of complex environmental Act with Part structure
- Māori Language Act 2016 / Te Ture mō te Reo Māori 2016: bilingual title example
- Treaty of Waitangi Act 1975: Treaty preamble and Waitangi Tribunal
- Income Tax Act 2007: extensive Subpart use in large fiscal legislation
