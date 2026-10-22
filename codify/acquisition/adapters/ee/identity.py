"""One Riigi Teataja act's identity, derived the same way by every path that reads its source.

The work URI slugs the act's full title. The abbreviation (`lyhend`) is not used: one
redaction of an act can carry it while another does not, which would split the act
across two works.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lxml import etree

from codify.frbr import build_frbr_work_uri

JURISDICTION = "ee"
CONSTITUTION_TITLE = "Eesti Vabariigi põhiseadus"
_CONSTITUTION_ABBREVIATION = "PS"
_CONSTITUTION_DOCUMENT_TYPE = "põhiseadus"
_ROMAN = str.maketrans("äöüõšž", "aouosz")


def romanise(text: str) -> str:
    """Romanise Estonian text for FRBR URI slugs per ee/config.json."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().translate(_ROMAN)).strip("-")
    return slug or "seadus"


def is_constitution(document_type: str, abbreviation: str, title: str) -> bool:
    """The constitution by its type, abbreviation or exact title. A title that only names
    it, as an amending act or `põhiseadusliku korra` does, belongs to another act."""
    return (
        document_type.strip().lower() == _CONSTITUTION_DOCUMENT_TYPE
        or abbreviation.strip().upper() == _CONSTITUTION_ABBREVIATION
        or " ".join(title.split()).casefold() == CONSTITUTION_TITLE.casefold()
    )


def classify(document_type: str, abbreviation: str, title: str) -> str:
    """`constitution`, `maarrus` for a regulation, else `act`."""
    if is_constitution(document_type, abbreviation, title):
        return "constitution"
    return "maarrus" if document_type.strip().lower() == "määrus" else "act"


def work_uri(doctype: str, year: int | str, title: str) -> str:
    """The act's work URI. The jurisdiction's pattern fixes the constitution's."""
    return build_frbr_work_uri(JURISDICTION, doctype, year, romanise(title))


@dataclass(frozen=True)
class ActIdentity:
    title: str
    doctype: str
    enacted: str  # empty when the source states no adoption date
    published: str

    @property
    def year(self) -> str:
        return (self.enacted or self.published).split("-")[0]

    @property
    def work_uri(self) -> str:
        return work_uri(self.doctype, self.year, self.title)


def _date(raw: str | None) -> str:
    return (raw or "").split("+")[0].strip()


def read_identity(root: etree._Element) -> ActIdentity:
    """Title, class and dates of one `<oigusakt>`; raises when it states neither date."""
    meta = root.find("{*}metaandmed")
    title = root.findtext(".//{*}pealkiri") or root.findtext(".//{*}aktinimi") or "Seadus"
    title = " ".join(title.split())
    document_type = (meta.findtext("{*}dokumentLiik") if meta is not None else "") or "seadus"
    abbreviation = (meta.findtext("{*}lyhend") if meta is not None else "") or ""
    enacted = published = ""
    if meta is not None:
        adopted = meta.find("{*}vastuvoetud")
        if adopted is not None:
            enacted = _date(adopted.findtext("{*}aktikuupaev"))
        published = _date(meta.findtext(".//{*}avaldamineKuupaev"))
    if not (enacted or published):
        raise ValueError(
            f"Riigi Teataja XML document {title!r} lacks both enactment and publication dates"
        )
    return ActIdentity(
        title=title,
        doctype=classify(document_type, abbreviation, title),
        enacted=enacted,
        published=published,
    )


__all__ = [
    "CONSTITUTION_TITLE",
    "ActIdentity",
    "classify",
    "is_constitution",
    "read_identity",
    "romanise",
    "work_uri",
]
