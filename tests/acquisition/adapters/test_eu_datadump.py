"""EU bulk-archive adapter, unit tests over a synthetic zip and index."""

from __future__ import annotations

import json
import zipfile
import zlib
from collections import Counter
from pathlib import Path

import pytest

import codify.acquisition.adapters  # noqa: F401  registers the adapter kinds
from codify.acquisition import DocumentRef, get_acquirer
from codify.acquisition.adapters.eu.datadump import (
    CelexNotInDump,
    DatadumpIndexMissing,
    EuDatadumpAcquirer,
    ManifestMissing,
    _assemble,
)
from codify.jurisdictions import SourceAdapter

_MEMBER = "uuid-1/fmx4/L_2014094XX.01006501.xml"
_SYNTHETIC_FORMEX = b"<ACT><TITLE>synthetic</TITLE></ACT>"


def _adapter() -> SourceAdapter:
    return SourceAdapter(kind="bulk_xml_archive", name="eu-datadump")


def _dump(tmp_path: Path, *, language: str | None) -> Path:
    archive = tmp_path / "ACTS_FMX.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(_MEMBER, _SYNTHETIC_FORMEX)
    payload: dict[str, object] = {"archive": str(archive), "entries": {"32014L0024": _MEMBER}}
    if language is not None:
        payload["language"] = language
    index = tmp_path / "index.json"
    index.write_text(json.dumps(payload))
    return index


def _ref() -> DocumentRef:
    return DocumentRef(
        jurisdiction_code="eu",
        doctype="directive",
        year=2014,
        number="24",
        extra={"celex": "32014L0024"},
    )


@pytest.mark.asyncio
async def test_archive_is_resolved_from_the_index_that_describes_it(tmp_path: Path) -> None:
    acquirer = EuDatadumpAcquirer(_adapter(), index_path=_dump(tmp_path, language=None))
    doc = await acquirer.fetch(_ref())
    acquirer.close()
    assert doc.bodies[0].content == _SYNTHETIC_FORMEX
    # An index predating the language field describes the English archive.
    assert doc.bodies[0].language == "eng"


@pytest.mark.asyncio
async def test_expression_language_comes_from_the_index(tmp_path: Path) -> None:
    """One archive per language, so a body is only English if the index says so."""
    acquirer = EuDatadumpAcquirer(_adapter(), index_path=_dump(tmp_path, language="spa"))
    doc = await acquirer.fetch(_ref())
    acquirer.close()
    assert doc.bodies[0].language == "spa"


@pytest.mark.asyncio
async def test_an_act_absent_from_the_archive_is_not_a_silent_miss(tmp_path: Path) -> None:
    acquirer = EuDatadumpAcquirer(_adapter(), index_path=_dump(tmp_path, language=None))
    ref = _ref()
    ref.extra["celex"] = "32014L9999"
    with pytest.raises(CelexNotInDump):
        await acquirer.fetch(ref)
    acquirer.close()


def test_a_missing_index_names_the_path_it_wanted(tmp_path: Path) -> None:
    acquirer = EuDatadumpAcquirer(_adapter(), index_path=tmp_path / "absent.json")
    with pytest.raises(DatadumpIndexMissing, match="absent.json"):
        acquirer._load_index()


def test_the_bulk_archive_kind_resolves_to_this_acquirer() -> None:
    assert isinstance(get_acquirer("eu", kind="bulk_xml_archive"), EuDatadumpAcquirer)


_MANIFEST = (
    '<?xml version="1.0"?><DOC><FMX>'
    '<DOC.MAIN.PUB NO.SEQ="0001"><REF.PHYS FILE="act.xml"/></DOC.MAIN.PUB>'
    '<DOC.SUB.PUB NO.SEQ="0001.0001" TYPE="ANNEX">'
    '<REF.PHYS FILE="annex1.xml"/></DOC.SUB.PUB>'
    '<DOC.SUB.PUB NO.SEQ="0001.0002" TYPE="ANNEX">'
    '<REF.PHYS FILE="annex2.xml"/></DOC.SUB.PUB>'
    "</FMX></DOC>"
)
_ACT = (
    '<?xml version="1.0"?><ACT><BIB.INSTANCE>'
    '<DOCUMENT.REF FILE="act.doc.xml"/></BIB.INSTANCE>'
    "<TITLE><TI><P>An act</P></TI></TITLE></ACT>"
)


