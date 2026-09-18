# Jurisdiction Profile: Italy

## Legal System

- **Tradition**: Civil law (Romano-Germanic). Italy is the birthplace of Akoma Ntoso: the AKN schema was developed at CIRSFID (now CIRSDIG), University of Bologna, from the 2004 African Union project through the OASIS LegalDocML TC. Italian legislative drafting conventions directly informed the schema's article-based basic unit and the Comma/Lettera/Numero subdivision hierarchy.
- **Legal continuity**: Continuous since unification (1861). Current constitutional order established by the Costituzione della Repubblica Italiana, entered into force 1 January 1948. Pre-unification regional codes (Piedmontese, Bourbon, etc.) were progressively replaced. Some pre-constitutional legislation (Regio Decreto era) remains in force, particularly in commercial and civil procedure law.
- **Legislature**: Parlamento italiano (bicameral, equal powers: "perfect bicameralism" / bicameralismo perfetto). Bills must pass both chambers in identical text.
  - **Lower house**: Camera dei Deputati (400 members, directly elected)
  - **Upper house**: Senato della Repubblica (200 members, directly elected + life senators)
  - **No review/veto body**: The Corte Costituzionale reviews constitutionality but does not participate in the legislative process. The Consiglio di Stato provides advisory opinions on government bills but these are not binding.
- **Head of state role**: Presidential promulgation. The Presidente della Repubblica promulgates laws (Art. 73–74 Costituzione). May return a bill to Parliament with reasons once; if Parliament passes it again, the President must promulgate. Laws take effect on the fifteenth day after publication in the Gazzetta Ufficiale unless a different date is specified (vacatio legis).
- **Official languages**: Italian (sole). Constitution is in Italian only. No minority language has co-equal status at national level, though German is co-official in Alto Adige/Südtirol and French in Valle d'Aosta for regional purposes.
- **Calendar system**: Gregorian throughout. All FRBR URIs use Gregorian dates.
- **Official gazette**: Gazzetta Ufficiale della Repubblica Italiana (GU). Published by Istituto Poligrafico e Zecca dello Stato (IPZS). Five series: Serie Generale (primary legislation, decrees), Serie Speciale (EU acts, regional statutes, court judgments, contracts/bids), Supplemento Ordinario, Supplemento Straordinario. Numbered by year (Anno) and issue number (Numero). Available online at gazzettaufficiale.it. Laws are uniquely identified by type + year + sequential number within that year (e.g., "Legge 7 agosto 1990, n. 241").

## Digital Presence: Tier 1

Tier 1 for official sources. normattiva.it (managed by IPZS under Presidenza del Consiglio dei Ministri) is the official legal database, provides AKN-compatible XML for all legislation since 1946 and selected historical legislation. It is the primary source of Italy's AKN implementation in practice. EUR-Lex covers all EU acts in Italian. Italy was an early AKN pilot jurisdiction; CIRSFID produced the initial legislative XML work that became the AKN standard.

## ISO Codes

- **Country**: `it` (ISO 3166-1 alpha-2)
- **Languages**: `ita` (ISO 639-3)

## Numbering Conventions

