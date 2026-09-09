"""Fix all known issues in jurisdiction config.json files.

Idempotent, safe to run multiple times.

Usage:
    uv run python scripts/fix_jurisdiction_configs.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The package's own resolver, which is what the wheel and every caller use. A
# path guessed from this file's location pointed inside the package, where no
# config tree exists, so main() walked nothing.
from codify.jurisdictions import JURISDICTIONS_DIR

VALID_STATUSES = {"production", "draft", "stub"}
VALID_LEVELS = {"higher", "basic", "subdivision", "grouping", "presentational"}
LEVEL_REMAP = {
    "highest": "higher",
    "higher_division": "higher",
    "higher_div": "higher",
    "mid": "higher",
    "sub_mid": "higher",
    "initial": "draft",
    "research": "draft",
    "research_complete": "draft",
}


def fix_config(path: Path) -> list[str]:
    """Fix a single config.json. Returns list of fixes applied."""
    fixes = []
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return [f"{path.parent.name}: JSON_PARSE_ERROR: {e}"]
    code = raw.get("code", path.parent.name)

    # --- Fix A: validation.notes (plural) → note (singular) ---
    v = raw.get("validation", {})
    if "notes" in v:
        val = v.pop("notes")
        if isinstance(val, list):
            v["note"] = "; ".join(str(x) for x in val)
        else:
            v["note"] = str(val)
        raw["validation"] = v
        fixes.append(f"{code}: validation.notes → note")

    # Also catch validation_note, validation_notes
    for bad_key in ("validation_note", "validation_notes"):
        if bad_key in v:
            val = v.pop(bad_key)
            v["note"] = str(val) if not isinstance(val, list) else "; ".join(str(x) for x in val)
            raw["validation"] = v
            fixes.append(f"{code}: validation.{bad_key} → note")

    # --- Fix B: Invalid validation.status ---
    status = v.get("status", "draft")
    if status not in VALID_STATUSES:
        v["status"] = LEVEL_REMAP.get(status, "draft")
        raw["validation"] = v
        fixes.append(f"{code}: validation.status '{status}' → '{v['status']}'")

    # --- Fix C: Invalid HierarchyLevel values ---
    for dc_name, dc in raw.get("document_classes", {}).items():
        for h in dc.get("hierarchy", []):
            level = h.get("level", "")
            if level and level not in VALID_LEVELS:
                new_level = LEVEL_REMAP.get(level, "higher")
                h["level"] = new_level
                fixes.append(f"{code}: {dc_name} hierarchy level '{level}' → '{new_level}'")

    # --- Fix D: Missing HContainer local_term ---
    for dc_name, dc in raw.get("document_classes", {}).items():
        for hc in dc.get("hcontainers", []):
            if "local_term" not in hc:
                # Derive from name, label, or description
                name = hc.get("name", hc.get("label", "unknown"))
                hc["local_term"] = name.replace("_", " ").title()
                fixes.append(
                    f"{code}: {dc_name} hcontainer '{name}' → local_term '{hc['local_term']}'"
                )

            # Also ensure 'name' exists (required field)
            if "name" not in hc:
                if "hcontainer_name" in hc:
                    hc["name"] = hc.pop("hcontainer_name")
                    fixes.append(f"{code}: {dc_name} hcontainer hcontainer_name → name")
                elif "local_term" in hc:
                    hc["name"] = hc["local_term"].lower().replace(" ", "_")
                    fixes.append(f"{code}: {dc_name} hcontainer derived name from local_term")

    # --- Fix E: TLC eId with spaces ---
    for tlc in raw.get("core_tlcs", []):
        eid = tlc.get("eId", "")
        if " " in eid:
            tlc["eId"] = eid.replace(" ", "")
            fixes.append(f"{code}: TLC eId space removed '{eid}' → '{tlc['eId']}'")

    # --- Fix F: bluebell_keyword null → None is OK now (schema allows it) ---
    # No action needed, schema field is str | None = None

    # --- Fix G: Invalid tradition values ---
    valid_traditions = {
        "civil_law",
        "common_law",
        "islamic",
        "customary",
        "hindu",
        "confucian",
        "jewish",
        "socialist",
        "sui_generis",
    }
    traditions = raw.get("tradition", [])
    cleaned = [t for t in traditions if t in valid_traditions]
    removed = [t for t in traditions if t not in valid_traditions]
    if removed:
        # Try to remap known invalid values
        for r in removed:
            if r == "roman_dutch":
                cleaned.append("civil_law")
            elif r == "nordic":
                pass  # Already have civil_law typically
        raw["tradition"] = cleaned if cleaned else ["civil_law"]
        fixes.append(f"{code}: tradition removed invalid {removed}")

    # --- Fix H: Calendar values ---
    valid_calendars = {
        "gregorian",
        "lunar_hijri",
        "solar_hijri",
        "ethiopian",
        "japanese_era",
        "minguo",
        "buddhist_era",
        "dual",
    }
    # The retired value covered two systems that convert differently, so it maps
    # by country rather than falling through to gregorian and silently losing it.
    legacy_imperial = {"jp": "japanese_era", "tw": "minguo"}
    cal = raw.get("calendar", "gregorian")
    if cal == "imperial_era":
        if code not in legacy_imperial:
            raise ValueError(f"{code}: imperial_era names no system for this country")
        raw["calendar"] = legacy_imperial[code]
        cal = raw["calendar"]
        fixes.append(f"{code}: calendar 'imperial_era' → '{cal}'")
    if cal not in valid_calendars:
        if "hijri" in cal.lower() and "gregorian" in cal.lower():
            raw["calendar"] = "dual"
        elif "hijri" in cal.lower():
            raw["calendar"] = "lunar_hijri"
        else:
            raw["calendar"] = "gregorian"
        fixes.append(f"{code}: calendar '{cal}' → '{raw['calendar']}'")

    if fixes:
        with open(path, "w") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
            f.write("\n")

    return fixes


def fix_hungary_mapping(path: Path) -> list[str]:
    """ADR 002: Fix Hungary § → article mapping."""
    fixes = []
    raw = json.loads(path.read_text())

    for dc_name in ("act", "rendelet"):
        dc = raw.get("document_classes", {}).get(dc_name)
        if not dc:
            continue

        if dc.get("basic_unit") == "section":
            dc["basic_unit"] = "article"
            fixes.append(f"hu: {dc_name} basic_unit section → article")

        for h in dc.get("hierarchy", []):
            if h.get("level") == "basic":
                if h.get("bluebell_keyword") == "SECTION":
                    h["bluebell_keyword"] = "ARTICLE"
                    fixes.append(f"hu: {dc_name} basic bluebell_keyword SECTION → ARTICLE")
                if h.get("akn_element") == "section":
                    h["akn_element"] = "article"
                    fixes.append(f"hu: {dc_name} basic akn_element section → article")

    if fixes:
        with open(path, "w") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
            f.write("\n")

    return fixes


def main() -> int:
    all_fixes = []

    # Fix all configs
    for d in sorted(JURISDICTIONS_DIR.iterdir()):
        config_path = d / "config.json"
        if config_path.is_file():
            fixes = fix_config(config_path)
            all_fixes.extend(fixes)

    # Apply Hungary ADR 002
    hu_path = JURISDICTIONS_DIR / "hu" / "config.json"
    if hu_path.exists():
        fixes = fix_hungary_mapping(hu_path)
        all_fixes.extend(fixes)

    juris_count = len({f.split(":")[0] for f in all_fixes})
    print(f"Applied {len(all_fixes)} fixes across {juris_count} jurisdictions:\n")
    for fix in all_fixes:
        print(f"  {fix}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