def _publication(tmp_path: Path, *, manifest: bool = True) -> Path:
    archive = tmp_path / "PUB.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("uuid-1/fmx4/act.xml", _ACT)
        if manifest:
            zf.writestr("uuid-1/fmx4/act.doc.xml", _MANIFEST)
        zf.writestr("uuid-1/fmx4/annex1.xml", "<ANNEX><TITLE>I</TITLE></ANNEX>")
        zf.writestr("uuid-1/fmx4/annex2.xml", "<ANNEX><TITLE>II</TITLE></ANNEX>")
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"archive": str(archive), "entries": {"32014L0024": "uuid-1/fmx4/act.xml"}})
    )
    return index


@pytest.mark.asyncio
async def test_the_manifest_annexes_are_assembled_into_the_act(tmp_path: Path) -> None:
    """FORMEX splits a publication across files; the act alone is not the act."""
    acquirer = EuDatadumpAcquirer(_adapter(), index_path=_publication(tmp_path))
    doc = await acquirer.fetch(_ref())
    acquirer.close()
    body = doc.bodies[0].content.decode()
    assert body.count("<ANNEX") == 2
    assert body.index("<TITLE>I</TITLE>") < body.index("<TITLE>II</TITLE>")
    assert doc.upstream_metadata["annex_count"] == "2"


@pytest.mark.asyncio
async def test_a_named_manifest_the_archive_lacks_is_refused(tmp_path: Path) -> None:
    """Answering "no annexes" is the loss this adapter exists to prevent."""
    acquirer = EuDatadumpAcquirer(_adapter(), index_path=_publication(tmp_path, manifest=False))
    with pytest.raises(ManifestMissing):
        await acquirer.fetch(_ref())
    acquirer.close()


# The index has to be rebuildable: an archive nothing can index cannot be read.

_ACT_BODY = (
    b'<?xml version="1.0"?><ACT><BIB.INSTANCE><NO.DOC TYPE="OJ">'
    b"<YEAR>2024</YEAR><NO.CURRENT>1866</NO.CURRENT></NO.DOC></BIB.INSTANCE>"
    b"<TITLE><TI>Commission Implementing <HT>Regulation</HT> (EU) 2024/1866</TI></TITLE></ACT>"
)


def test_a_celex_is_rebuilt_from_the_act_body() -> None:
    from codify.acquisition.adapters.eu.datadump import celex_from_formex

    assert celex_from_formex(_ACT_BODY) == "32024R1866"


def test_a_doctype_keyword_inside_markup_is_still_found() -> None:
    """Titles wrap the keyword in inline markup, so a plain-text match misses it."""
    from codify.acquisition.adapters.eu.datadump import celex_from_formex

    body = _ACT_BODY.replace(b"<HT>Regulation</HT>", b"<HT>Decision</HT>")
    assert celex_from_formex(body) == "32024D1866"


def test_a_toc_stub_is_not_indexed() -> None:
    from codify.acquisition.adapters.eu.datadump import celex_from_formex

    assert celex_from_formex(b'<?xml version="1.0"?><DOC><TI>Contents</TI></DOC>') is None


def test_the_index_names_the_archive_language_from_its_filename(tmp_path: Path) -> None:
    from codify.acquisition.adapters.eu.datadump import build_index

    archive = tmp_path / "LEG_ES_FMX_20260830_01_00.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("uuid-1/fmx4/body.xml", _ACT_BODY)
        zf.writestr("uuid-1/fmx4/body.toc.xml", b"<DOC/>")
    payload = build_index(archive)
    assert payload["language"] == "spa"
    assert payload["entries"] == {"32024R1866": "uuid-1/fmx4/body.xml"}
    # The .toc member is skipped before it is read, not indexed and discarded.
    stats = payload["stats"]
    assert stats["files_scanned"] == 1  # type: ignore[index]
    assert stats["celex_indexed"] == 1  # type: ignore[index]
    assert stats["unindexed_acts"] == 0  # type: ignore[index]


