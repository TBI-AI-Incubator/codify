"""Derive `data/jurisdictions/registry.json` from individual configs.

The registry is a generated artefact, do not edit it by hand. Instead, edit
the per-jurisdiction `config.json` and re-run this script (or let the
validator do it in --check mode).

Usage:
    uv run python scripts/build_registry.py         # rewrite registry.json
    uv run python scripts/build_registry.py --check # exit 2 if drift detected
"""

from __future__ import annotations

import json
import sys

from codify.jurisdictions import JURISDICTIONS_DIR, JurisdictionConfig

_DRIFT = 2  # --check found drift, as distinct from the script failing

REGISTRY_PATH = JURISDICTIONS_DIR / "registry.json"

REGISTRY_BANNER = (
    "// GENERATED — do not edit by hand. "
    "Run `uv run python scripts/build_registry.py` after modifying per-jurisdiction configs."
)


def build_registry() -> dict:
    jurisdictions: list[dict] = []
    for config_path in sorted(JURISDICTIONS_DIR.glob("*/config.json")):
        cfg = JurisdictionConfig.model_validate_json(config_path.read_text())
        if cfg.synthetic:  # fictional test-only jurisdictions stay out of the registry
            continue
        jurisdictions.append(
            {
                "code": cfg.code,
                "name": cfg.name,
                "type": cfg.type,
                "tradition": cfg.tradition,
                "languages": cfg.languages,
                "calendar": cfg.calendar,
                "tier": cfg.tier,
                "status": cfg.status,
                # Tier achievable in the current runtime after token-gating.
                "effective_tier": cfg.effective_tier,
                # Orthogonal coverage axis. in_scope by default; subsumed carries
                # subsumed_by (parent code); no_corpus / deferred have
                # no parent.
                "coverage_status": cfg.coverage_status,
                "subsumed_by": cfg.subsumed_by,
                # Whether this config ships in the open core, so the export can
                # read the registry alone rather than opening all 257 configs.
                "public_reference": cfg.public_reference,
            }
        )
    jurisdictions.sort(key=lambda j: j["code"])
    return {"_comment": REGISTRY_BANNER, "jurisdictions": jurisdictions}


def main() -> int:
    check_only = "--check" in sys.argv
    built = build_registry()
    built_json = json.dumps(built, indent=2, ensure_ascii=False) + "\n"

    if check_only:
        current = REGISTRY_PATH.read_text() if REGISTRY_PATH.exists() else ""
        if current != built_json:
            print("DRIFT: registry.json does not match derived version.", file=sys.stderr)
            print("Run `uv run python scripts/build_registry.py` to regenerate.", file=sys.stderr)
            # Distinct from 1, which any uncaught exception also gives: a caller
            # must be able to tell drift from the script failing to run.
            return _DRIFT
        print(f"registry.json matches derived ({len(built['jurisdictions'])} entries).")
        return 0

    REGISTRY_PATH.write_text(built_json)
    print(f"wrote {REGISTRY_PATH} ({len(built['jurisdictions'])} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