- **Legge (ordinary law) numbering**: Format `legge {day} {month name} {year}, n. {number}` (e.g., "Legge 7 agosto 1990, n. 241", "Legge 27 luglio 2000, n. 212"). The number is sequential within the calendar year. Citation typically uses type + year + number: "L. 241/1990". Official Gazzetta Ufficiale designation: "LEGGE {date}, n. {number}".
- **Decreto Legislativo**: Format `decreto legislativo {day} {month} {year}, n. {number}` (e.g., "D.Lgs. 30 marzo 2001, n. 165"). Sequential within year. Issued under Parliamentary delegation (deleghe legislative, Art. 76 Costituzione).
- **Decreto Legge**: Format `decreto-legge {day} {month} {year}, n. {number}` (e.g., "D.L. 23 febbraio 2020, n. 6"). Sequential within year. Emergency decree with immediate force (Art. 77 Costituzione); lapses after 60 days if not converted to law.
- **Decreto del Presidente della Repubblica (D.P.R.)**: Format `D.P.R. {day} {month} {year}, n. {number}`. Regulatory decrees.
- **Codici (Codes)**: Large consolidated codes are typically enacted by Decreto Legislativo and referred to by short name: Codice Civile (R.D. 16 marzo 1942, n. 262), Codice Penale (R.D. 19 ottobre 1930, n. 1398), Codice di Procedura Civile, Codice di Procedura Penale (D.P.R. 22 settembre 1988, n. 447), Codice dei contratti pubblici, etc.
- **Article numbering**: Continuous Arabic numerals throughout the document (e.g., Art. 1, Art. 2, … Art. 241). In codes, articles may number in the hundreds. Cross-references cite "articolo {N}" or "art. {N}".
- **Comma numbering**: Arabic numerals (1, 2, 3), cited inline as "comma 1" or within the article text. Not in parentheses in Italian style: the numeral appears at the start of the comma text followed by a period: "1. Il presente decreto...".
- **Lettera numbering**: Lowercase letters followed by a closing parenthesis: a), b), c). Cited as "lettera a)".
- **Numero numbering**: Arabic numerals followed by a closing parenthesis: 1), 2), 3). Cited as "numero 1)". Appears within lettere.
- **Dual numbering (Codici)**: Articles in codes often have "bis", "ter", "quater" suffixes for inserted articles (e.g., Art. 2-bis, Art. 2-ter, Art. 12-quater, Art. 96-bis). This is the standard Italian insertion numbering convention.

## FRBR URI Patterns

- **Legge**: `/akn/it/act/{year}/{number}`
- **Decreto Legislativo**: `/akn/it/act/dl/{year}/{number}`
- **Decreto Legge**: `/akn/it/act/dl_emergency/{year}/{number}`
- **Decreto del Presidente della Repubblica**: `/akn/it/act/dpr/{year}/{number}`
- **Regio Decreto (pre-republican, still in force)**: `/akn/it/act/rd/{year}/{number}`
- **Legge Regionale**: `/akn/it-{region-code}/act/{year}/{number}` (e.g., `/akn/it-lom/act/...` for Lombardia)
- **Bills (Disegno di Legge, DDL)**: `/akn/it/bill/{year}/{number}` (using the parliamentary bill number)
- **Costituzione**: `/akn/it/act/constitution/1948/constitution`
- **Codice Civile**: `/akn/it/act/rd/1942/262`
- **Codice Penale**: `/akn/it/act/rd/1930/1398`

### Sample URIs (from examined documents)