def test_a_repeated_celex_keeps_the_longer_path(tmp_path: Path) -> None:
    """A corrigendum stub and the publication share a CELEX; the publication
    sits deeper."""
    from codify.acquisition.adapters.eu.datadump import build_index

    archive = tmp_path / "LEG_EN_FMX.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a/body.xml", _ACT_BODY)
        zf.writestr("a/deeper/body.xml", _ACT_BODY)
    payload = build_index(archive)
    assert payload["entries"] == {"32024R1866": "a/deeper/body.xml"}
    assert payload["stats"]["duplicates"] == 1  # type: ignore[index]


async def test_an_index_this_build_wrote_is_one_the_acquirer_reads(tmp_path: Path) -> None:
    """The builder and the acquirer agree on the payload shape."""
    from codify.acquisition.adapters.eu.datadump import build_index

    archive = tmp_path / "LEG_EN_FMX.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("uuid-1/fmx4/body.xml", _ACT_BODY)
    index = tmp_path / "index.json"
    index.write_text(json.dumps(build_index(archive)))
    acquirer = EuDatadumpAcquirer(_adapter(), index_path=index)
    acquired = await acquirer.fetch(
        DocumentRef(
            jurisdiction_code="eu",
            doctype="regulation",
            year=2024,
            number="1866",
            extra={"celex": "32024R1866"},
        )
    )
    assert acquired.frbr_work_uri == "/akn/eu/act/reg/2024/1866"
    assert acquired.primary().language == "eng"


