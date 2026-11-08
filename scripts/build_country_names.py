"""Build `codify/countries.json` from pinned Unicode CLDR data and, given a downloaded
copy, the EU Publications Office country authority table (`--eu-table country.xml`).

Run with `uv run python scripts/build_country_names.py`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

from lxml import etree

from codify.countries import name_forms
from codify.lang import ISO1_TO_3

OUTPUT = Path(__file__).resolve().parents[1] / "codify" / "countries.json"

CLDR_VERSION = "49.0.0"
# npm's published integrity for each tarball; a changed download fails the build.
CLDR_PACKAGES = {
    "cldr-core": "sha512-2Z2dYeDO95AbKIG9dIUdZy6DD4f0hhHjTYnaHZj/9JTQ82pX5EsJlCs2"
    "BXPWi4vd1d98MwotOivh4Uthxwri4A==",
    "cldr-localenames-full": "sha512-nTBCysDVK+JHb2hPhb26fJGp9+Lw0mOiqVsJ/9aTEQIduBTn"
    "ghTSLgZitsM7FLa1Vjr0K0hfBQMdBgoyDxK0nQ==",
}
CLDR_NOTICE = (
    "Unicode CLDR data, Copyright © 2001–2026 Unicode, Inc., under the Unicode "
    "License v3; see licenses/Unicode-CLDR.txt."
)
EU_NOTICE = (
    "Contains data from the country authority table, © European Union, Publications "
    "Office of the European Union; names selected by language and keyed by ISO code."
)

# Languages whose country names take no case endings; a genitive -s is still missed.
LANGUAGES = frozenset("bul dan deu eng fra ind ita ltz mkd nld nor por spa swe".split())
ALT_FORMS = frozenset({"", "short", "variant"})


def _download(package: str) -> tarfile.TarFile:
    url = f"https://registry.npmjs.org/{package}/-/{package}-{CLDR_VERSION}.tgz"
    with urllib.request.urlopen(url, timeout=120) as response:
        body = response.read()
    digest = "sha512-" + base64.b64encode(hashlib.sha512(body).digest()).decode()
    if digest != CLDR_PACKAGES[package]:
        raise SystemExit(f"{url} does not match its pinned integrity")
    return tarfile.open(fileobj=io.BytesIO(body), mode="r:gz")


def _supplemental(core: tarfile.TarFile, name: str) -> Any:
    member = core.extractfile(f"package/supplemental/{name}.json")
    assert member is not None
    return json.load(member)["supplemental"]


def _user_assigned(code: str) -> bool:
    """ISO 3166-1's user-assigned alpha-2 codes: AA, QM to QZ, XA to XZ and ZZ."""
    return code in {"AA", "ZZ"} or code[0] == "X" or (code[0] == "Q" and code[1] >= "M")


def cldr_countries(core: tarfile.TarFile) -> frozenset[str]:
    """Alpha-2 codes with an ISO numeric code that are not user-assigned, withdrawn
    or a region containing others (the EU, the UN)."""
    mappings = _supplemental(core, "codeMappings")["codeMappings"]
    regions = _supplemental(core, "territoryContainment")["territoryContainment"]
    withdrawn = _supplemental(core, "aliases")["metadata"]["alias"]["territoryAlias"]
    return frozenset(
        code
        for code, mapping in mappings.items()
        if len(code) == 2
        and code.isalpha()
        and mapping.get("_numeric")
        and not _user_assigned(code)
        and code not in regions
        and code not in withdrawn
    )