| Document                                                                  | URI                                          |
| ------------------------------------------------------------------------- | -------------------------------------------- |
| Legge 7 agosto 1990, n. 241 (Legge sul procedimento amministrativo)       | `/akn/it/act/1990/241`                       |
| Legge 27 luglio 2000, n. 212 (Statuto del contribuente)                   | `/akn/it/act/2000/212`                       |
| D.Lgs. 30 marzo 2001, n. 165 (Norme generali sull'ordinamento del lavoro) | `/akn/it/act/dl/2001/165`                    |
| D.L. 23 febbraio 2020, n. 6 (COVID emergency measures)                    | `/akn/it/act/dl_emergency/2020/6`            |
| Codice Civile (R.D. 16 marzo 1942, n. 262)                                | `/akn/it/act/rd/1942/262`                    |
| Codice Penale (R.D. 19 ottobre 1930, n. 1398)                             | `/akn/it/act/rd/1930/1398`                   |
| Costituzione della Repubblica Italiana                                    | `/akn/it/act/constitution/1948/constitution` |
| Legge Cost. 18 ottobre 2001, n. 3 (Regional powers reform)                | `/akn/it/act/cost/2001/3`                    |

**URI construction rules**:

- Leggi ordinarie (primary legislation): `{year}` = year of enactment (from GU date), `{number}` = sequential number within year. E.g., Legge n. 241/1990 → `/akn/it/act/1990/241`.
- Subtypes (`dl`, `dl_emergency`, `dpr`, `rd`, `cost`) distinguish instrument classes. Do not conflate: a D.Lgs. and a Legge with the same number in the same year are different instruments.
- Regio Decreti (R.D.) enacted before the Republic (pre-1948): use `rd` subtype. These remain in force in large numbers (civil code, penal code, etc.).
- Leggi Costituzionali: use `cost` subtype: these amend the Costituzione and require absolute majority + referendum if not passed by two-thirds.
- Regional laws use the ISO 3166-2:IT region code as locality suffix (e.g., `it-lom` for Lombardia, `it-laz` for Lazio, `it-sic` for Sicilia).

## Hierarchy Mapping

| Local Term               | AKN Element              | Level           | eId Abbreviation | Notes                                                                                                                                                                                                                                                                        |
| ------------------------ | ------------------------ | --------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Parte                    | `part`                   | Higher division | `part`           | Used in large codes. Roman numeral: Parte I, Parte II. Not common in shorter statutes.                                                                                                                                                                                       |
| Libro                    | `book`                   | Higher division | `book`           | Used in major codes (Codice Civile has 6 Libri). Roman numeral: Libro I, Libro II. Highest structural level in Italian codes.                                                                                                                                                |
| Titolo                   | `title`                  | Higher division | `title`          | Below Libro (or at top level). Roman numeral: Titolo I, Titolo II. Very common in both codes and statutes.                                                                                                                                                                   |
| Capo                     | `chapter`                | Higher division | `chap`           | Below Titolo. Roman numeral: Capo I, Capo II. "Capo" (head) is the Italian equivalent of Chapitre/Chapter.                                                                                                                                                                   |
| Sezione                  | `section`                | Higher division | `sec`            | Below Capo. Roman numeral or Arabic. Used in codes as a grouping above articles; NOT the basic unit. Critical: in Italian law, Sezione is a higher division, not the basic unit.                                                                                             |
| Articolo                 | `article`                | **Basic unit**  | `art`            | Arabic numeral, continuous throughout act. "Art. 5" or "articolo 5". The fundamental independently citable provision. Inserted articles: "Art. 2-bis", "Art. 12-quater".                                                                                                     |
| Comma                    | `paragraph`              | Subdivision L1  | `para`           | Arabic numeral followed by period: "1. Testo...": numbered subdivisions within articles. Primary subdivision in Italian law. Cited as "comma 1" or "art. 5, comma 1". Maps to AKN `paragraph` (NOT `subsection`: commas are numbered but function as unnumbered paragraphs). |
| Lettera                  | `point`                  | Subdivision L2  | `point`          | Lowercase letter + closing paren: a), b), c). List items within a comma or article. Cited as "lettera a)". Maps to AKN `point`.                                                                                                                                              |
| Numero                   | `indent`                 | Subdivision L3  | `indent`         | Arabic numeral + closing paren: 1), 2), 3). Sub-items within a lettera. Maps to AKN `indent`.                                                                                                                                                                                |
| Allegato                 | `attachment`             | Attachment      | `att`            | Schedules/annexes. Named: Allegato 1, Allegato A, Allegato tecnico. Wrapped in `<attachments><attachment>`.                                                                                                                                                                  |
| Rubrica                  | `heading`                | Inline          | N/A              | Section/article heading (marginal-note equivalent). Often present for articles in codes: "Art. 1 — Oggetto della legge". Maps to `<heading>` within `<article>`. Not all statutes have rubriche.                                                                             |
| Epigrafe                 | `longTitle`              | Preface         | N/A              | The act's full descriptive title at the top: "Norme in materia di procedimento amministrativo e di diritto di accesso ai documenti amministrativi". Inside `<preface><longTitle>`.                                                                                           |
| Formula di promulgazione | `formula`                | Preamble        | N/A              | Presidential promulgation formula. Inside `<preamble><formula name="enactingFormula">`.                                                                                                                                                                                      |
| Nota                     | `hcontainer name="nota"` | Subdivision     | N/A              | Explanatory notes (note a piè di pagina) in the GU do not form part of the enacted text. Omit from AKN body or mark as `<note>`. Do not confuse with the substantive text of the article.                                                                                    |

## eId Conventions

