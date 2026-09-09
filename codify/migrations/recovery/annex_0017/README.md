# Historical annex migration

The adjacent file is the exact original revision, preserved for operator investigation. It is outside active Alembic discovery and is not a deployable migration bundle: its ledger/delegation prerequisites were unpublished and are not imported here. The manifest records their source commits.

Clean installations use `0018_annex_admission` after genuine `0014_effect_publisher_id`. No alias or stamp maps historical 0015/0016/0017 to that path. Normal Alembic traversal intentionally rejects those unknown stamps.

For an installation carrying a historical stamp, stop. Preserve a restorable backup, actual revision rows and schema fingerprint, ledger/delegation tables and data, annex runs/artifacts, and source image digest. Obtain and verify the authentic deployed migration graph. Any forward adoption must separately represent those additional schemas and receive operator review; this PR does not supply one. Never downgrade the historical 0017 to repair compatibility: its downgrade deletes transcript evidence and cannot undo document splices.
