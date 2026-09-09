"""Minimal authored URI and manifest fixtures for mocked adapter tests.

These are protocol examples, not jurisdiction configurations or real corpora.
"""

import json


def install_protocol_data(monkeypatch, tmp_path):
    from codify import jurisdictions
    from codify.acquisition import manifest

    root = tmp_path / "protocol-data"
    patterns = {
        "eu": {"directive": "dir", "regulation": "reg", "decision": "dec"},
        "rs": {"zakon": "", "uredba": "uredba"},
        "ua": {"zakon": "", "law": ""},
        "al": {"ligj": "ligj"},
    }
    adapters = {
        "al": [dict(kind="pdf_gazette", name="qbz", base_url="https://qbz.gov.al")],
        "eu": [
            dict(kind="eurlex_cellar", name="eu-sparql"),
            dict(kind="bulk_xml_archive", name="eu-datadump"),
        ],
        "rs": [
            dict(
                kind="paragraf_propisi",
                name="paragraf",
                base_url="https://www.paragraf.rs",
            )
        ],
        "ua": [
            dict(
                kind="html_portal",
                name="zakon-rada",
                base_url="https://zakon.rada.gov.ua",
            )
        ],
    }
    base = jurisdictions.load_config("xa").model_dump()
    for country, classes in patterns.items():
        target = root / country
        target.mkdir(parents=True)
        raw = base | {
            "code": country,
            "name": "Synthetic protocol fixture",
            "source_adapters": adapters[country],
            "frbr": {
                "country_code": country,
                "uri_patterns": {
                    kind: f"/akn/{country}/act/"
                    + (token + "/" if token else "")
                    + "{year}/{number}"
                    for kind, token in classes.items()
                },
            },
        }
        (target / "config.json").write_text(json.dumps(raw))
        refs = []
        kind = next(iter(classes))
        for index in range(1, 6):
            extra = {}
            if country == "eu":
                extra = {"celex": f"32042L{index:04d}"}
            if country == "rs":
                extra = {"slug": f"synthetic_observatory_{index}"}
            refs.append(
                dict(
                    jurisdiction_code=country,
                    doctype=kind,
                    year=2042,
                    number=str(index),
                    extra=extra,
                )
            )
        (target / "corpus.json").write_text(
            json.dumps(
                {
                    "jurisdiction_code": country,
                    "notes": "Invented test records, not a real corpus",
                    "refs": refs,
                }
            )
        )
    original = jurisdictions.try_load_config

    def configured(country):
        path = root / country / "config.json"
        if path.exists():
            return jurisdictions.JurisdictionConfig.model_validate_json(path.read_text())
        return original(country)

    from codify import frbr

    monkeypatch.setattr(frbr, "try_load_config", configured)
    monkeypatch.setattr(jurisdictions, "try_load_config", configured)
    monkeypatch.setattr(manifest, "JURISDICTIONS_DIR", root)
