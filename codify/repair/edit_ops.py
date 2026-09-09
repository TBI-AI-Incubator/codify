"""Transactional, XML-native application of repair operations to AKN. An edit is
accepted only if every guard passes, otherwise the original XML returns untouched
with a reason the agent can read.

Guards, in order: op-to-check policy, so a Delete may clear a phantom
`duplicate_number` but never an `empty_article` where deletion destroys real text;
normalised baseline, both measured on the ``ensure_unique_eids`` image so eId
de-duplication cannot hand an op a free finding budget; schema validity against
OASIS AKN 3.0, catching mis-nesting the heuristic validator is blind to; finding
identity, introducing no new finding by check and location rather than by count, and
clearing the targeted one; and body-text non-decrease on a non-delete op.

Moved and spliced subtrees are re-indexed so their eIds match the new position, with
references rewritten. Bodies are written by re-parsing Bluebell, structure moves via
tree ops, and the model never emits raw AKN.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import structlog
from lxml import etree

from codify.akn import AKN_NS
from codify.akn._schema import validate_akn as schema_validate
from codify.akn.eid import eid_abbrev
from codify.akn.eids import ensure_unique_eids
from codify.pipeline.enrich.anchors import _normalise_number
from codify.pipeline.enrich.bluebell import parse_to_akn
from codify.pipeline.enrich.conclusions import find_displaced_attestation, lift_attestation
from codify.pipeline.enrich.points import nest_enumerated_lines
from codify.pipeline.enrich.scaffold import _STRUCTURAL_LINE_RE
from codify.pipeline.enrich.validator import validate_akn
from codify.repair.grounding import eid_to_span
from codify.repair.ops import (
    Annotate,
    Delete,
    Merge,
    Move,
    MoveToConclusions,
    RenumberSequence,
    RepairOp,
    RestoreFromSource,
    SetBody,
    SetMoneyNumeral,
    SetNum,
    Split,
)
from codify.repair.scope import out_of_scope

_META = {"num", "heading", "subheading"}

# Operations must match the finding they claim to repair. Deletion cannot repair
# a content-bearing finding.
logger = structlog.get_logger()

_OP_POLICY: dict[str, set[type]] = {
    "empty_article": {SetBody, RestoreFromSource},
    "ocr_garble": {SetBody, RestoreFromSource},
    "body_artefact": {SetBody},
    "swallowed_enumerator": {Split},
    "orphan_articles": {Move},
    "hierarchy_coherence": {Move},
    # SetBody + Move let the mis-parse subclass restore-and-remove; a SetBody
    # here rewrites VALID text, so the sandbox escalates it to high (queued, never
    # auto-applied), the grounding floor only bars fabrication.
    "duplicate_number": {SetNum, RenumberSequence, Merge, Delete, SetBody, Move},
    # Not actionable: Split only cuts a swallowed enumerator from a flat <p>, not
    # two merged articles' point runs. The finding drops the grade; the real fix is
    # structurer-side, so the loop skips it rather than burning budget.
    "money_words_mismatch": {SetMoneyNumeral},
    "number_gap": {Annotate},
    "displaced_terminal_material": {MoveToConclusions},
}

# These repairs may shorten prose because they remove noise or move terminal
# material outside <body>.
_REDUCTION_OK_CHECKS = {
    "ocr_garble",
    "body_artefact",
    "money_words_mismatch",
    "displaced_terminal_material",
}

# Require at least one applicable operation before invoking the repair agent.
ACTIONABLE_CHECKS = frozenset(check for check, ops in _OP_POLICY.items() if ops)


@dataclass
class SourceEvidence:
    """What the source-anchored ops need: the combined OCR text, its expected
    sha (the dossier pin, a mismatch means the evidence drifted since the plan
    was made), and the jurisdiction's attestation vocabulary."""

    text: str = ""
    sha256: str = ""
    closing_phrases: list[str] = field(default_factory=list)


