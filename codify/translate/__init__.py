"""Legislative translation, anchor-scaffolded and terminology-consistent.
`translate_document` walks the source AKN, builds binding notes, fans out per-eid
body-fill, then patches a source clone via `apply_translation_to_akn`. eIds survive
because structure never transits the LLM and the writer never reparses.
"""

from codify.translate.anchors import (
    SourceUnit,
    WalkSummary,
    build_translation_scaffold,
    walk_source,
    walk_source_units,
)
from codify.translate.audit import audit_translation
from codify.translate.notes import build_translation_notes, notes_to_prompt
from codify.translate.translate import (
    TranslationResult,
    translate_document,
    translate_title,
)
from codify.translate.translate_bodies import (
    BodyFillOutcome,
    TranslatedBatchResponse,
    TranslatedBlock,
    translate_bodies,
)

__all__ = [
    "BodyFillOutcome",
    "SourceUnit",
    "TranslatedBatchResponse",
    "TranslatedBlock",
    "TranslationResult",
    "WalkSummary",
    "audit_translation",
    "build_translation_notes",
    "build_translation_scaffold",
    "notes_to_prompt",
    "translate_bodies",
    "translate_document",
    "translate_title",
    "walk_source",
    "walk_source_units",
]
