"""The evidence boundary the repair tools read through.

A Protocol, injected at `build_agent` time like the old `render_page` callback,
so `codify.repair` stays free of MinIO/DBOS I/O and the agent tests offline
with a stub. Every method is a read over persisted, immutable evidence: tools
re-execute on durable-workflow recovery (only model calls are checkpointed), so
nothing here may write or depend on mutable state.
"""

from __future__ import annotations

from typing import Any, Protocol


class EvidenceStore(Protocol):
    """Read-only access to page evidence for one version."""

    async def render_page(self, object_key: str, page_no: int) -> bytes:
        """PNG of the rendered source page."""
        ...

    async def render_region(
        self, version_id: str, object_key: str, page_no: int, block_index: int
    ) -> bytes:
        """PNG of one layout block, cropped from a high-resolution re-render.

        Raises when the page has no persisted layout or the index is out of
        range, the tool layer turns that into a sentence for the model.
        """
        ...

    async def page_read(self, version_id: str, page_no: int) -> dict[str, Any] | None:
        """The persisted read of one page: text, rival_text, divergence,
        verdict metrics and block summaries. None when no read is retained."""
        ...
