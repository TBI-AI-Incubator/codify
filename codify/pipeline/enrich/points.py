"""Reconstruct nested point structure from enumerated body lines.

Body-fill emits sub-points as plain lines ("(1)", "(أ)"), and Bluebell needs explicit
``POINT`` keywords and indentation to infer nesting.

Depth uses a family stack of each level's last ordinal: a new family opens a child, an
open one lower resumes that level, and a same-family marker going backwards opens a
child. That backwards step is the only nesting cue when one marker style serves both
outer and inner enumerations.
"""

from __future__ import annotations

import re

from codify.lang import normalise_digits

_INDENT = "  "

_ROMAN_CHARS = set("ivxlcdm")
_BULLETS = "•▪◦‣·●○*"

# One enumerator marker: parenthesised "(1)/(أ)"; token plus terminator "1)/1./أ-"
# where the terminator is not followed by a digit, so decimals and ranges do not
# match, and no trailing space is required because Arabic omits it ("1.تدعى"); or a
# bullet. A trailing kashida (U+0640) on an Arabic letter is decorative, written to
# disambiguate `ه` from short-form vowels, and matches as the bare letter.
_MARK = (
    r"(?:"
    r"\(\s*(?P<ptok>[0-9٠-٩۰-۹]+|viii|vii|iii|ix|iv|vi|ii|VIII|VII|III|IX|IV|VI|II|(?P<prep>[A-Za-z])(?P=prep){1,2}|[A-Za-z]|[ء-ي]ـ?)\s*\)"
    r"|(?P<ntok>[0-9٠-٩۰-۹]+|viii|vii|iii|ix|iv|vi|ii|VIII|VII|III|IX|IV|VI|II|(?P<nrep>[A-Za-z])(?P=nrep){1,2}|[A-Za-z]|[ء-ي]ـ?)[)\.‐-―:-](?=\s|[^0-9٠-٩۰-۹])"
    r"|(?P<bul>[" + _BULLETS + r"])"
    r")"
)
_ENUM_RE = re.compile(r"^\s*(?P<marker>" + _MARK + r")\s*(?P<rest>.*)$")
_INLINE_MARK = re.compile(r"(?:^|(?<=\s))(?P<marker>" + _MARK + r")(?=\s)")

# A reference noun directly before a marker ("الفقرة (1)") makes it a prose
# cross-reference, not an enumerator. Singular and dual, with or without the article
# or a clitic prefix. Suffix-matched, so a list item ending in one of these nouns
# also suppresses the next marker: a missed split preferred over a shredded
# cross-reference.
_REF_NOUN_RE = re.compile(
    r"(?:فقرة|فقرتين|فقرتان|مادة|مادتين|مادتان|بند|بندين|بندان|نقطة|نقطتين|نقطتان"
    r"|رقم|مبلغ|بمبلغ|غرامة)$"
)


# A marker whose following word is a currency or year connector is an amount
# or citation, not an enumerator: "(2000) دينار", "(6) لسنة 1999". Currency
# vocabulary shared with the money words/numeral consistency check.
def _followed_by_amount_word(line: str, end: int) -> bool:
    from codify.pipeline.enrich.arabic_cardinals import _CURRENCY
    from codify.pipeline.enrich.arabic_normalise import fold_arabic_for_match

    tail_words = line[end:].split()
    first = tail_words[0] if tail_words else ""
    folded = fold_arabic_for_match(first).lstrip("و").strip(".،,:؛()")
    return folded in _CURRENCY or folded in ("لسنه", "لعام", "الف", "مليون", "الاف", "ملايين")


def _split_inline(line: str) -> list[str]:
    """Split a line carrying a run of inline enumerators into one line per marker.

    Conservative: splits when two or more markers of one family form an ascending chain,
    or at a marker introduced by a colon, where a lead-in and its first item run
    together. Markers preceded by a reference noun ("الفقرة (1) من المادة (5)") are prose
    cross-references, so they neither count towards a run nor become split points.
    """
    marks = [
        m
        for m in _INLINE_MARK.finditer(line)
        if not _REF_NOUN_RE.search(line[: m.start("marker")].rstrip())
        and not _followed_by_amount_word(line, m.end("marker"))
    ]
    if len(marks) < 2:
        return [line]
    fams: dict[str, list[int]] = {}
    for m in marks:
        tok = m.group("ptok") or m.group("ntok") or m.group("bul")
        fam = _family(tok, None, _style_of_match(m))
        fams.setdefault(fam, []).append(_token_order(tok, fam))
    same_family_run = any(
        len(orders) >= 2
        and (
            f.startswith("bullet/")
            or all(o > 0 for o in orders)
            and all(a < b for a, b in zip(orders, orders[1:]))
        )
        for f, orders in fams.items()
        if not f.startswith("other/")
    )
    colon_marks = [
        m for m in marks if line[: m.start("marker")].rstrip(" \t-‐‑‒–—―").endswith((":", "："))
    ]
    if not same_family_run and not colon_marks:
        return [line]
    split_marks = marks if same_family_run else colon_marks
    starts = sorted(m.start("marker") for m in split_marks)
    out: list[str] = []
    head = line[: starts[0]].strip()
    if head:
        out.append(head)
    for i, s in enumerate(starts):
        seg = line[s : starts[i + 1] if i + 1 < len(starts) else len(line)].strip()
        if seg:
            out.append(seg)
    return out