- **Basic unit abbreviation**: `art` (for articolo)
- **Insertion numbering**: Italian uses Latin-derived ordinal suffixes: bis (2nd), ter (3rd), quater (4th), quinquies (5th), sexies (6th), septies (7th), octies (8th), novies (9th), decies (10th). These appear hyphenated after the article number: Art. 2-bis, Art. 12-quater. eId: `art_2-bis`, `art_12-quater`. No full renumbering on amendment.
- **Comma eIds**: Commas are numbered within each article. eId follows: `art_5__para_1`, `art_5__para_2`. Comma 1 of Art. 5 = `art_5__para_1`.
- **Lettera eIds**: `art_5__para_1__point_a`, `art_5__para_1__point_b`.
- **Numero eIds**: `art_5__para_1__point_a__indent_1`.
- **Example eId chains**:

```
Codice Civile, Libro I, Titolo I, Capo I, Art. 2, comma 1:
  book_1__title_1__chap_1__art_2__para_1

Legge 241/1990, Capo I, Art. 3, comma 1, lettera a):
  chap_1__art_3__para_1__point_a

Art. 2-bis, comma 2, lettera b), numero 1):
  art_2-bis__para_2__point_b__indent_1

Allegato 1 (first schedule):
  att_1

Art. 5 of Allegato 2:
  att_2__art_5
```

### wId for Amendments

Italian law makes heavy use of bis/ter insertion numbering. When Art. 2-bis is inserted into a statute by a subsequent amendment, the consolidated version records:

- `eId="art_2-bis"` with `wId="art_2-bis"` (newly inserted, wId = eId)

When an existing article's comma is replaced, the article's eId is stable but the comma content changes. AKN `<mod>` with `<quotedText>` records the change at the comma level.

## Enacting Formula

**Current (Republic, from 1 January 1948):**

For Legge ordinaria (enacted by Parliament):

> "La Camera dei deputati ed il Senato della Repubblica hanno approvato;
> IL PRESIDENTE DELLA REPUBBLICA
> Promulga
> la seguente legge:"

For Decreto Legislativo (delegated legislation):

> "IL PRESIDENTE DELLA REPUBBLICA
> Visti gli articoli 76 e 87 della Costituzione;
> Vista la legge {delegation act reference} recante delega al Governo per…;
> Vista la deliberazione del Consiglio dei ministri, adottata nella riunione del {date};
> Sulla proposta del Presidente del Consiglio dei ministri e del Ministro {responsible minister};
> Emana
> il seguente decreto legislativo:"

For Decreto Legge (emergency decree):

> "IL PRESIDENTE DELLA REPUBBLICA
> Visti gli articoli 77 e 87 della Costituzione;
> Ritenuta la straordinaria necessità ed urgenza di {subject};
> Vista la deliberazione del Consiglio dei ministri, adottata nella riunione del {date};
> Sulla proposta del Presidente del Consiglio dei ministri e del Ministro {responsible minister};
> Emana
> il seguente decreto-legge:"

**Pre-republican (Regio Decreto, pre-1948):**

> "VITTORIO EMANUELE III
> per grazia di Dio e per volontà della Nazione RE D'ITALIA
> …
> Abbiamo decretato e decretiamo:"

**Pipeline note**: Formula type must be selected based on document type. All current-era formulae are in Italian. The promulgation block appears at the top before the body, after any preamble (Premesse/Visti/Considerati).

## Amendment Patterns

- **Style**: Textual amendment (novelle legislative). Italian law uses precise textual substitution: replacing specific phrases, commas, lettere, or entire articles.
- **Amendment act type**: Usually a Legge ordinaria or Decreto Legislativo. Decreto Legge may also amend (with conversion law ratifying the amendments).
- **Typical phrasing** (observed in normattiva.it amendment texts):
  - Comma substitution: "Il comma {N} dell'articolo {N} della legge {reference} è sostituito dal seguente: «{replacement text}»"
  - Comma insertion: "Dopo il comma {N} dell'articolo {N} è inserito il seguente: «{new text}»"
  - Article insertion (bis/ter): "Dopo l'articolo {N} è inserito il seguente: «Art. {N}-bis. {heading} - {text}»"
  - Lettera substitution: "La lettera {x}) del comma {N} dell'articolo {N} è sostituita dalla seguente: «{x}) {new text}»"
  - Word/phrase substitution: "Le parole: «{old text}» sono sostituite dalle seguenti: «{new text}»"
  - Repeal: "L'articolo {N} è abrogato." / "Il comma {N} dell'articolo {N} è soppresso."