def test_an_in_tree_archive_is_named_relative_to_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed index names a path, so an absolute one would only resolve on
    the machine that built it."""
    from codify.acquisition.adapters.eu.datadump import build_index

    nested = tmp_path / "data" / "datadumps" / "eu"
    nested.mkdir(parents=True)
    archive = nested / "LEG_EN_FMX.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("uuid-1/fmx4/body.xml", _ACT_BODY)
    monkeypatch.chdir(tmp_path)
    assert build_index(archive)["archive"] == "data/datadumps/eu/LEG_EN_FMX.zip"


def test_an_archive_outside_the_tree_keeps_its_own_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from codify.acquisition.adapters.eu.datadump import build_index

    archive = tmp_path / "LEG_EN_FMX.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("uuid-1/fmx4/body.xml", _ACT_BODY)
    tree = tmp_path / "tree"
    tree.mkdir()
    monkeypatch.chdir(tree)
    assert build_index(archive)["archive"] == str(archive)


def test_an_act_the_index_cannot_name_is_counted_apart_from_a_non_act(tmp_path: Path) -> None:
    """Both leave the index. Counted together, a run of unnameable acts reads as
    the housekeeping files the scan is meant to skip."""
    from codify.acquisition.adapters.eu.datadump import build_index

    archive = tmp_path / "LEG_EN_FMX.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a/good.xml", _ACT_BODY)
        zf.writestr("a/toc.xml", b'<?xml version="1.0"?><DOC><TI>Contents</TI></DOC>')
        # An act, but its title names no doctype, so no CELEX can be built.
        zf.writestr("a/untitled.xml", _ACT_BODY.replace(b"<HT>Regulation</HT>", b"<HT>Thing</HT>"))
    stats = build_index(archive)["stats"]
    assert stats["celex_indexed"] == 1  # type: ignore[index]
    assert stats["skipped_non_act"] == 1  # type: ignore[index]
    assert stats["unindexed_acts"] == 1  # type: ignore[index]
    assert stats["unindexed_by_reason"] == {"no_doctype_in_title": 1}  # type: ignore[index]


# An annex whose whole content is a reference to a further document. Inlining has
# to happen here: the converter reads a stored source with no siblings beside it,
# and the archive is only open during acquisition.
_NESTED = b"<DOC><CONTENTS><P>The rates apply from 1 January.</P></CONTENTS></DOC>"


def _publication_with_include(
    tmp_path: Path,
    *,
    annex: bytes,
    extra: dict[str, bytes] | None = None,
    elsewhere: dict[str, bytes] | None = None,
) -> Path:
    archive = tmp_path / "PUB.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("uuid-1/fmx4/act.xml", _ACT)
        zf.writestr("uuid-1/fmx4/act.doc.xml", _MANIFEST)
        zf.writestr("uuid-1/fmx4/annex1.xml", annex)
        zf.writestr("uuid-1/fmx4/annex2.xml", "<ANNEX><TITLE>II</TITLE></ANNEX>")
        for name, body in (extra or {}).items():
            zf.writestr(f"uuid-1/fmx4/{name}", body)
        for name, body in (elsewhere or {}).items():
            zf.writestr(f"uuid-2/fmx4/{name}", body)
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"archive": str(archive), "entries": {"32014L0024": "uuid-1/fmx4/act.xml"}})
    )
    return index


async def _fetched(index: Path) -> str:
    acquirer = EuDatadumpAcquirer(_adapter(), index_path=index)
    doc = await acquirer.fetch(_ref())
    acquirer.close()
    return doc.bodies[0].content.decode()


@pytest.mark.asyncio
async def test_a_nested_document_is_inlined_from_the_acts_own_directory(
    tmp_path: Path,
) -> None:
    """The whole-annex shape: unresolved, this annex reduces to its heading."""
    body = await _fetched(
        _publication_with_include(
            tmp_path,
            annex=b"<ANNEX><TITLE>I</TITLE>"
            b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='nested.xml'/></ANNEX>",
            extra={"nested.xml": _NESTED},
        )
    )
    assert "The rates apply from 1 January." in body
    assert "INCL.ELEMENT" not in body


@pytest.mark.asyncio
async def test_an_annex_under_another_uuid_resolves_beside_itself(tmp_path: Path) -> None:
    """A publication spans UUID directories: `_annex_members` resolves an annex
    against the manifest and falls back across the archive, so an annex can sit
    under a different UUID from its act. A reference inside that annex names a
    file beside the annex, not beside the act, and resolving from the act's
    directory would find nothing."""
    archive = tmp_path / "PUB.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("uuid-1/fmx4/act.xml", _ACT)
        zf.writestr("uuid-1/fmx4/act.doc.xml", _MANIFEST)
        zf.writestr("uuid-1/fmx4/annex1.xml", "<ANNEX><TITLE>I</TITLE></ANNEX>")
        # The second annex and the file it includes both live elsewhere.
        zf.writestr("uuid-2/fmx4/annex2.xml", _ANNEX_WITH_REF)
        zf.writestr("uuid-2/fmx4/n.xml", _NESTED)
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"archive": str(archive), "entries": {"32014L0024": "uuid-1/fmx4/act.xml"}})
    )
    body = await _fetched(index)
    assert "The rates apply from 1 January." in body
    assert "INCL.ELEMENT" not in body


@pytest.mark.asyncio
async def test_a_reference_resolves_beside_its_container_not_the_act(tmp_path: Path) -> None:
    """The same basename exists in both directories. Resolving from the file that
    held the reference picks the live one; resolving from the act picks the stale
    one, and both find something, so only differing content tells them apart."""
    archive = tmp_path / "PUB.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("uuid-1/fmx4/act.xml", _ACT)
        zf.writestr("uuid-1/fmx4/act.doc.xml", _MANIFEST)
        zf.writestr("uuid-1/fmx4/annex1.xml", "<ANNEX><TITLE>I</TITLE></ANNEX>")
        zf.writestr("uuid-1/fmx4/n.xml", b"<DOC><CONTENTS><P>Stale copy.</P></CONTENTS></DOC>")
        zf.writestr("uuid-2/fmx4/annex2.xml", _ANNEX_WITH_REF)
        zf.writestr("uuid-2/fmx4/n.xml", b"<DOC><CONTENTS><P>Live copy.</P></CONTENTS></DOC>")
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"archive": str(archive), "entries": {"32014L0024": "uuid-1/fmx4/act.xml"}})
    )
    body = await _fetched(index)
    assert "Live copy." in body
    assert "Stale copy." not in body


@pytest.mark.asyncio
async def test_a_nested_reference_resolves_beside_its_own_container(tmp_path: Path) -> None:
    """One level deeper than the annex case. The annex sits under uuid-2 and its
    include resolves to a file under uuid-1; that file's own include then names a
    basename present in both. Only carrying the resolved path down the recursion
    picks the copy beside it."""
    archive = tmp_path / "PUB.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("uuid-1/fmx4/act.xml", _ACT)
        zf.writestr("uuid-1/fmx4/act.doc.xml", _MANIFEST)
        zf.writestr("uuid-1/fmx4/annex1.xml", "<ANNEX><TITLE>I</TITLE></ANNEX>")
        # n.xml is only under uuid-1, so the annex reaches across to find it.
        zf.writestr(
            "uuid-1/fmx4/n.xml",
            b"<DOC><CONTENTS><P>Nested.</P>"
            b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='m.xml'/></CONTENTS></DOC>",
        )
        zf.writestr(
            "uuid-1/fmx4/m.xml", b"<DOC><CONTENTS><P>Beside the nested one.</P></CONTENTS></DOC>"
        )
        zf.writestr("uuid-2/fmx4/annex2.xml", _ANNEX_WITH_REF)
        zf.writestr(
            "uuid-2/fmx4/m.xml", b"<DOC><CONTENTS><P>Beside the annex.</P></CONTENTS></DOC>"
        )
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"archive": str(archive), "entries": {"32014L0024": "uuid-1/fmx4/act.xml"}})
    )
    body = await _fetched(index)
    assert "Nested." in body
    assert "Beside the nested one." in body
    assert "Beside the annex." not in body


@pytest.mark.asyncio
async def test_a_reference_in_the_acts_own_body_resolves_beside_the_act(
    tmp_path: Path,
) -> None:
    """Every other fixture puts the reference in an annex, so resolving the act's
    own side against an annex path would go unnoticed. The basename sits in both
    directories and only the act's copy is correct here."""
    act = _ACT.replace("<TITLE>", "<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='n.xml'/><TITLE>", 1)
    archive = tmp_path / "PUB.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("uuid-1/fmx4/act.xml", act)
        zf.writestr("uuid-1/fmx4/act.doc.xml", _MANIFEST)
        zf.writestr("uuid-2/fmx4/annex1.xml", "<ANNEX><TITLE>I</TITLE></ANNEX>")
        zf.writestr("uuid-1/fmx4/n.xml", b"<DOC><CONTENTS><P>Beside the act.</P></CONTENTS></DOC>")
        zf.writestr("uuid-2/fmx4/annex2.xml", "<ANNEX><TITLE>II</TITLE></ANNEX>")
        zf.writestr(
            "uuid-2/fmx4/n.xml", b"<DOC><CONTENTS><P>Beside the annex.</P></CONTENTS></DOC>"
        )
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"archive": str(archive), "entries": {"32014L0024": "uuid-1/fmx4/act.xml"}})
    )
    body = await _fetched(index)
    assert "Beside the act." in body
    assert "Beside the annex." not in body


