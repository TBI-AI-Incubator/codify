"""Compare deterministic AKN skeletons while ignoring model-filled prose."""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from codify.akn._schema import parse_xml
from codify.akn.vocabulary import BORROWED_ANCESTORS, PROSE_ELEMENTS


@dataclass(frozen=True)
class StructureNode:
    """One eId-bearing element, reduced to what the pipeline computes."""

    eid: str
    tag: str
    parent_eid: str | None
    num: str | None
    heading: str | None
    name: str | None = None  # `hcontainer[@name]` is a provision; bare is not.


def _local(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _child_text(el: etree._Element, name: str) -> str | None:
    for child in el:
        if isinstance(child.tag, str) and _local(child.tag) == name:
            return " ".join("".join(child.itertext()).split()) or None
    return None


def _is_borrowed(el: etree._Element) -> bool:
    """True for a borrowed element and anything inside one. The lifter gives the
    quote wrapper its own eId, so the element is tested with its ancestors."""
    return any(_local(e.tag) in BORROWED_ANCESTORS for e in (el, *el.iterancestors()))


def structure_of(akn_xml: str) -> tuple[StructureNode, ...] | None:
    """The document's skeleton in document order, or ``None`` if it will not parse.

    ``None`` rather than an empty tuple, so a caller cannot mistake "could not
    read this" for "this document has no structure".
    """
    try:
        root = parse_xml(akn_xml)
    except etree.XMLSyntaxError:
        return None

    nodes: list[StructureNode] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        eid = el.get("eId")
        if not eid or _local(el.tag) in PROSE_ELEMENTS or _is_borrowed(el):
            continue
        parent = el.getparent()
        while parent is not None and parent.get("eId") is None:
            parent = parent.getparent()
        nodes.append(
            StructureNode(
                eid=eid,
                tag=_local(el.tag),
                parent_eid=parent.get("eId") if parent is not None else None,
                num=_child_text(el, "num"),
                heading=_child_text(el, "heading"),
                name=el.get("name"),
            )
        )
    return tuple(nodes)


def diff_structure(before: str, after: str) -> list[str] | None:
    """Differences between two documents' skeletons, or ``None`` if either will
    not parse. An empty list means the deterministic layer reproduced exactly."""
    old = structure_of(before)
    new = structure_of(after)
    if old is None or new is None:
        return None

    by_eid = {n.eid: n for n in old}
    seen = {n.eid: n for n in new}
    differences = [f"{eid}: gone (was {by_eid[eid].tag})" for eid in by_eid if eid not in seen]
    differences += [f"{eid}: new ({seen[eid].tag})" for eid in seen if eid not in by_eid]

    for eid, fresh in seen.items():
        was = by_eid.get(eid)
        if was is None:
            continue
        for field in ("tag", "parent_eid", "num", "heading"):
            old_value = getattr(was, field)
            new_value = getattr(fresh, field)
            if old_value != new_value:
                differences.append(f"{eid}: {field} {old_value!r} -> {new_value!r}")

    shared_old = [n.eid for n in old if n.eid in seen]
    shared_new = [n.eid for n in new if n.eid in by_eid]
    if shared_old != shared_new:
        differences.append("document order changed")
    return differences


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m codify.akn.structure_diff BEFORE.xml AFTER.xml")
        return 2
    before, after = (open(p, encoding="utf-8").read() for p in argv)
    differences = diff_structure(before, after)
    if differences is None:
        print("could not parse one of the documents")
        return 2
    for line in differences:
        print(line)
    print(f"{len(differences)} structural difference(s)")
    return 1 if differences else 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_main(sys.argv[1:]))