- **Cross-reference format**: "articolo {N} della legge {day} {month} {year}, n. {number}" or abbreviated "articolo {N} della legge n. {number}/{year}". Codici cited by name: "articolo {N} del codice civile".
- **Quoted text format**: Replacement text enclosed in guillemets (« »), the standard Italian quotation marks in legal texts. Not double curly quotes.
- **AKN mapping**: Novelle map to `<mod>` with `<quotedText>` (for text/comma substitutions) or `<quotedStructure>` (for structural replacements such as whole articles or parts). The guillemet-enclosed text becomes the content of the quoted element.
- **Conversion law (legge di conversione)**: When a D.L. is converted, the conversion law may amend the D.L. text. The converted D.L. is the authoritative consolidated text; normattiva.it maintains the integrated ("coordinato") version. Model the conversion law as a separate FRBR work with a `<mod>` pointing to the D.L. work.

## Institutional TLCs

| Entity                                   | TLC Class       | eId                   | href                                              | showAs                                       |
| ---------------------------------------- | --------------- | --------------------- | ------------------------------------------------- | -------------------------------------------- |
| Parlamento italiano                      | TLCOrganization | `parlamento`          | `/ontology/org/it/parlamento`                     | Parlamento italiano                          |
| Camera dei Deputati                      | TLCOrganization | `camera`              | `/ontology/org/it/camera-dei-deputati`            | Camera dei Deputati                          |
| Senato della Repubblica                  | TLCOrganization | `senato`              | `/ontology/org/it/senato-della-repubblica`        | Senato della Repubblica                      |
| Presidente della Repubblica              | TLCRole         | `presidente`          | `/ontology/role/it/presidente-della-repubblica`   | Presidente della Repubblica                  |
| Presidente del Consiglio dei Ministri    | TLCRole         | `presidenzeConsiglio` | `/ontology/role/it/presidente-consiglio-ministri` | Presidente del Consiglio dei Ministri        |
| Consiglio dei Ministri                   | TLCOrganization | `consiglio`           | `/ontology/org/it/consiglio-dei-ministri`         | Consiglio dei Ministri                       |
| Ministro (generic, per materia)          | TLCRole         | `ministro`            | `/ontology/role/it/ministro`                      | Ministro                                     |
| Corte Costituzionale                     | TLCOrganization | `corteCostituzionale` | `/ontology/org/it/corte-costituzionale`           | Corte Costituzionale                         |
| Consiglio di Stato                       | TLCOrganization | `consiglioDiStato`    | `/ontology/org/it/consiglio-di-stato`             | Consiglio di Stato                           |
| Corte di Cassazione                      | TLCOrganization | `corteCassazione`     | `/ontology/org/it/corte-di-cassazione`            | Corte di Cassazione                          |
| Corte dei Conti                          | TLCOrganization | `corteConti`          | `/ontology/org/it/corte-dei-conti`                | Corte dei Conti                              |
| Avvocatura dello Stato                   | TLCOrganization | `avvocaturaStato`     | `/ontology/org/it/avvocatura-dello-stato`         | Avvocatura dello Stato                       |
| Regione (generic)                        | TLCRole         | `regione`             | `/ontology/role/it/regione`                       | Regione                                      |
| Gazzetta Ufficiale                       | TLCObject       | `gazzettaUfficiale`   | `/ontology/obj/it/gazzetta-ufficiale`             | Gazzetta Ufficiale della Repubblica Italiana |
| Istituto Poligrafico e Zecca dello Stato | TLCOrganization | `ipzs`                | `/ontology/org/it/ipzs`                           | Istituto Poligrafico e Zecca dello Stato     |
| Italia                                   | TLCLocation     | `italia`              | `/ontology/place/it`                              | Italia                                       |
| Unione Europea                           | TLCOrganization | `ue`                  | `/ontology/org/eu/unione-europea`                 | Unione Europea                               |
| Codify (pipeline source)                 | TLCOrganization | `codify`              | `/ontology/org/codify`                            | Codify                                       |

## Bluebell Keyword Mapping