def _style_of_match(m: re.Match[str]) -> str:
    """Marker style, distinguishes '(1)', '1)', and '1.' so two markers carrying
    the same token-type but different shapes register as different families
    (and so different hierarchy levels)."""
    if m.group("ptok"):
        return "paren-both"
    if m.group("bul"):
        return "bullet"
    matched = m.group("marker")
    last = matched[-1] if matched else ""
    if last == ")":
        return "paren-right"
    if last == ".":
        return "dot"
    if last in ":：":
        return "colon"
    return "dash"


def _alpha_position(tok: str) -> int:
    """Position in a..z, aa..zz, aaa..zzz repeated-letter sequences."""
    low = tok.lower()
    if 1 <= len(low) <= 3 and low.isascii() and low.isalpha() and len(set(low)) == 1:
        return 26 * (len(low) - 1) + ord(low[0]) - ord("a") + 1
    return 0


def _family(tok: str, parent_family: str | None, style: str = "?", parent_order: int = 0) -> str:
    """Classify an enumerator as '{token-type}/{style}'. Style discriminates '1.' from '1)'
    from '(1)', so two markers sharing a token-type but written differently do not
    collapse to the same hierarchy level.
    """
    if tok in _BULLETS:
        return f"bullet/{style}"
    if normalise_digits(tok).isdigit():
        return f"num/{style}"
    if all("ء" <= c <= "ي" for c in tok):
        return f"alpha-ar/{style}"
    low = tok.lower()
    if low.isascii() and low.isalpha():
        is_roman = all(c in _ROMAN_CHARS for c in low)
        # Roman disambiguation looks at parent token-type only, style irrelevant.
        parent_token = parent_family.split("/", 1)[0] if parent_family else None
        # Seven letters are also roman numerals, so a single letter is ambiguous
        # while a lettered run of the same style is open. It continues that run when
        # it is the next letter along, and opens a roman child when it is not:
        # (a)(b)(c) stays flat, (a) then (i) nests. `parent_order` is that run's last
        # ordinal, 0 when none is open. Without it an (a)-to-(z) list breaks apart at
        # c, i, l, v and x.
        if is_roman and parent_order > 0:
            if _alpha_position(low) == parent_order + 1:
                return f"alpha-lat/{style}"
        if is_roman and (len(low) > 1 or parent_token in {"alpha-lat", "roman"}):  # noqa: S105
            return f"roman/{style}"
        return f"alpha-lat/{style}"
    return f"other/{style}"


# Arabic list markers run in abjad order (أ ب ج د ه و ز ح ط ي ...), which is
# non-monotonic in codepoint space, ز sits after و in a list but before it
# in Unicode. Rank through the abjad sequence so ascending-chain checks hold.
_ABJAD_ORDER = {c: i + 1 for i, c in enumerate("ابجدهوزحطيكلمنسعفصقرشتثخذضظغ")}
_ABJAD_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"})


def _token_order(tok: str, family: str | None = None) -> int:
    """Ordinal position of an enumerator token, for detecting backwards resets.
    Returns 0 for unrecognised tokens (which skip the reset check)."""
    n = normalise_digits(tok)
    if n.isdigit():
        return int(n)
    low = tok.lower()
    if all("ء" <= c <= "ي" for c in tok):
        return _ABJAD_ORDER.get(tok[0].translate(_ABJAD_FOLD), 0)
    if low.isascii() and low.isalpha() and len(low) <= 4:
        # Read the token the way its family was classified. Judging by shape
        # alone disagrees with _family on c/d/i/l/m/v/x and silently zeroes the
        # ordinal of an ordinary alpha item.
        as_roman = (
            family.startswith("roman/")
            if family is not None
            else all(c in _ROMAN_CHARS for c in low)
        )
        if as_roman:
            return {
                "i": 1,
                "ii": 2,
                "iii": 3,
                "iv": 4,
                "v": 5,
                "vi": 6,
                "vii": 7,
                "viii": 8,
                "ix": 9,
                "x": 10,
            }.get(low, 0)
        return _alpha_position(low)
    return 0


def starts_with_enumerator(line: str) -> bool:
    """Whether a line begins with a marker understood by the point nester."""
    return _ENUM_RE.match(line) is not None


