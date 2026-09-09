"""Arabic-Indic digit-substitution repair for anchor sequences.

Older Arabic legal PDFs OCR these digits inconsistently: ``٦`` collapses to ``١``
when the hook is lost, ``٥`` to ``٠`` when the loop fills. The article-number
sequence in a well-formed statute is monotonic increasing, a prior far stronger
than any single OCR emission, so a break in it can be repaired deterministically:
generate candidates by single substitutions from the confusion matrix, score each
against the local prior, adopt the winner when it wins by a margin.

Scope guard: one substitution per anchor per pass, and only on `article`-class
anchors, since chapters and higher use ordinal words that do not OCR into the
same confusion set.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from codify.lang import normalise_digits

logger = structlog.get_logger()

_ASCII_TO_ARABIC_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
_ASCII_TO_EXTENDED_INDIC = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _match_source_script(original: str, candidate: str) -> str:
    """Render an ASCII repair candidate in the original number's digit
    script, so a repaired ``مادة (١)`` stays Arabic-Indic (``٦``) instead
    of landing a lone ASCII ``6`` in an otherwise-Arabic sequence."""
    if any("٠" <= c <= "٩" for c in original):
        return candidate.translate(_ASCII_TO_ARABIC_INDIC)
    if any("۰" <= c <= "۹" for c in original):
        return candidate.translate(_ASCII_TO_EXTENDED_INDIC)
    return candidate


# Confusion pairs from observed defects and adjacent Naskh typesetting.
# Bidirectional: an emitted ``١`` might have been ``٦`` in the source, or the
# reverse. The Latin-digit form is what ``normalise_digits`` leaves behind.
_CONFUSION_PAIRS: tuple[tuple[str, str], ...] = (
    ("1", "6"),  # ٦ hook lost → ١ vertical (reviewer's exemplar)
    ("0", "5"),  # ٥ heart loop filled → ٠ dot
    ("8", "9"),  # ٨ / ٩ share a top-hook
    ("2", "3"),  # ٢ / ٣ Naskh variants
    ("3", "4"),  # ٤ / ٣ Naskh variants
)


def _confusion_neighbours(digit: str) -> tuple[str, ...]:
    """All digits that ``digit`` could plausibly have been misread as, or
    vice versa. Excludes the digit itself."""
    out: set[str] = set()
    for a, b in _CONFUSION_PAIRS:
        if digit == a:
            out.add(b)
        elif digit == b:
            out.add(a)
    return tuple(sorted(out))


@dataclass(frozen=True)
class DigitRepair:
    """A single per-anchor correction applied by the repair pass. ``index`` is the anchor's
    position in the ordered list; ``from_num`` and ``to_num`` are keyed off
    ``StructuralAnchor.number`` so an audit can trace the change back to the original
    OCR output.
    """

    index: int
    from_num: str
    to_num: str
    reason: str


def _candidate_substitutions(number: str) -> list[str]:
    """Every single-position digit substitution reachable through the confusion matrix.
    ``number`` is expected already ASCII-folded. Digit order is preserved and non-digit
    characters are untouched, such as the ``A`` in a ``5A`` article number.
    """
    out: list[str] = []
    for idx, ch in enumerate(number):
        for repl in _confusion_neighbours(ch):
            out.append(number[:idx] + repl + number[idx + 1 :])
    return out


def _score(candidate: int, prev: int | None, next_num: int | None) -> int:
    """Higher is better. A candidate that fits ``prev + 1`` beats
    ``prev + 2`` beats larger gaps. When a ``next_num`` is available, a
    candidate that also sits below it (``prev < candidate < next``) beats
    one that only fits ``prev``. Non-monotonic candidates score at the floor.
    """
    score = 0
    if prev is not None:
        if candidate == prev + 1:
            score += 10
        elif candidate == prev + 2:
            score += 6
        elif prev < candidate <= prev + 5:
            score += 3
        elif candidate <= prev:
            return -100
    if next_num is not None:
        if candidate < next_num:
            score += 5
            if candidate + 1 == next_num:
                score += 5
        else:
            return -100
    return score


def _fits_locally(candidate: int, prev: int | None, next_num: int | None) -> bool:
    """Does the candidate continue its neighbours, rather than merely clear a floor?

    `_score` credits any candidate below `next`, so a number fitting nothing can
    still beat the observed one and be written in as a repair. A substitution is
    only evidence when it lands where the sequence expects it.
    """
    if prev is None:
        # No trusted prior is no evidence. Scoring alone would still accept a
        # candidate that merely continues the corrupted neighbour ahead of it,
        # which rewrites sound numbers rather than abstaining.
        return False
    if prev < candidate <= prev + 5:
        return True
    return next_num is not None and candidate + 1 == next_num


def _to_int(num: str) -> int | None:
    """ASCII-fold and int() the number; return None if it does not parse
    as a base-10 integer (e.g. ``5A``, roman ``IV``, an ordinal word)."""
    folded = normalise_digits(num).strip()
    if not folded.isdigit():
        return None
    return int(folded)


# Above this jump a number is suspicious enough to consider substitutions; below it
# gaps are assumed legitimate (renumbered, reserved, annex). The observed defects
# (article 1 after 5, article 68 where 18 belonged) break monotonicity outright or
# overshoot by 40+, so 20 catches them without touching normal gaps.
_ANOMALY_STEP_CEILING = 20


def _is_anomalous(current: int, prev: int | None) -> bool:
    """Whether the repair pass considers substitutions at all. Strict non-monotonicity
    (``current <= prev``) always counts, as does a forward jump past
    ``_ANOMALY_STEP_CEILING``: OCR substitutions blow a number up by an order of
    magnitude (7 -> 68), while legitimate renumbering rarely skips more than a few.
    """
    if prev is None:
        return False
    if current <= prev:
        return True
    return current - prev > _ANOMALY_STEP_CEILING


def _run_hypothesis(prev: int, first_folded: str) -> tuple[int, str, str] | None:
    """Hypothesise the single-position substitution that would make the
    first member of an anomalous run continue ``prev``: compare it against
    ``prev + 1`` digit-by-digit; exactly one differing position whose digit
    pair sits in the confusion matrix yields ``(position, from, to)``."""
    expected = str(prev + 1)
    if len(expected) != len(first_folded):
        return None
    diffs = [p for p in range(len(expected)) if expected[p] != first_folded[p]]
    if len(diffs) != 1:
        return None
    p = diffs[0]
    if expected[p] not in _confusion_neighbours(first_folded[p]):
        return None
    return p, first_folded[p], expected[p]


def _repair_shifted_runs(numbers: list[str | None]) -> tuple[list[str | None], list[DigitRepair]]:
    """Repair whole shifted runs the per-anchor pass cannot see.

    When OCR misreads one digit consistently ("٦١".."٦٩" to "١١".."١٩"), each member
    fits its corrupted neighbour and only the first breaks the prior, so the per-anchor
    pass accepts the tail. This finds the maximal monotonic run from the anomaly,
    hypothesises one substitution from the first member against ``prev + 1``, and
    accepts only when every member carries that digit at that position, the transformed
    run still ascends, and it fits between the last trusted number and the first number
    after the run. A run at document end has no closing bracket and is accepted on the
    lower one alone.
    """
    repaired: list[str | None] = list(numbers)
    corrections: list[DigitRepair] = []
    idxs = [i for i, n in enumerate(repaired) if n is not None and _to_int(n) is not None]
    vals = {i: v for i in idxs if (v := _to_int(repaired[i] or "")) is not None}
    prev: int | None = None
    # A declined anomaly still becomes the prior, so the run after it must not be
    # fitted to it: that reads the one damaged number as the truth and the run
    # correcting it as the damage.
    prev_trusted = True
    k = 0
    while k < len(idxs):
        i = idxs[k]
        cur = vals[i]
        if prev is None or not _is_anomalous(cur, prev):
            prev = cur
            prev_trusted = True
            k += 1
            continue
        first_folded = normalise_digits(repaired[i] or "").strip()
        hyp = _run_hypothesis(prev, first_folded) if prev_trusted else None
        if hyp is not None:
            p, from_digit, to_digit = hyp
            # Extend only over ascending members carrying the hypothesised digit at
            # that position. The first that does not, such as a trusted "70" after
            # "11..19", is the closing bracket rather than part of the run.
            end = k
            while end + 1 < len(idxs):
                nxt_folded = normalise_digits(repaired[idxs[end + 1]] or "").strip()
                if (
                    vals[idxs[end + 1]] > vals[idxs[end]]
                    and len(nxt_folded) == len(first_folded)
                    and nxt_folded[p] == from_digit
                ):
                    end += 1
                else:
                    break
            run = idxs[k : end + 1]
            nxt = vals[idxs[end + 1]] if end + 1 < len(idxs) else None
            folded = [normalise_digits(repaired[j] or "").strip() for j in run]
            if len(run) >= 2:
                transformed = [int(f[:p] + to_digit + f[p + 1 :]) for f in folded]
                ascending = all(a < b for a, b in zip(transformed, transformed[1:]))
                fits = transformed[0] > prev and (nxt is None or transformed[-1] < nxt)
                if ascending and fits:
                    for j, f, t in zip(run, folded, transformed):
                        original = repaired[j] or ""
                        fixed = _match_source_script(original, str(t))
                        repaired[j] = fixed
                        bracket = f"{prev}..{nxt}" if nxt is not None else f"{prev}..(document end)"
                        reason = (
                            f"run shift {from_digit}->{to_digit} at digit {p}: "
                            f"bracketed {bracket}, run of {len(run)}"
                        )
                        corrections.append(
                            DigitRepair(index=j, from_num=original, to_num=fixed, reason=reason)
                        )
                        logger.info(
                            "digit_run_repaired",
                            index=j,
                            from_num=original,
                            to_num=fixed,
                            position=p,
                            from_digit=from_digit,
                            to_digit=to_digit,
                            prev=prev,
                            next=nxt,
                            run_len=len(run),
                            # Margin: how the transformed run fits its brackets
                            # (1 = seamless continuation) vs how far the observed
                            # value sat from the expected continuation.
                            fit_start=transformed[0] - prev,
                            fit_end=(nxt - transformed[-1]) if nxt is not None else None,
                            observed_offset=cur - (prev + 1),
                        )
                    prev = transformed[-1]
                    k = end + 1
                    continue
        # No consistent hypothesis, so leave it to the per-anchor pass. Logged
        # because the operator should see what the detector refused, not only what it
        # rewrote; this is reached only on an already-fired anomaly.
        logger.info(
            "digit_run_anomaly_unrepaired",
            index=i,
            number=repaired[i],
            prev=prev,
            prev_trusted=prev_trusted,
        )
        prev = cur
        prev_trusted = False
        k += 1
    return repaired, corrections


def repair_number_sequence(numbers: list[str | None]) -> tuple[list[str | None], list[DigitRepair]]:
    """Walk ``numbers`` left to right; on an anomaly, look for a single-position
    substitution that better fits the prior. Returns ``(repaired_numbers, corrections)``.

    The run-level pass goes first, because a consistently misread run defeats the
    per-anchor walk. The prior is anchored on the last accepted digit-only number, so a
    roman-numeral or ordinal-word neighbour is skipped. Only integer article numbers are
    repaired.
    """
    repaired, corrections = _repair_shifted_runs(numbers)
    last_int: int | None = None
    # A number the pass declined to repair is not evidence for what follows it.
    last_trusted = True
    for i, num in enumerate(repaired):
        if num is None:
            continue
        current = _to_int(num)
        if current is None:
            continue
        if not _is_anomalous(current, last_int):
            last_int = current
            last_trusted = True
            continue
        prior = last_int if last_trusted else None
        next_int: int | None = None
        for j in range(i + 1, len(repaired)):
            nxt = repaired[j]
            if nxt is None:
                continue
            next_int = _to_int(nxt)
            if next_int is not None:
                break
        observed_score = _score(current, prior, next_int)
        folded = normalise_digits(num).strip()
        best: tuple[int, str, int] | None = None
        for candidate in _candidate_substitutions(folded):
            cand_int = _to_int(candidate)
            if cand_int is None:
                continue
            cand_score = _score(cand_int, prior, next_int)
            if cand_score <= observed_score:
                continue
            if best is None or cand_score > best[0]:
                best = (cand_score, candidate, cand_int)
        if (
            best is not None
            and best[0] - observed_score >= 6
            and _fits_locally(best[2], prior, next_int)
        ):
            fixed = _match_source_script(num, best[1])
            repaired[i] = fixed
            corrections.append(
                DigitRepair(
                    index=i,
                    from_num=num,
                    to_num=fixed,
                    reason=(
                        f"monotonic prior: {prior} -> {best[2]} (was {current}); "
                        f"neighbour next={next_int}"
                    ),
                )
            )
            last_int = best[2]
            last_trusted = True
        else:
            last_int = current
            last_trusted = False
    return repaired, corrections
