"""Bulk-deliverable packaging: manifest schema, quality report, zip packager.

Consumed by the `bulk_deliverable` DBOS workflow. This module owns the operator
contract (the manifest) and the deliverable layout; the workflow owns orchestration.
"""

from codify.deliverable.manifest import DeliverableManifest, LawEntry
from codify.deliverable.packager import LawArtifacts, build_deliverable

__all__ = ["DeliverableManifest", "LawEntry", "LawArtifacts", "build_deliverable"]