def _split(line: str) -> tuple[str, str, str, str] | None:
    """(emit-marker, classify-token, rest, style) for an enumerated line, else None.
    Bullets carry an empty emit-marker (an unnumbered POINT) and the bullet char
    as the classify token."""
    m = _ENUM_RE.match(line.strip())
    if not m:
        return None
    rest = m.group("rest").strip()
    if m.group("bul"):
        return "", m.group("bul"), rest, "bullet"
    tok = m.group("ptok") or m.group("ntok")
    return m.group("marker").strip(), tok, rest, _style_of_match(m)


# A defined-term lead-in ("المورد: الشخص الذي يقوم…"): a short colon-headed
# line opens a new definition at the container's own level, never a
# continuation of the previous point.
_TERM_LEADIN_RE = re.compile(r"^[^:：.。،؛;()]{1,40}[:：]\s*\S")
# Continuation labels that head a clarifying sentence inside a point, not a
# new defined term; they must not reset the nesting stack.
_LEADIN_CONTINUATIONS = ("ملاحظة", "مثال", "تنبيه", "استثناء", "Note", "Example", "NB", "N.B")


# A form numbers its blanks, so `(1) ............` and `1. | | | |` enumerate
# fields rather than provisions. Leaders and table pipes are the whole tell:
# real provision text never consists of them.
_FILLER = ".·•_|—–-"
_PURE_FILLER_RE = re.compile(rf"^[\s{re.escape(_FILLER)}]+$")


def _is_form_field(rest: str) -> bool:
    """Is this line's body a blank to be filled rather than provision text?"""
    body = rest.strip()
    if not body:
        return False  # a bare marker takes its body from the lines beneath it
    # Count the filler itself, not the spacing: `| | | |` is a table row, while a
    # lone `-` or `.` is a placeholder inside a genuine numbered item.
    filler = sum(body.count(ch) for ch in _FILLER)
    if _PURE_FILLER_RE.match(body):
        return filler >= 3
    # `Nama : ...............` labels a blank. Require a long run so an ordinary
    # sentence with an ellipsis or a dashed compound stays a provision.
    run = max((len(m) for m in re.findall(rf"[{re.escape(_FILLER)}]+", body)), default=0)
    return run >= 10 and run / len(body) >= 0.4


def nest_enumerated_lines(lines: list[str]) -> list[str]:
    """Rewrite enumerated body lines as indented Bluebell ``POINT`` markup. Lead-in and
    non-enumerated lines stay plain text, and indentation is relative, the caller adding
    the container's base. A block with fewer than two enumerators returns unchanged, a
    lone "(1)" being prose rather than a list.
    """
    # First break apart any lines that run several points together inline, so a
    # source (or body-fill) that didn't put one point per line still nests.
    expanded: list[str] = []
    for ln in lines:
        expanded.extend(_split_inline(ln))
    lines = expanded

    # A numbered form field is not a provision, so it stays plain text and
    # never opens a level; two fields sharing a number are not a collision.
    parsed = [(ln, None if (p := _split(ln)) and _is_form_field(p[2]) else p) for ln in lines]
    if sum(1 for _, p in parsed if p) < 2:
        return lines

    out: list[str] = []
    # Each stack entry tracks (family, last-seen-ordinal) so we can detect a
    # backwards step at the same level and open a child level.
    stack: list[tuple[str, int]] = []
    for raw, p in parsed:
        if p is None:
            stripped_line = raw.strip()
            if (
                stack
                and _TERM_LEADIN_RE.match(stripped_line)
                and stripped_line.split(":")[0].split("：")[0].strip() not in _LEADIN_CONTINUATIONS
            ):
                stack.clear()
            if not stack:
                out.append(raw)
            else:
                out.append(f"{_INDENT * len(stack)}{raw.strip()}")
            continue
        marker, tok, rest, style = p
        # The level an ambiguous letter would resume is the one carrying its own
        # style, which is not always the deepest: a nested run in a different
        # style sits between them and must not be read as the letter's run.
        alpha_resume = next(
            (o for f, o in reversed(stack) if f == f"alpha-lat/{style}"),
            0,
        )
        fam = _family(
            tok,
            stack[-1][0] if stack else None,
            style,
            alpha_resume,
        )
        order = _token_order(tok, fam)
        if stack and stack[-1][0] == fam:
            last_order = stack[-1][1]
            # Reset opens a child only when last>1, a fresh sequence starting
            # at 1 mustn't collapse the first two items into nested form.
            if last_order > 1 and order and order <= last_order:
                stack.append((fam, order))
            else:
                stack[-1] = (fam, order)
        elif any(f == fam for f, _ in stack):
            idx = next(i for i, (f, _) in enumerate(stack) if f == fam)
            stack = stack[: idx + 1]
            stack[-1] = (fam, order)
        else:
            stack.append((fam, order))
        depth = len(stack) - 1
        out.append(f"{_INDENT * depth}POINT {marker}".rstrip())
        if rest:
            out.append(f"{_INDENT * (depth + 1)}{rest}")
    return out


__all__ = ["nest_enumerated_lines", "starts_with_enumerator"]