def test_a_count_arising_in_an_annex_reaches_the_caller() -> None:
    """Every other count assertion passes no annexes, so an annex accumulator that
    went nowhere would still leave them green and a lost annex read as a clean act."""
    counts: Counter[str] = Counter()
    _assemble(
        b"<ACT><TITLE>t</TITLE></ACT>",
        [(_ANNEX_WITH_REF, "u/annex.xml")],
        lambda _ref, _near: None,
        near="u/act.xml",
        include_counts=counts,
    )
    assert counts["unresolved"] == 1


@pytest.mark.asyncio
async def test_one_basename_under_two_uuids_is_not_taken_for_a_cycle(tmp_path: Path) -> None:
    """`seen` has to hold the member resolved to, not the FILEREF. The chain crosses
    into the act's directory and then names `n.xml` again, which is a different file
    from the `n.xml` it started at; keyed on the basename it reads as a cycle and
    the second document is silently dropped."""
    archive = tmp_path / "PUB.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("uuid-1/fmx4/act.xml", _ACT)
        zf.writestr("uuid-1/fmx4/act.doc.xml", _MANIFEST)
        zf.writestr("uuid-1/fmx4/annex1.xml", "<ANNEX><TITLE>I</TITLE></ANNEX>")
        zf.writestr("uuid-2/fmx4/annex2.xml", _ANNEX_WITH_REF)
        zf.writestr(
            "uuid-2/fmx4/n.xml",
            b"<DOC><CONTENTS><P>First n.</P>"
            b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='m.xml'/></CONTENTS></DOC>",
        )
        # m.xml exists only beside the act, so the chain crosses UUIDs here.
        zf.writestr(
            "uuid-1/fmx4/m.xml",
            b"<DOC><CONTENTS><P>Crossed over.</P>"
            b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='n.xml'/></CONTENTS></DOC>",
        )
        zf.writestr("uuid-1/fmx4/n.xml", b"<DOC><CONTENTS><P>Second n.</P></CONTENTS></DOC>")
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({"archive": str(archive), "entries": {"32014L0024": "uuid-1/fmx4/act.xml"}})
    )
    body = await _fetched(index)
    assert "First n." in body
    assert "Crossed over." in body
    assert "Second n." in body