| Local Term | Bluebell Keyword | Notes                                                           |
| ---------- | ---------------- | --------------------------------------------------------------- |
| Parte      | `PART`           | Higher division in codes                                        |
| Libro      | `BOOK`           | Top-level division in major codes (Codice Civile, etc.)         |
| Titolo     | `TITLE`          | Very common higher division in both codes and statutes          |
| Capo       | `CHAPTER`        | Below Titolo; maps to `chapter` element                         |
| Sezione    | `SECTION`        | Higher division (NOT basic unit). Used above articles in codes. |
| Articolo   | `ARTICLE`        | Basic unit. Use `ART` as shorthand.                             |
| Comma      | `PARAGRAPH`      | Primary numbered subdivision within articles.                   |
| Lettera    | `POINT`          | Lettered list items: a), b), c)                                 |
| Numero     | `INDENT`         | Numbered sub-items: 1), 2), 3)                                  |
| Allegato   | `SCHEDULE`       | Attachments block                                               |

**Bluebell limitation note**: Bluebell uses `PARAGRAPH` for comma, but Italian commas are numbered (1, 2, 3) rather than using the unmumbered `alinea` pattern of French law. The pipeline must ensure comma numbers are preserved in the `<num>` element of each `<paragraph>` rather than treating them as unnumbered alinee.

**Prompt additions**: Italian legislation would need jurisdiction-specific `structuring.prompt_additions`, which the structurer appends to its jurisdiction context, because:

1. The basic unit is `ARTICLE` not `SECTION`
2. `SECTION` (Sezione) is a higher division above articles, not the basic unit
3. Comma notation uses Arabic numerals with period ("1. Testo..."), not parenthesised numbers "(1)" as in Anglophone subsections
4. Lettera uses "a)" not "(a)" format
5. Large codes use `BOOK` as the top level, which the base prompt does not include

## Applicable Supranational Frameworks