def cldr_names(
    locales: tarfile.TarFile, countries: frozenset[str]
) -> dict[str, dict[str, list[str]]]:
    """Every locale of each language, its own locale first, so a code's first name is
    the language's standard one."""
    files: dict[str, str] = {}
    for member in locales.getmembers():
        parts = member.name.split("/")
        if len(parts) == 4 and parts[1] == "main" and parts[3] == "territories.json":
            files[parts[2]] = member.name
    out: dict[str, dict[str, list[str]]] = {}
    for locale in sorted(files, key=lambda loc: ("-" in loc, loc)):
        language = ISO1_TO_3.get(locale.split("-")[0])
        if language not in LANGUAGES:
            continue
        stream = locales.extractfile(files[locale])
        assert stream is not None
        territories = json.load(stream)["main"][locale]["localeDisplayNames"]["territories"]
        table = out.setdefault(language, {})
        for key, name in territories.items():
            code, _, alt = key.partition("-alt-")
            if code in countries and alt in ALT_FORMS:
                _add(table, code, name)
    return out


def eu_names(path: Path, countries: frozenset[str]) -> tuple[str, dict[str, dict[str, list[str]]]]:
    """The table's version and its short and full names, deprecated records included:
    a former official name is still the name an older law uses."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
    root = etree.parse(str(path), parser).getroot()
    version = root.get("version")
    records = root.findall("record")
    if root.get("table.id") != "country" or not version or not records:
        raise SystemExit(f"{path} is not a country authority table with a version and records")
    out: dict[str, dict[str, list[str]]] = {}
    for record in records:
        code = (record.findtext("code-3166-1-alpha-2") or "").strip()
        if code not in countries:
            continue
        for name in record.findall("label/lg.version") + record.findall("long.label/lg.version"):
            language = name.get("lg")
            if language in LANGUAGES and name.text and name.text.strip():
                _add(out.setdefault(language, {}), code, name.text.strip())
    if not out:
        raise SystemExit(f"{path} names no country in {sorted(LANGUAGES)}")
    return version, out


def _add(table: dict[str, list[str]], code: str, name: str) -> None:
    names = table.setdefault(code, [])
    if name not in names:
        names.append(name)


def merge(*parts: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    """Each language's names from every source. A name matching as another country's
    does once folded and capitalised as the matcher does names neither."""
    out: dict[str, dict[str, list[str]]] = {}
    for part in parts:
        for language, table in part.items():
            for code, names in table.items():
                for name in names:
                    _add(out.setdefault(language, {}), code, name)
    for language, table in out.items():
        owners: dict[str, set[str]] = {}
        for code, names in table.items():
            for name in names:
                for form in name_forms(name):
                    owners.setdefault(form, set()).add(code)
        shared = {
            name
            for names in table.values()
            for name in names
            if any(len(owners[form]) > 1 for form in name_forms(name))
        }
        for name in sorted(shared):
            codes = sorted(set().union(*(owners[form] for form in name_forms(name))))
            print(f"{language}: dropped {name!r}, which matches as {codes}")
        for code in table:
            table[code] = [n for n in table[code] if n not in shared]
        nameless = sorted(code for code, names in table.items() if not names)
        if nameless:
            raise SystemExit(f"{language}: {nameless} would be left with no name")
    return {language: dict(sorted(table.items())) for language, table in sorted(out.items())}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build codify/countries.json.")
    parser.add_argument("--eu-table", type=Path, help="a downloaded country authority table (XML)")
    args = parser.parse_args()

    countries = cldr_countries(_download("cldr-core"))
    parts = [cldr_names(_download("cldr-localenames-full"), countries)]
    sources: dict[str, Any] = {
        "cldr": {"version": CLDR_VERSION, "packages": CLDR_PACKAGES, "notice": CLDR_NOTICE}
    }
    if args.eu_table:
        version, eu = eu_names(args.eu_table, countries)
        parts.append(eu)
        sources["eu"] = {"table": "country", "version": version, "notice": EU_NOTICE}
    names = merge(*parts)
    missing = LANGUAGES - names.keys()
    if missing:
        raise SystemExit(f"no names for {sorted(missing)}")
    payload = {"sources": sources, "names": names}
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(countries)} countries; " + ", ".join(f"{k} {len(v)}" for k, v in names.items()))


if __name__ == "__main__":
    main()