@pytest.mark.asyncio
async def test_a_damaged_member_costs_its_reference_and_not_the_act(tmp_path: Path) -> None:
    """A truncated member raises out of zlib, which is not an OSError. Losing the
    whole act to one unreadable optional file is worse than the gap it fills."""

    def _explode(_ref: str, _near: str) -> tuple[bytes, str] | None:
        raise zlib.error("invalid distance too far back")

    counts: Counter[str] = Counter()
    out = _assemble(_ANNEX_WITH_REF, [], _explode, include_counts=counts).decode()
    assert "INCL.ELEMENT" in out
    assert counts["unresolved"] == 1


def test_a_nested_root_holding_only_a_comment_leaves_the_reference() -> None:
    """`list(nested)` counts comments and processing instructions as children, so
    a comment-only root reads as content and the reference would be dropped for
    nothing."""
    counts: Counter[str] = Counter()
    out = _assemble(
        _ANNEX_WITH_REF,
        [],
        lambda _ref, _near: (b"<DOC><!-- nothing here --><?p i?></DOC>", "u/n.xml"),
        include_counts=counts,
    ).decode()
    assert "INCL.ELEMENT" in out
    assert counts["empty"] == 1


@pytest.mark.asyncio
async def test_a_same_named_file_in_another_publication_is_not_inlined(
    tmp_path: Path,
) -> None:
    """Basenames repeat across Cellar directories. An include naming a sibling this
    publication does not have must leave its reference, not reach for a stranger's
    file of the same name: inlining foreign law is worse than the gap it fills."""
    body = await _fetched(
        _publication_with_include(
            tmp_path,
            annex=b"<ANNEX><TITLE>I</TITLE>"
            b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='nested.xml'/></ANNEX>",
            elsewhere={"nested.xml": _NESTED},
        )
    )
    assert "The rates apply from 1 January." not in body
    assert "INCL.ELEMENT" in body


