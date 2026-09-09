"""Export the open-core subset to a scratch directory: the allowlisted configs
and the package that reads them, so the result is a tree an ingest can run in.

`JURISDICTIONS_DIR` resolves relative to the package, so the two have to travel
together or the export resolves back to the full set and proves nothing.

    uv run python packages/codify/scripts/export_public_configs.py /tmp/open-core
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import codify
from codify.jurisdictions import JURISDICTIONS_DIR
from codify.open_wheel import ships_in_open_wheel


def public_codes() -> list[str]:
    """The configs the open tree carries, on the wheel hook's own predicate.
    Reading `public_reference` off the registry missed the synthetic
    jurisdictions, which have no registry entry by design."""
    return sorted(
        p.parent.name for p in JURISDICTIONS_DIR.glob("*/config.json") if ships_in_open_wheel(p)
    )


def export(dest: Path) -> int:
    codes = public_codes()
    if not codes:
        # An empty allowlist means a misread flag, not an empty open core, and
        # an empty export would look like a passing run with nothing in it.
        print("no jurisdiction is flagged public_reference; nothing to export", file=sys.stderr)
        return 1
    # Refuse rather than merge. `dirs_exist_ok` would leave behind a jurisdiction
    # since removed from the allowlist, and files since deleted from one still on
    # it, so a re-run could publish exactly what the allowlist excludes.
    if dest.exists() and any(dest.iterdir()):
        print(f"{dest} is not empty; refusing to merge into it", file=sys.stderr)
        return 1
    out = dest / "data" / "jurisdictions"
    out.mkdir(parents=True, exist_ok=True)
    for code in codes:
        src = JURISDICTIONS_DIR / code
        shutil.copytree(src, out / code, dirs_exist_ok=True)

    # The registry travels filtered rather than whole: shipping all 257 entries
    # would publish the list of jurisdictions studied, which is the disclosure
    # the allowlist exists to avoid.
    registry = json.loads((JURISDICTIONS_DIR / "registry.json").read_text())
    registry["jurisdictions"] = [j for j in registry["jurisdictions"] if j["code"] in set(codes)]
    (out / "registry.json").write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n")

    # Whole, not filtered: it lists institutions, not jurisdictions, and every
    # real config's membership validator reads it.
    shutil.copy(JURISDICTIONS_DIR / "supranational_bodies.json", out / "supranational_bodies.json")

    # The package travels too, at the depth `JURISDICTIONS_DIR` expects, so the
    # exported tree resolves to its own configs rather than back to the full set.
    package = Path(codify.__file__).parent
    shutil.copytree(package, dest / "packages" / "codify" / "codify")

    print(f"exported {len(codes)} configs and the codify package to {dest}: {', '.join(codes)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", type=Path, help="destination directory (created if absent)")
    return export(parser.parse_args().dest)


if __name__ == "__main__":
    sys.exit(main())
