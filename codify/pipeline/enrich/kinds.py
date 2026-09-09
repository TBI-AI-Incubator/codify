"""Shared AKN element vocabulary for the enrich pipeline.

Lifted out of ``structure.py`` so the scaffolder and anchor scanner can
both consume the same kind/keyword/rank tables without circular imports.
"""

from __future__ import annotations

# The canonical spellings this code writes and matches on. The parser also takes
# abbreviations listed in the installed grammar.
BLUEBELL_HIER_KEYWORDS: frozenset[str] = frozenset(
    {
        "ALINEA",
        "ARTICLE",
        "BOOK",
        "CHAPTER",
        "CLAUSE",
        "DIVISION",
        "INDENT",
        "LEVEL",
        "LIST",
        "PARAGRAPH",
        "PART",
        "POINT",
        "PROVISO",
        "RULE",
        "SECTION",
        "SUBCHAPTER",
        "SUBCLAUSE",
        "SUBDIVISION",
        "SUBLIST",
        "SUBPARAGRAPH",
        "SUBPART",
        "SUBRULE",
        "SUBSECTION",
        "SUBTITLE",
        "TITLE",
        "TOME",
        "TRANSITIONAL",
    }
)

# Crossing one of these resets per-parent child numbering; civil-law codes
# restart paragraph/point numbering inside every fresh article.
BASIC_UNIT_KEYWORDS: frozenset[str] = frozenset({"ARTICLE", "SECTION"})

# Child units that live under a basic unit (top-level keywords minus the basic units).
CHILD_KEYWORDS: frozenset[str] = frozenset({"PARAGRAPH", "SUBSECTION", "POINT", "RULE", "CLAUSE"})

# Rank for the stack walk that derives anchor depth. Lower rank = higher
# in the hierarchy. Containers open higher ranks; basic units close them.
KIND_RANK: dict[str, int] = {
    "book": 0,
    "tome": 0,
    "part": 1,
    "subpart": 2,
    "title": 2,
    "subtitle": 3,
    "chapter": 3,
    "subchapter": 4,
    "division": 4,
    "subdivision": 5,
    "section": 6,
    "subsection": 7,
    "article": 8,
    "paragraph": 9,
    "subparagraph": 10,
    "point": 10,
    "clause": 9,
    "subclause": 10,
    "rule": 8,
    "subrule": 9,
}


# Grouping kinds above the basic unit: every ranked kind above `section`.
# Shared by the heading-capture pass and the missing-title validator so the
# two vocabularies cannot drift.
CONTAINER_KINDS: frozenset[str] = frozenset(
    k for k, r in KIND_RANK.items() if r < KIND_RANK["section"]
)


def kind_to_kw(kind: str) -> str:
    """Map an AKN element name to its Bluebell uppercase keyword."""
    return kind.strip().upper()


__all__ = [
    "BLUEBELL_HIER_KEYWORDS",
    "BASIC_UNIT_KEYWORDS",
    "CHILD_KEYWORDS",
    "CONTAINER_KINDS",
    "KIND_RANK",
    "kind_to_kw",
]