@pytest.mark.asyncio
async def test_prose_following_a_reference_survives_the_inlining(tmp_path: Path) -> None:
    """lxml hangs text after an element on that element, so removing the reference
    takes the words behind it with it unless they are carried over."""
    body = await _fetched(
        _publication_with_include(
            tmp_path,
            annex=b"<ANNEX><TITLE>I</TITLE><P>Before it. "
            b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='nested.xml'/>"
            b" and after it.</P></ANNEX>",
            extra={"nested.xml": _NESTED},
        )
    )
    assert "The rates apply from 1 January." in body
    assert "and after it." in body


@pytest.mark.asyncio
async def test_the_reference_stands_when_the_archive_lacks_the_file(tmp_path: Path) -> None:
    """The state before this change, not a failure of it: the converter still
    writes its marker and the run still counts it."""
    body = await _fetched(
        _publication_with_include(
            tmp_path,
            annex=b"<ANNEX><TITLE>I</TITLE>"
            b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='absent.xml'/></ANNEX>",
        )
    )
    assert "absent.xml" in body
    assert "INCL.ELEMENT" in body


@pytest.mark.asyncio
async def test_a_page_scan_is_left_named(tmp_path: Path) -> None:
    """A TIFF has no text to inline, and its bytes are not FORMEX."""
    body = await _fetched(
        _publication_with_include(
            tmp_path,
            annex=b"<ANNEX><TITLE>I</TITLE><INCL.ELEMENT TYPE='TIFF' FILEREF='page.tif'/></ANNEX>",
            # Parseable on purpose: unparseable bytes would keep the marker for
            # the wrong reason and the TYPE check would go untested.
            extra={"page.tif": _NESTED},
        )
    )
    assert "page.tif" in body
    assert "INCL.ELEMENT" in body
    assert "The rates apply from 1 January." not in body


@pytest.mark.asyncio
async def test_content_beside_the_reference_survives(tmp_path: Path) -> None:
    """The third shape: an annex holding prose as well as a reference keeps both,
    and the document lands where the reference stood rather than at the end."""
    body = await _fetched(
        _publication_with_include(
            tmp_path,
            annex=b"<ANNEX><TITLE>I</TITLE><P>Introductory words.</P>"
            b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='nested.xml'/>"
            b"<P>Closing words.</P></ANNEX>",
            extra={"nested.xml": _NESTED},
        )
    )
    assert body.index("Introductory words.") < body.index("The rates apply from 1 January.")
    assert body.index("The rates apply from 1 January.") < body.index("Closing words.")


@pytest.mark.asyncio
async def test_a_reference_cycle_terminates(tmp_path: Path) -> None:
    """A document referencing itself, directly or through another, would recurse
    forever; the depth bound and the seen set are what stop it."""
    body = await _fetched(
        _publication_with_include(
            tmp_path,
            annex=b"<ANNEX><TITLE>I</TITLE>"
            b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='a.xml'/></ANNEX>",
            extra={
                "a.xml": b"<DOC><CONTENTS><P>First.</P>"
                b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='b.xml'/></CONTENTS></DOC>",
                "b.xml": b"<DOC><CONTENTS><P>Second.</P>"
                b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='a.xml'/></CONTENTS></DOC>",
            },
        )
    )
    # Once each: without the seen set the pair would be inlined repeatedly until
    # the depth bound, duplicating the content rather than looping forever.
    assert body.count("First.") == 1
    assert body.count("Second.") == 1


@pytest.mark.asyncio
async def test_a_chain_deeper_than_the_bound_stops(tmp_path: Path) -> None:
    """`seen` stops a cycle; the depth bound stops a long acyclic chain, which a
    cycle test never reaches."""
    chain = {
        f"d{i}.xml": (
            f"<DOC><CONTENTS><P>Level {i}.</P>"
            f"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='d{i + 1}.xml'/></CONTENTS></DOC>"
        ).encode()
        for i in range(9)
    }
    chain["d9.xml"] = b"<DOC><CONTENTS><P>Level 9.</P></CONTENTS></DOC>"
    body = await _fetched(
        _publication_with_include(
            tmp_path,
            annex=b"<ANNEX><TITLE>I</TITLE>"
            b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='d0.xml'/></ANNEX>",
            extra=chain,
        )
    )
    assert "Level 0." in body
    assert "Level 9." not in body  # the bound stopped before the end
    assert "INCL.ELEMENT" in body  # and left the reference it stopped at


