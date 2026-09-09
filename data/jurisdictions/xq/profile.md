# Quivira

Quivira is a fictional civil-law republic. It exists only as test material and
as the worked example in `docs/jurisdictions/adding-a-jurisdiction.md`. Nothing
here describes a real place, and no text in its corpus is real legislation.

## Legal system

A unicameral National Assembly legislates by _ley_. There is one instrument
class, which is the point: Quivira is the smallest configuration that ingests,
so a contributor can see which fields are load-bearing before adding the rest.

Laws are organised into _títulos_, numbered in upper-case Roman, each holding
_artículos_ numbered continuously across the whole law rather than restarting
per título. An artículo may be divided into numbered _apartados_.

## Citation

A law is cited as `Ley {number} de {year}`. An article inserted by amendment
takes the preceding number with _bis_, then _ter_, rather than a decimal, so an
article inserted after 12 is 12 bis and not 12.1.

## Publication

Laws are published in the Gaceta de Quivira. The enacting formula is
`LA ASAMBLEA NACIONAL DE QUIVIRA DECRETA:` and closes the preamble.

## Corpus

Three laws, authored in Bluebell under `packages/codify/tests/fixtures/synthetic/xq/`.
One is amended by another, so the pair exercises the amendment and citation
path rather than only the parser.