@dataclass
class ApplyResult:
    ok: bool
    xml: str  # the accepted xml, or the unchanged original on reject
    error: str = ""


class _RejectedOp(Exception):
    """A mutation that can't proceed (missing target, unsupported shape)."""


def _ln(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def has_structural_body_line(bluebell: str) -> bool:
    """A body line that opens another unit (ARTICLE/SECTION/(1)/(a)), it would
    mis-nest under a provision, so both check_bluebell and _set_body reject it."""
    return any(_STRUCTURAL_LINE_RE.match(line.strip()) for line in bluebell.splitlines())


def wrap_provision_bluebell(keyword: str, num: str, bluebell: str) -> str:
    """The single-provision Bluebell wrapper both the validate-time check and the
    apply-time splice parse, so an 'ok' can't be rejected differently at apply."""
    body = "\n".join(f"      {line}" for line in bluebell.splitlines() if line.strip())
    return f"BODY\n  {keyword} {num}\n{body}\n"


def validate_body_bluebell(subtree_xml: str, bluebell: str, *, country: str, doctype: str) -> str:
    """The subtree-local checks _set_body will apply. Empty string = would apply;
    else a human reason. Shared by check_bluebell (tool) and the output_validator
    so the model self-corrects before the plan reaches the workflow."""
    if has_structural_body_line(bluebell):
        return "a body line opens a new unit (ARTICLE/SECTION/(1)/(a)); write prose only"
    el = etree.fromstring(subtree_xml.encode("utf-8"))
    num_el = el.find(f"{{{AKN_NS}}}num")
    num = (num_el.text or "").strip() if num_el is not None else ""
    wrapper = wrap_provision_bluebell(etree.QName(el).localname.upper(), num, bluebell)
    try:
        parse_to_akn(wrapper, country=country, doctype=doctype)
    except Exception as exc:  # noqa: BLE001, surface the parser reason
        return f"{type(exc).__name__}: {exc}"
    return ""


def find_by_eid(root: etree._Element, eid: str) -> etree._Element | None:
    hits = root.xpath(".//*[@eId=$e]", e=eid)
    return hits[0] if hits else None


def _finding_key(issue: dict[str, Any]) -> tuple[str, str]:
    loc = (
        issue.get("eid")
        or ",".join(sorted(issue.get("eids", [])))
        or issue.get("container")
        or (issue.get("message", "")[:80])
    )
    # Several findings can share a host (two swallowed markers behind one
    # point; two gap runs after one provision), the payload disambiguates,
    # so clearing one does not read as clearing the others.
    detail = issue.get("missing")
    if detail is not None:
        loc = f"{loc}:{detail}"
    return (issue["check"], str(loc))


def _finding_keys(issues: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {_finding_key(i) for i in issues}


def _still_present(target: dict[str, Any], after: set[tuple[str, str]]) -> bool:
    """Whether the finding this plan was meant to clear survives the edit. Not key equality
    when the finding is a set of eIds: the key joins the whole list, so one recorded under
    a different truncation would never match and the guard would stop binding silently.
    One shared eId under the same check means still there.
    """
    key = _finding_key(target)
    if key in after:
        return True
    eids = target.get("eids")
    if not isinstance(eids, (list, tuple)) or not eids:
        return False
    mine = {str(e) for e in eids}
    check = target["check"]
    return any(c == check and (set(loc.split(",")) & mine) for c, loc in after)


def _body_text_len(xml: str) -> int:
    """Non-whitespace char count of body *prose*, excludes num/heading labels
    so removing a duplicate's number doesn't read as content loss."""
    root = etree.fromstring(xml.encode("utf-8"))
    body = root.find(f".//{{{AKN_NS}}}body")
    if body is None:
        return 0
    total = 0
    for el in body.iter():
        if _ln(el) in _META:
            continue
        for txt in (el.text, el.tail):
            if txt:
                total += sum(1 for c in txt if not c.isspace())
    return total


def apply_plan(
    xml: str,
    ops: list[RepairOp],
    *,
    country: str,
    doctype: str = "act",
    target_finding: dict[str, Any] | None = None,
    expected_anchor_summary: dict[str, int] | None = None,
    evidence: SourceEvidence | None = None,
) -> ApplyResult:
    """Apply a whole plan atomically: all ops on a clone, then validate once. A coordinated
    multi-edit fix, such as renumbering N points the OCR collapsed together, lands or
    not as a whole. Intermediate collisions do not reject it; only the final state is
    gated.
    """
    if not ops:
        return ApplyResult(False, xml, "empty plan")
    if target_finding is not None:
        allowed = _OP_POLICY.get(str(target_finding.get("check")), set())
        bad = next((op for op in ops if type(op) not in allowed), None)
        if bad is not None:
            return ApplyResult(
                False, xml, f"{bad.op} not permitted for finding {target_finding.get('check')!r}"
            )
        reach = out_of_scope(ops, target_finding)
        if reach is not None:
            logger.warning(
                "repair_plan_out_of_scope",
                check=str(target_finding.get("check", "")),
                finding_eid=str(target_finding.get("eid", "")),
                reason=reach,
            )
            return ApplyResult(False, xml, f"out of scope: {reach}")

    phrases = evidence.closing_phrases if evidence else None
    try:
        base_xml, _ = ensure_unique_eids(xml)
        before = _finding_keys(
            validate_akn(
                base_xml,
                expected_anchor_summary=expected_anchor_summary,
                closing_phrases=phrases,
            )
        )
        before_body = _body_text_len(base_xml)
    except Exception as exc:  # noqa: BLE001, unparseable input is a hard reject
        return ApplyResult(False, xml, f"pre-validate failed: {type(exc).__name__}: {exc}")

    root = etree.fromstring(base_xml.encode("utf-8"))
    for op in ops:
        try:
            _mutate(root, op, country=country, doctype=doctype, evidence=evidence)
        except _RejectedOp as exc:
            return ApplyResult(False, xml, str(exc))
        except Exception as exc:  # noqa: BLE001, a broken mutation must never corrupt
            return ApplyResult(False, xml, f"apply failed: {type(exc).__name__}: {exc}")

    new_xml, _ = ensure_unique_eids(etree.tostring(root, encoding="unicode"))

    try:
        schema_validate(new_xml)
    except Exception as exc:  # noqa: BLE001, DocumentInvalid + parse errors
        return ApplyResult(False, xml, f"schema-invalid: {str(exc).splitlines()[0][:160]}")

    try:
        after = _finding_keys(
            validate_akn(
                new_xml,
                expected_anchor_summary=expected_anchor_summary,
                closing_phrases=phrases,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return ApplyResult(False, xml, f"post-validate failed: {type(exc).__name__}: {exc}")

    introduced = after - before
    if introduced:
        return ApplyResult(False, xml, f"introduced findings {sorted(introduced)[:3]}, rejected")
    if target_finding is not None and _still_present(target_finding, after):
        return ApplyResult(False, xml, "targeted finding not cleared, rejected")
    # Reject silent truncation, except for operations that intentionally relocate
    # or remove content.
    check = str(target_finding.get("check", "")) if target_finding else ""
    reduction_ok = (
        any(isinstance(op, (Delete, Split, Merge)) for op in ops) or check in _REDUCTION_OK_CHECKS
    )
    if not reduction_ok and _body_text_len(new_xml) < before_body:
        return ApplyResult(False, xml, "body text shrank, rejected")
    return ApplyResult(True, new_xml, "")


def apply_op(
    xml: str,
    op: RepairOp,
    *,
    country: str,
    doctype: str = "act",
    target_finding: dict[str, Any] | None = None,
    expected_anchor_summary: dict[str, int] | None = None,
    evidence: SourceEvidence | None = None,
) -> ApplyResult:
    """Apply a single op transactionally (a one-op plan)."""
    return apply_plan(
        xml,
        [op],
        country=country,
        doctype=doctype,
        target_finding=target_finding,
        expected_anchor_summary=expected_anchor_summary,
        evidence=evidence,
    )


def _mutate(
    root: etree._Element,
    op: RepairOp,
    *,
    country: str,
    doctype: str,
    evidence: SourceEvidence | None = None,
) -> None:
    if isinstance(op, SetBody):
        _set_body(root, op, country=country, doctype=doctype)
    elif isinstance(op, SetNum):
        _set_num(root, op)
    elif isinstance(op, SetMoneyNumeral):
        _set_money_numeral(root, op)
    elif isinstance(op, RenumberSequence):
        _renumber_sequence(root, op)
    elif isinstance(op, Move):
        _move(root, op)
    elif isinstance(op, Delete):
        _delete(root, op)
    elif isinstance(op, Merge):
        _merge(root, op)
    elif isinstance(op, Split):
        _split(root, op)
    elif isinstance(op, Annotate):
        _annotate(root, op)
    elif isinstance(op, RestoreFromSource):
        _restore_from_source(root, op, country=country, doctype=doctype, evidence=evidence)
    elif isinstance(op, MoveToConclusions):
        _move_to_conclusions(root, op, evidence=evidence)
    else:  # pragma: no cover - exhaustive
        raise _RejectedOp(f"unknown op {op!r}")


def _require(root: etree._Element, eid: str) -> etree._Element:
    el = find_by_eid(root, eid)
    if el is None:
        raise _RejectedOp(f"eId {eid!r} not found")
    return el


def _own_eid(el: etree._Element) -> str:
    ln = _ln(el)
    abbrev = eid_abbrev(ln)
    num_el = el.find(f"{{{AKN_NS}}}num")
    num = _normalise_number(num_el.text if num_el is not None else "")
    return f"{abbrev}_{num}"


# Carry a prefix down to their paragraphs without taking an eId themselves,
# which is how both the emitter and Bluebell write them. Their <p> children are
# unreachable to a walk that only follows elements carrying an eId.
_TRANSPARENT: dict[str, str] = {"content": "", "intro": "intro", "wrapUp": "wrapup"}


def _reindex(el: etree._Element, parent_eid: str | None, remap: dict[str, str]) -> None:
    """Recompute hierarchical eIds for ``el`` and eId-bearing descendants from
    their new parent, recording old→new so refs can be rewritten."""
    old = el.get("eId")
    own = _own_eid(el)
    new = f"{parent_eid}__{own}" if parent_eid else own
    if old and old != new:
        remap[old] = new
    el.set("eId", new)
    if el.get("wId") is not None:
        el.set("wId", new)
    for child in el:
        if not isinstance(child.tag, str):
            continue
        if child.get("eId") is not None:
            _reindex(child, new, remap)
            continue
        segment = _TRANSPARENT.get(_ln(child))
        if segment is not None:
            _reindex_paragraphs(child, f"{new}__{segment}" if segment else new, remap)


def _reindex_paragraphs(container: etree._Element, prefix: str, remap: dict[str, str]) -> None:
    """Renumber a transparent container's <p> children by position.

    A <p> carries no <num>, so its ordinal is its position among its siblings.
    Deriving it the usual way would collapse every paragraph onto ``p_0``.
    """
    index = 0
    for child in container:
        if _ln(child) != "p":
            continue
        index += 1
        old = child.get("eId")
        if old is None:
            continue
        new = f"{prefix}__p_{index}"
        if old != new:
            remap[old] = new
        child.set("eId", new)
        if child.get("wId") is not None:
            child.set("wId", new)


def _rewrite_refs(root: etree._Element, remap: dict[str, str]) -> None:
    if not remap:
        return
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in ("href", "refersTo"):
            val = el.get(attr)
            if val and val.startswith("#") and val[1:] in remap:
                el.set(attr, f"#{remap[val[1:]]}")


def _set_num(root: etree._Element, op: SetNum) -> None:
    el = _require(root, op.eid)
    num = el.find(f"{{{AKN_NS}}}num")
    if num is None:
        num = etree.Element(f"{{{AKN_NS}}}num")
        el.insert(0, num)
    num.text = op.num


_ARABIC_INDIC = "\u0660-\u0669\u06f0-\u06f9"
_DIGIT = rf"0-9{_ARABIC_INDIC}"
_SEPARATOR = ".,\u066b\u066c"
_NUMERAL_ONLY = re.compile(rf"^[{_DIGIT}][{_DIGIT}{_SEPARATOR}]*$")


def _digit_script(surface: str) -> str:
    """ "latin" or "arabic" for a numeral's digits; both is a mixed surface."""
    scripts = {"arabic" if "\u0660" <= c <= "\u06f9" else "latin" for c in surface if c.isdigit()}
    return scripts.pop() if len(scripts) == 1 else "mixed"


def _set_money_numeral(root: etree._Element, op: SetMoneyNumeral) -> None:
    """Rewrite one numeral inside the element's own text, leaving prose alone. Both surfaces
    must be bare numerals, share a digit script, differ, and ``old`` must occur exactly
    once bounded by non-digits, so correcting 50 cannot rewrite the 50 in 500. The
    element's own tail is out of scope.
    """
    for surface, label in ((op.old, "old"), (op.new, "new")):
        if not _NUMERAL_ONLY.match(surface.strip()):
            raise _RejectedOp(f"{label} surface {surface!r} is not a numeral")
    old, new = op.old.strip(), op.new.strip()
    if old == new:
        raise _RejectedOp(f"numeral {old!r} is unchanged; the op would clear nothing")
    scripts = {_digit_script(old), _digit_script(new)}
    if scripts != {"latin"} and scripts != {"arabic"}:
        # Two mixed surfaces would otherwise compare equal and pass.
        raise _RejectedOp(f"digit script is not consistent across {old!r} and {new!r}")
    el = _require(root, op.eid)
    # A grouping separator is part of the numeral, so plain digit boundaries
    # let `50` match inside `50,000` and `000` match its tail.
    bounded = re.compile(
        rf"(?<![{_DIGIT}])(?<![{_DIGIT}][{_SEPARATOR}])"
        rf"{re.escape(old)}"
        rf"(?![{_DIGIT}])(?![{_SEPARATOR}][{_DIGIT}])"
    )
    nodes = [
        (node, attr)
        for node in el.iter()
        for attr in ("text", "tail")
        if not (attr == "tail" and node is el) and bounded.search(getattr(node, attr) or "")
    ]
    total = sum(len(bounded.findall(getattr(n, a) or "")) for n, a in nodes)
    if total != 1:
        raise _RejectedOp(f"numeral {old!r} occurs {total} times under {op.eid!r}, expected 1")
    node, attr = nodes[0]
    setattr(node, attr, bounded.sub(new, getattr(node, attr) or "", count=1))


def _nearest_eid_ancestor(el: etree._Element) -> etree._Element | None:
    for anc in el.iterancestors():
        if anc.get("eId"):
            return anc
    return None


def _renumber_sequence(root: etree._Element, op: RenumberSequence) -> None:
    """Renumber the parent's immediate ``kind`` children, in document order, to
    the given sequence, fixing a whole run of collapsed numbering at once. Only
    the direct run counts: a ``kind`` element nested inside another belongs to
    that inner run, matching how the validator groups duplicates by parent."""
    parent = _require(root, op.parent_eid)
    kids = [
        k
        for k in parent.iter(f"{{{AKN_NS}}}{op.kind}")
        if k is not parent and _nearest_eid_ancestor(k) is parent
    ]
    if len(kids) != len(op.nums):
        raise _RejectedOp(f"{len(kids)} {op.kind}(s) under {op.parent_eid} but {len(op.nums)} nums")
    for kid, num in zip(kids, op.nums):
        num_el = kid.find(f"{{{AKN_NS}}}num")
        if num_el is None:
            num_el = etree.Element(f"{{{AKN_NS}}}num")
            kid.insert(0, num_el)
        num_el.text = num


def _move(root: etree._Element, op: Move) -> None:
    el = _require(root, op.eid)
    parent = _require(root, op.new_parent_eid)
    if el is parent or parent in el.iter():
        raise _RejectedOp("cannot move an element under itself or a descendant")
    parent.append(el)  # lxml detaches from the old parent
    remap: dict[str, str] = {}
    _reindex(el, parent.get("eId"), remap)
    _rewrite_refs(root, remap)


def _delete(root: etree._Element, op: Delete) -> None:
    el = _require(root, op.eid)
    parent = el.getparent()
    if parent is None:
        raise _RejectedOp("cannot delete the document root")
    parent.remove(el)


def _merge(root: etree._Element, op: Merge) -> None:
    """Fold eid_b's body into eid_a, then remove eid_b (an over-split unit)."""
    a = _require(root, op.eid_a)
    b = _require(root, op.eid_b)
    if a is b:
        raise _RejectedOp("cannot merge an element with itself")
    if b in a.iter() or a in b.iter():
        raise _RejectedOp("cannot merge nested elements")
    # An element carries one <content>, so fold b's content children into a's
    # existing <content> rather than appending a second (which is schema-invalid).
    a_content = a.find(f"{{{AKN_NS}}}content")
    # References to the removed unit follow the merge into a.
    b_eid = b.get("eId")
    remap: dict[str, str] = {b_eid: a.get("eId", "")} if b_eid and a.get("eId") else {}
    for child in list(b):
        if _ln(child) in _META:
            continue
        if _ln(child) == "content" and a_content is not None:
            for para in list(child):
                clone = copy.deepcopy(para)
                a_content.append(clone)
                if clone.get("eId") is not None:
                    _reindex(clone, a_content.get("eId"), remap)
        else:
            clone = copy.deepcopy(child)
            a.append(clone)
            if clone.get("eId") is not None:
                _reindex(clone, a.get("eId"), remap)
    parent = b.getparent()
    if parent is None:
        raise _RejectedOp("cannot remove the document root")
    parent.remove(b)
    _rewrite_refs(root, remap)


# The numeral/letter carried by a swallowed enumerator marker like "(2)" or "b)".
_MARKER_NUM = re.compile(r"[0-9]+|[ivxlcdm]+|[a-z]", re.IGNORECASE)

_ARABIC_LETTER = re.compile(r"[\u0621-\u064a]")


def _token_class(surface: str) -> str:
    """The enumerator family of a marker surface: digits, Arabic letters or
    Latin letters. Empty when nothing recognisable, the caller then abstains
    rather than blocking a legitimate split."""
    bare = surface.strip().strip("()").strip().rstrip("\u0640.")
    if not bare:
        return ""
    if any(c.isdigit() for c in bare):
        return "digit"
    if _ARABIC_LETTER.search(bare):
        return "alpha-ar"
    if bare.isalpha():
        return "alpha-lat"
    return ""


def _split(root: etree._Element, op: Split) -> None:
    """Cut a swallowed enumerator out of ``eid``'s body into a new sibling. The body holds a
    run that begins another unit (``…first (2) second…``); split its <p> at ``marker``,
    keep the head, and make the tail a new sibling of the same kind numbered from the
    marker.
    """
    el = _require(root, op.eid)
    marker = op.marker.strip()
    if not marker:
        raise _RejectedOp("empty split marker")
    content = el.find(f"{{{AKN_NS}}}content")
    if content is None:
        raise _RejectedOp("element has no <content> to split")
    target_p = next((p for p in content if _ln(p) == "p" and p.text and marker in p.text), None)
    if target_p is None or not target_p.text:
        raise _RejectedOp(f"marker {marker!r} not found in body")
    idx = target_p.text.index(marker)
    head = target_p.text[:idx].rstrip()
    tail = target_p.text[idx + len(marker) :].strip()
    if not head:
        raise _RejectedOp("nothing before the marker to keep")
    if not tail:
        raise _RejectedOp("nothing after the marker to split out")
    if has_structural_body_line(tail):
        raise _RejectedOp("split tail opens another unit; a same-rank sibling cannot hold it")
    own_num = el.find(f"{{{AKN_NS}}}num")
    own_class = _token_class((own_num.text or "") if own_num is not None else "")
    if own_class and _token_class(marker) != own_class:
        raise _RejectedOp(
            f"marker {marker!r} is a different enumerator family than the run it joins"
        )

    target_p.text = head
    new_el = etree.Element(el.tag)
    num_el = etree.SubElement(new_el, f"{{{AKN_NS}}}num")
    m = _MARKER_NUM.search(marker)
    num_el.text = m.group(0) if m else marker
    new_p = etree.SubElement(etree.SubElement(new_el, f"{{{AKN_NS}}}content"), f"{{{AKN_NS}}}p")
    new_p.text = tail

    parent = el.getparent()
    if parent is None:
        raise _RejectedOp("cannot split the document root")
    parent.insert(list(parent).index(el) + 1, new_el)
    remap: dict[str, str] = {}
    _reindex(new_el, parent.get("eId"), remap)
    _rewrite_refs(root, remap)


def _set_body(root: etree._Element, op: SetBody, *, country: str, doctype: str) -> None:
    """Re-parse one provision's Bluebell, splice its body children in keeping the target's
    own num and heading, then re-index. Model-authored bodies are prose-only, the
    structural-line guard stopping the agent smuggling units in through a body;
    `_splice_body` without it serves the deterministic restore path.
    """
    if has_structural_body_line(op.bluebell):
        raise _RejectedOp("body line begins with a structural keyword")
    _splice_body(root, op.eid, op.bluebell, country=country, doctype=doctype)


def _splice_body(
    root: etree._Element, eid: str, bluebell: str, *, country: str, doctype: str
) -> None:
    target = _require(root, eid)
    num_el = target.find(f"{{{AKN_NS}}}num")
    num = (num_el.text or "").strip() if num_el is not None else ""
    wrapper = wrap_provision_bluebell(_ln(target).upper(), num, bluebell)
    parsed_xml = parse_to_akn(wrapper, country=country, doctype=doctype)
    parsed = etree.fromstring(parsed_xml.encode("utf-8"))
    src = next(iter(parsed.iter(f"{{{AKN_NS}}}{_ln(target)}")), None)
    if src is None:
        raise _RejectedOp("re-parse produced no matching unit")

    for child in list(target):
        if _ln(child) not in _META:
            target.remove(child)
    remap: dict[str, str] = {}
    for child in list(src):
        if _ln(child) in _META:
            continue
        clone = copy.deepcopy(child)
        target.append(clone)
        if clone.get("eId") is not None:
            _reindex(clone, target.get("eId"), remap)
    _rewrite_refs(root, remap)
    if all(_ln(c) in _META for c in target):
        raise _RejectedOp("re-parse yielded no body content")


_NOTE_MAX_CHARS = 500


def _annotate(root: etree._Element, op: Annotate) -> None:
    """Attach an editorial note to the target's inline host (heading, else its
    first paragraph). `placement="bottom"` is load-bearing: every projection
    excludes lifted notes, so the note can never read as provision text; the
    `editorial-gap` marker is what lets an acknowledged number gap clear."""
    note = op.note.strip()
    if not note:
        raise _RejectedOp("empty note")
    if len(note) > _NOTE_MAX_CHARS:
        raise _RejectedOp(f"note longer than {_NOTE_MAX_CHARS} chars")
    if has_structural_body_line(note):
        raise _RejectedOp("note opens a structural unit; write prose only")
    el = _require(root, op.eid)
    host = el.find(f"{{{AKN_NS}}}heading")
    if host is None:
        host = next(iter(el.iter(f"{{{AKN_NS}}}p")), None)
    if host is None:
        raise _RejectedOp(f"{op.eid!r} has no heading or paragraph to host a note")
    taken = [
        int(m.group(1))
        for el in root.iter()
        if isinstance(el.tag, str) and el.get("eId")
        for m in [re.match(r"edn_(\d+)$", el.get("eId", ""))]
        if m
    ]
    note_el = etree.SubElement(host, f"{{{AKN_NS}}}authorialNote")
    note_el.set("eId", f"edn_{max(taken, default=0) + 1}")
    note_el.set("placement", "bottom")
    note_el.set("marker", "editorial-gap")
    p_el = etree.SubElement(note_el, f"{{{AKN_NS}}}p")
    p_el.text = note


def _restore_from_source(
    root: etree._Element,
    op: RestoreFromSource,
    *,
    country: str,
    doctype: str,
    evidence: SourceEvidence | None,
) -> None:
    """Restore the target's body verbatim from its resolved span of the source. The span
    comes from the deterministic anchor scan, never the model, and the evidence must hash
    to the pin it was proposed against: sliced-from-source text grounds 1.0 by
    construction, so offset integrity carries the weight.
    """
    if evidence is None or not evidence.text:
        raise _RejectedOp("no source evidence available for restore_from_source")
    if evidence.sha256:
        actual = hashlib.sha256(evidence.text.encode("utf-8")).hexdigest()
        if actual != evidence.sha256:
            raise _RejectedOp("source text drifted since the plan was made")
    span = eid_to_span(evidence.text, country=country, doctype=doctype).get(op.eid)
    if span is None:
        raise _RejectedOp(f"no source span resolves for {op.eid!r}")
    # The span opens at the newline BEFORE the anchor line; strip it so the
    # partition drops the anchor line itself, structure, not body.
    chunk = evidence.text[span[0] : span[1]].lstrip("\n")
    _anchor_line, _, rest = chunk.partition("\n")
    lines = [ln.strip() for ln in rest.splitlines() if ln.strip()]
    if not lines:
        raise _RejectedOp(f"the source span for {op.eid!r} carries no body text")
    target = _require(root, op.eid)
    current = "".join(
        t for el in target.iter() if _ln(el) not in _META for t in (el.text, el.tail) if t
    )
    restored = "\n".join(lines)
    if len("".join(current.split())) >= len("".join(restored.split())):
        raise _RejectedOp(
            f"{op.eid!r} already carries at least as much text as its source span; "
            "restore is for dropped bodies, not rewrites"
        )
    _splice_body(
        root, op.eid, "\n".join(nest_enumerated_lines(lines)), country=country, doctype=doctype
    )


def _move_to_conclusions(
    root: etree._Element, op: MoveToConclusions, *, evidence: SourceEvidence | None
) -> None:
    """Lift the attestation out of the named provision into `<conclusions>`,
    with the same detector the ingest pass uses, the op cannot move anything
    the detector would not itself classify as attestation."""
    phrases = evidence.closing_phrases if evidence else []
    if not phrases:
        raise _RejectedOp("no closing-phrase vocabulary for this jurisdiction")
    container, start = find_displaced_attestation(root, phrases)
    if container is None or start is None:
        raise _RejectedOp("no displaced attestation found")
    target = _require(root, op.eid)
    if container is not target and target not in set(container.iterancestors()):
        raise _RejectedOp(
            f"the displaced attestation sits under "
            f"{container.get('eId') or 'an unnamed container'!r}, not {op.eid!r}"
        )
    if not lift_attestation(root, container, start):
        raise _RejectedOp("nothing to move")