| Body              | Member?                                    | Direct effect?                                                                                                                                 | Subject areas affected                                                                                                                                                                                                            | Profile reference                           |
| ----------------- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| EU                | Yes (founding member, 1957 Treaty of Rome) | EU Regulations: yes, directly applicable. EU Directives: require transposition into domestic law (decreti legislativi or leggi).               | Company law, competition, consumer protection, environment, financial services, data protection (GDPR directly applicable), agriculture (CAP), customs, trade; broad areas of private and public law overlap with EU instruments. | `/akn/eu/...` (AKN4EU profile)              |
| Council of Europe | Yes                                        | ECHR: binding on Italy via CEDU (Convenzione europea dei diritti dell'uomo, ratified by L. 848/1955). Court of Human Rights judgments binding. | Human rights, fundamental freedoms, criminal procedure                                                                                                                                                                            | EU and ECHR overlap significantly for Italy |
| UN                | Yes                                        | Security Council Chapter VII resolutions: binding. GA: aspirational.                                                                           | International peace and security, sanctions                                                                                                                                                                                       | `/akn/un/...` (AKN4UN profile)              |

**EU integration note**: Italy has transposed the vast majority of EU Directives via decreti legislativi under deleghe europee (annual EU delegation laws). The "legge europea" and "legge di delegazione europea" are annual laws specifically for EU transposition; model these as ordinary legge/decreto legislativo respectively, noting the EU origin in metadata.

## Sub-national Legislative Layer

Italy's 20 regions (Regioni) have concurrent legislative power in many areas (Art. 117 Costituzione, as amended by Legge Cost. 3/2001). Five regions have special autonomy (Regioni a Statuto Speciale): Sicilia, Sardegna, Valle d'Aosta, Trentino-Alto Adige/Südtirol, Friuli-Venezia Giulia, with broader legislative competence.

FRBR URI locality codes for Italian regions (ISO 3166-2:IT, lowercase after hyphen):

| Region                | ISO code | FRBR locality |
| --------------------- | -------- | ------------- |
| Valle d'Aosta         | IT-23    | `it-vda`      |
| Piemonte              | IT-21    | `it-pie`      |
| Liguria               | IT-42    | `it-lig`      |
| Lombardia             | IT-25    | `it-lom`      |
| Trentino-Alto Adige   | IT-32    | `it-taa`      |
| Veneto                | IT-34    | `it-ven`      |
| Friuli-Venezia Giulia | IT-36    | `it-fvg`      |
| Emilia-Romagna        | IT-45    | `it-emr`      |
| Toscana               | IT-52    | `it-tos`      |
| Umbria                | IT-55    | `it-umb`      |
| Marche                | IT-57    | `it-mar`      |
| Lazio                 | IT-62    | `it-laz`      |
| Abruzzo               | IT-65    | `it-abr`      |
| Molise                | IT-67    | `it-mol`      |
| Campania              | IT-72    | `it-cam`      |
| Puglia                | IT-75    | `it-pug`      |
| Basilicata            | IT-77    | `it-bas`      |
| Calabria              | IT-78    | `it-cal`      |
| Sicilia               | IT-82    | `it-sic`      |
| Sardegna              | IT-88    | `it-sar`      |

The initial pipeline scope should target national legislation only. Regional legislation is a future scope extension.

## Document Types Present

- [x] Primary legislation (Legge ordinaria, Legge costituzionale)
- [x] Delegated legislation (Decreto Legislativo)
- [x] Emergency decrees (Decreto Legge + Legge di conversione)
- [x] Presidential regulatory decrees (Decreto del Presidente della Repubblica)
- [x] Historical royal decrees (Regio Decreto, still in force)
- [x] Codes (Codici, large consolidated acts enacted by R.D. or D.Lgs.)
- [x] Bills (Disegni di Legge, DDL government bills; Proposte di Legge, PDL private member bills)
- [x] Constitutional instruments (Legge Costituzionale)
- [x] Regional laws (Legge Regionale, future scope)
- [ ] Court judgments (separate pipeline, Corte Costituzionale sentenze are particularly significant)
- [ ] Parliamentary debates (Resoconto stenografico, Camera and Senato)

## Digitisation Maturity Target

The levels below are this project's incremental-digitisation ladder, not part of the
OASIS AKN standard, which defines its own naming-convention compliance levels.

- **Initial**: Level 1 (valid AKN structure, FRBR identification, publication metadata)
- **Target**: Level 3 (lifecycle events, amendment chains, conversion law tracking)
- **Basis**: normattiva.it already publishes the integrated ("coordinato") text with amendments applied, so its XML can be consumed directly without OCR. OCR is needed only for pre-normattiva documents and documents not yet integrated.

## Unresolved Ambiguities

1. **Decreto Legge subtype URI collision**: D.L. and D.Lgs. both abbreviated to `dl` in the proposed URI pattern. Recommend: use `dlgs` for Decreto Legislativo and `dl` for Decreto Legge. Needs a definitive decision before bulk ingest. Current proposal: `dl_emergency` for D.L., `dl` for D.Lgs.; review against normattiva.it URI conventions for alignment.

2. **Comma → AKN element mapping**: Italian commas are numbered (1., 2., 3.) and occupy the same structural role as French alinée (unnumbered paragraphs). Comparative hierarchy tables list `alinea` for French and `paragraph` for Italian at this level. However, `alinea` produces an unnumbered element in AKN, while `paragraph` expects a `<num>` element. Italian commas ARE numbered, so `paragraph` with `<num>` is the correct mapping, but the Bluebell `PARAGRAPH` keyword must be used carefully. Confirm with a Bluebell parse test.

3. **Sezione as higher division vs. basic unit**: Comparative hierarchy tables map Italian "Sezione" to `section` used as a higher division above articles. This is confirmed for codes. However, some shorter Italian statutes have no intermediate hierarchy and go directly from the act title to articles; Sezione may occasionally appear as a grouping device in these. The profile models Sezione as always a higher division. Verify against short acts without codes.

4. **Regio Decreto FRBR URIs**: Hundreds of R.D. instruments from the monarchy era remain in force. The proposed `rd` subtype places them under the current `it` country code, which is correct (they are part of Italy's legal corpus). However, the Republic government post-1948 has republished many in consolidated form. The canonical FRBR number should use the original R.D. number, not any subsequent consolidated number. Verify with normattiva.it URI patterns.

5. **Legge di conversione relationship to Decreto Legge**: When a D.L. is converted, the conversion law formally amends the D.L. text. normattiva.it produces an integrated version. AKN lifecycle modelling requires recording: (a) the original D.L., (b) the conversion law, (c) any amendments made during conversion, (d) the integrated text. This is a Level 3 concern but the URI structure must accommodate it from Level 1. Recommend: D.L. and conversion law are separate FRBR works; the integrated text is a distinct FRBR expression of the D.L. work incorporating all modifications.

6. **Rubrica (article heading) presence**: Not all Italian statutes include rubriche (article headings/rubrics). Major codes (Codice Civile, Codice Penale) have them; many shorter statutes do not. The pipeline should treat rubrica as optional: use `<heading>` when present, omit when absent. Confirmed from examining L. 241/1990 (has headings) vs. shorter fiscal statutes (often heading-free).

7. **Province/Municipal instruments**: Comuni and Province have regulatory powers (regolamenti comunali/provinciali) that are not legislation in the strict sense. These are outside scope for initial pipeline but should be noted for future modelling as `<doc>` rather than `<act>`.

## Notes

- **AKN birthplace significance**: Italy's legislative drafting practices directly shaped the AKN schema. The CIRSFID team (Monica Palmirani, Fabio Vitali et al.) developed AKN from work on Italian parliamentary documents. This means the Italian hierarchy maps with unusual fidelity to AKN elements: there are no "mapping fiction" cases where a local term has to be approximated by a non-matching AKN element.
- **normattiva.it as gold standard**: normattiva.it provides the official XML representation of Italian legislation. Importing from normattiva.it is preferable to OCR from GU PDFs. Its XML uses a dialect close to, but not identical with, AKN 3.0, so a transformation layer is required.
- **Perfect bicameralism**: Unlike most parliamentary systems, Italy's two chambers have identical legislative powers. Bills must pass both chambers in identical text; there is no fast-track or Lords-equivalent revision process. This means no "Money Bills" designation; all legislation follows the same passage route.
- **Decreti Legge and democratic legitimacy**: D.L.s are emergency decrees with immediate legal force. They expire after 60 days if not converted to law (legge di conversione). Parliament may amend the D.L. during conversion. Italian constitutional law prohibits reiteration of a lapsed D.L. (Corte Costituzionale sent. 360/1996). This creates a complex amendment chain that AKN lifecycle modelling must represent.
- **Codici and structural depth**: Italian codes can be very long: the Codice Civile has 2969 articles across 6 Libri. The full hierarchy (Libro > Titolo > Capo > Sezione > Articolo) is genuinely used. eId chains can be 5+ levels deep: `book_4__title_1__chap_3__sec_2__art_1457`.
- **Deleghe europee**: Italy regularly enacts annual "legge di delegazione europea" (granting delegation to the government to transpose EU directives by D.Lgs.) and "legge europea" (directly implementing EU obligations). These are ordinary acts in structure but have a special function: record EU origin in `<FRBRcountry>` metadata with a cross-reference to the relevant EU directive or regulation.
- **Vacatio legis**: Italian law takes effect on the 15th day after GU publication unless a different vacatio period is specified. This is relevant for `<temporalData>` modelling at Level 3.

## Research Sources

- normattiva.it: official Italian legal database (IPZS). Primary source for act text, numbering, XML structure. https://www.normattiva.it
- Gazzetta Ufficiale online: https://www.gazzettaufficiale.it
- Camera dei Deputati legislative process: https://www.camera.it/
- Senato della Repubblica: https://www.senato.it
- Costituzione della Repubblica Italiana: https://www.senato.it/istituzione/la-costituzione
- CIRSFID/CIRSDIG AKN origin documentation: University of Bologna. Monica Palmirani, Fabio Vitali. "Akoma-Ntoso for Legal Documents", Legislative XML for the Semantic Web, Springer 2011.
- Legge 7 agosto 1990, n. 241 (Legge sul procedimento amministrativo): examined directly on normattiva.it as structural reference (48 articles, Capi structure, rubriche present, comma numbering confirmed).
- D.Lgs. 30 marzo 2001, n. 165: examined for decreto legislativo structure and promulgation formula.
- Codice Civile (R.D. 16 marzo 1942, n. 262): examined for deep hierarchy (Libro > Titolo > Capo > Sezione > Articolo), bis/ter insertion numbering, Regio Decreto promulgation formula.
- AKN4EU profile documentation: EUR-Lex. For EU membership context and supranational layer mapping.
