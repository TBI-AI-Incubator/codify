# United Kingdom (`gb`)

**Full name**: United Kingdom of Great Britain and Northern Ireland  
**Type**: National (sovereign state)  
**Legal tradition**: Common law (dominant); Scots law has a mixed civil-law element.  
**Calendar**: Gregorian  
**Languages**: English (`eng`). Welsh is co-official in Wales.

---

## Parliament

Westminster Parliament (UK Parliament) is bicameral:

- **House of Commons**: 650 elected members (MPs). Primary legislative chamber.
- **House of Lords**: approximately 800 members (Lords Spiritual, Lords Temporal, comprising life peers and remaining hereditary peers). Revising chamber; cannot permanently block Commons legislation (Parliament Acts 1911 and 1949).

Parliament legislates for the **whole UK** on reserved matters (defence, foreign affairs, immigration, most taxation, broadcasting) and for **England** (and sometimes England & Wales) on devolved matters not transferred to Edinburgh, Cardiff, or Belfast.

---

## Devolved legislatures

This configuration covers the whole statute book legislation.gov.uk publishes, devolved instruments included, through the publisher's type tokens (see FRBR URIs below). The legislatures:

| Legislature                                                                                 | Territory                     |
| ------------------------------------------------------------------------------------------- | ----------------------------- |
| No separate assembly for England alone; English-only legislation passes through Westminster | England (and England & Wales) |
| Senedd Cymru / Welsh Parliament                                                             | Wales                         |
| Scottish Parliament                                                                         | Scotland                      |
| Northern Ireland Assembly                                                                   | Northern Ireland              |

---

## Constitution

The UK has **no single codified constitution**. Constitutional arrangements are spread across statute (Magna Carta 1215, Bill of Rights 1689, Acts of Union 1707/1800, Parliament Acts 1911/1949, Human Rights Act 1998, Constitutional Reform Act 2005, Fixed-term Parliaments Act 2011, etc.), common law, and constitutional conventions. No `constitution` document class is defined; constitutional documents are modelled as `act`.

---

## Digital Presence: Tier 1

legislation.gov.uk, operated by The National Archives, publishes AKN XML natively for the great majority of UK legislation; see AKN source below.

---

## Document classes

### `act`: Act of Parliament

Primary legislation. Introduced as a Bill; receives Royal Assent from the Crown.  
Hierarchy: **Part → Chapter → Section → Subsection → Paragraph → Subparagraph**

Section is the basic unit. Parts and Chapters are grouping levels; both are optional. Schedules follow the same hierarchy internally.

**Crossheadings** appear between sections and do not carry an eId; they are presentational dividers.  
**Marginal notes** (now usually printed as side-headings or italic inline headings) describe each section; they appear in the AKN `<heading>` element.

### `si`: Statutory Instrument

Secondary legislation made under a parent Act (enabling/parent Act must be cited in the preamble). Identified by year and SI number (e.g. SI 2023/1234). The basic unit is typically "article" (for Orders) or "regulation" (for Regulations), both mapped to AKN `section`.

---

## Enacting formulae

Three variants are modelled:

1. **Standard (King Charles III era, from 2022-09-08)** (both Houses): "BE IT ENACTED by the King's most Excellent Majesty, by and with the advice and consent of the Lords Spiritual and Temporal, and Commons…"
2. **Parliament Acts procedure (King Charles III era)** (Commons only; Money Bills, delayed Bills): "…by and with the advice and consent of the Commons in this present Parliament assembled, in accordance with the provisions of the Parliament Acts 1911 and 1949…"
3. **Queen Elizabeth II era (to 2022-09-08)**: standard form with "Queen's most Excellent Majesty" and feminine pronoun.

---

## FRBR URIs

The publisher's type token is the AKN subtype, the shape EU uses for regulations and directives:

- `/akn/gb/act/{token}/{year}/{number}`, where the token is the [legislation.gov.uk](https://www.legislation.gov.uk) type (`ukpga`, `uksi`, `asp`, `ssi`, `wsi`, `nisr`, ...)
- e.g. `/akn/gb/act/ukpga/2006/46` for the Companies Act 2006 c.46, `/akn/gb/act/uksi/2023/1234` for SI 2023/1234, `/akn/gb/act/asp/2020/1` for the first Act of the Scottish Parliament of 2020

The token is part of the identity because the publisher numbers each type separately: `ukpga/2020/1` and `asp/2020/1` are different works, as are `uksi/2020/1` and `ssi/2020/1`. Each token is a document class in the config, extending `act` or `si`. Pre-1963 Acts carry a regnal citation in the publisher's path (`ukpga/Geo6/14-15/48`); the fetched document names its calendar-year work (`/id/ukpga/1951/48`) and keeps the regnal form as an alternative number.

---

## AKN source

legislation.gov.uk publishes AKN XML for the majority of UK legislation (from 1988 onwards for most types; selected older items). Append `/data.akn` to any legislation.gov.uk URL:

```
https://www.legislation.gov.uk/ukpga/2006/46/data.akn
```

A smaller example, `https://www.legislation.gov.uk/ukpga/1978/30/data.akn` (Interpretation Act 1978), returns a document rooted at `akomaNtoso` in the `http://docs.oasis-open.org/legaldocml/ns/akn/3.0` namespace, with `uk:` and `ukl:` extension namespaces and an FRBR URI under `legislation.gov.uk/id/`.

---

## Supranational memberships

| Body                                                | Since | Notes                                                        |
| --------------------------------------------------- | ----- | ------------------------------------------------------------ |
| United Nations (`un`)                               | 1945  | Founding member; P5 permanent Security Council seat          |
| Council of Europe (`council-of-europe`)             | 1949  | Founding member; ECHR incorporated via Human Rights Act 1998 |
| Commonwealth of Nations (`commonwealth-of-nations`) | 1949  | Head of the Commonwealth                                     |
| NATO (`nato`)                                       | 1949  | Founding member                                              |
| World Trade Organization (`wto`)                    | 1995  | Founding member                                              |

**EU**: The UK formally left the EU on **31 January 2020** (Brexit). The transition period ended 31 December 2020. EU membership is **not** modelled here. Retained EU law (REUL) was converted into UK domestic law by the European Union (Withdrawal) Act 2018 and subsequent instruments.

---

## Official Gazette

**The London Gazette**: published continuously since 1665. Statutory notices, Royal Proclamations, and official appointments are published here. The Edinburgh Gazette and Belfast Gazette serve Scotland and Northern Ireland respectively.

---

## Amendment conventions

UK Acts follow Commonwealth textual amendment style: substitution of words/phrases/sections with explicit "for X substitute Y" or "omit X" formulations. Cross-references use short title and year (e.g. "the Companies Act 2006"). Consolidation Acts are common.
