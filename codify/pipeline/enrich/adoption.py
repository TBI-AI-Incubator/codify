"""Does an instrument adopt another body's numbered text?

Two numbering series in one file make every count read the adopted articles as the
instrument's own, so a complete document looks partial. Phrases come from jurisdiction
config, so no legal tradition's vocabulary sits here.
"""

from __future__ import annotations

import re

from codify.jurisdictions import JurisdictionConfig

# The title block, where an instrument states what it is for. A law citing a convention
# in its recitals says the same words further down, so the whole page over-fires.
TITLE_BLOCK_CHARS = 250


def _pattern(markers: tuple[str, ...]) -> re.Pattern[str] | None:
    if not markers:
        return None
    # Split rather than substituted into an escaped string, where the replacement's
    # own backslashes are escaped again and the pattern matches nothing.
    alts = "|".join(
        r"\s+".join(re.escape(word) for word in m.split()) for m in markers if m.strip()
    )
    return re.compile(alts) if alts else None


def adopts_external_text(text: str, config: JurisdictionConfig | None) -> bool:
    """True where the title block declares the instrument adopts another text. False
    without markers declared, which is most jurisdictions: silence, not a finding."""
    if config is None or not text:
        return False
    pattern = _pattern(tuple(config.adoption_markers))
    if pattern is None:
        return False
    return pattern.search(text[:TITLE_BLOCK_CHARS]) is not None
