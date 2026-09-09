from __future__ import annotations

import pytest

from codify.jurisdictions import JurisdictionConfig
from codify.pipeline import stages


@pytest.mark.parametrize(
    ("number", "title", "kind", "expected"),
    [
        ("2031-007", "Example", "joint", "007"),
        ("2031-007", "Joint Circular No. 2031-007", None, "007"),
        ("007", "Example", "joint", "007"),
        ("2031–007", "Example", "joint", "007"),
        ("2031 – 007", "Example", "joint", "007"),
        ("", "Joint Circular No. 2031 – 007", None, "007"),
        ("", "Joint Circular No. 2031-007", "joint", "007"),
        ("2030-007", "Example", "joint", "2030-007"),
        ("2031-007", "Example", "act", "2031-007"),
        ("2031-A", "Example", "joint", stages.draft_number(b"Original source")),
        (
            "",
            "Joint Circular implementing Circular No. 2031 – 007",
            "joint",
            stages.draft_number(b"Original source"),
        ),
        ("2031 – 007", "Example", "act", stages.draft_number(b"Original source")),
    ],
)
def test_descriptor_normalises_only_declared_matching_year_prefix(
    monkeypatch: pytest.MonkeyPatch, number: str, title: str, kind: str | None, expected: str
) -> None:
    cfg = JurisdictionConfig.model_validate(
        {
            "code": "xx",
            "name": "Example",
            "tradition": ["common_law"],
            "languages": ["eng"],
            "structuring": {
                "classification_rules": [
                    {
                        "signal": "title_regex",
                        "pattern": "^Joint Circular",
                        "target_document_class": "joint",
                    }
                ]
            },
            "document_classes": {
                "act": {"label": "Act"},
                "joint": {"label": "Joint Circular", "number_has_year_prefix": True},
            },
        }
    )
    monkeypatch.setattr(stages, "load_config", lambda _: cfg)
    desc = stages.resolve_descriptors(
        {"title": title, "number": number, "year": "2031", "date": "2031-07-17"},
        jurisdiction_code="xx",
        source_bytes=b"Original source",
        fallback_stem="source",
        requested_doctype=kind,
    )
    assert desc.number == expected
    assert desc.year == "2031"
    assert desc.title == title