def test_a_cycle_and_a_depth_cut_are_both_counted() -> None:
    """A cut that reports nothing reads as a clean act. Both bounds abandon
    references, so both have to say how many."""
    counts: Counter[str] = Counter()
    _assemble(
        _ANNEX_WITH_REF,
        [],
        lambda _ref, _near: (
            b"<DOC><CONTENTS><INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='n.xml'/></CONTENTS></DOC>",
            "u/n.xml",
        ),
        include_counts=counts,
    )
    assert counts["cycle"] == 1

    deep: Counter[str] = Counter()
    _assemble(
        _ANNEX_WITH_REF,
        [],
        lambda ref, _near: (
            (
                "<DOC><CONTENTS><INCL.ELEMENT TYPE='FORMEX.DOC' "
                f"FILEREF='{ref}x'/></CONTENTS></DOC>"
            ).encode(),
            f"u/{ref}",
        ),
        include_counts=deep,
    )
    assert deep["too_deep"] == 1


@pytest.mark.asyncio
async def test_an_unparseable_target_leaves_the_reference(tmp_path: Path) -> None:
    body = await _fetched(
        _publication_with_include(
            tmp_path,
            annex=b"<ANNEX><TITLE>I</TITLE>"
            b"<INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='broken.xml'/></ANNEX>",
            extra={"broken.xml": b"<DOC><unclosed>"},
        )
    )
    assert "broken.xml" in body
    assert "INCL.ELEMENT" in body


def test_an_act_with_nothing_to_add_is_returned_byte_identical() -> None:
    """The contract a resolver must not break: parsing and re-serialising an
    untouched act changes its bytes for no reason, and the index test that
    compares them is what caught it."""
    from codify.acquisition.adapters.eu.datadump import _assemble

    act = b"<ACT><TITLE>unchanged</TITLE></ACT>"
    assert _assemble(act, [], lambda _ref, _near: (b"<DOC/>", "u/x.xml")) is act


_ANNEX_WITH_REF = (
    b"<ANNEX><TITLE>I</TITLE><INCL.ELEMENT TYPE='FORMEX.DOC' FILEREF='n.xml'/></ANNEX>"
)


def _inlined(nested: bytes) -> str:
    return _assemble(_ANNEX_WITH_REF, [], lambda _ref, _near: (nested, "u/n.xml")).decode()


def test_text_sitting_on_the_nested_root_is_not_dropped() -> None:
    """Only the root's children take the reference's place, so text directly on
    the root would go with the root. It becomes a paragraph instead."""
    assert "Only text, no children." in _inlined(b"<DOC>Only text, no children.</DOC>")
    both = _inlined(b"<DOC>Introductory root text.<P>Child.</P></DOC>")
    assert "Introductory root text." in both
    assert both.index("Introductory root text.") < both.index("Child.")


def test_a_nested_document_with_nothing_in_it_keeps_the_reference() -> None:
    """Removing the reference and putting nothing in its place is the same loss
    this change exists to fix, reached more quietly: the converter would never
    see it, so neither its marker nor its count would fire."""
    out = _inlined(b"<DOC></DOC>")
    assert "INCL.ELEMENT" in out
    assert "n.xml" in out


def test_the_outcome_of_every_reference_is_counted() -> None:
    """A resolver failing on everything degrades to the state before this change,
    which is invisible without a count."""
    counts: Counter[str] = Counter()
    _assemble(_ANNEX_WITH_REF, [], lambda _ref, _near: None, include_counts=counts)
    assert counts["unresolved"] == 1
    counts.clear()
    _assemble(
        _ANNEX_WITH_REF,
        [],
        lambda _ref, _near: (b"<DOC><P>x</P></DOC>", "u/n.xml"),
        include_counts=counts,
    )
    assert counts["inlined"] == 1
