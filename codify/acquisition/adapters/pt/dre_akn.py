"""The Diário da República's consolidated fragments, or its printed text, as Bluebell
parsed to AKN without a model. Each fragment's label names its unit (Capítulo II,
Artigo 5.º-A, Anexo), so the structure is read, not inferred."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, cast

from lxml import etree

from codify.acquisition.adapters.th.ocs_akn import escape
from codify.akn._schema import AKN_NS, parse_xml
from codify.akn.bluebell import bluebell_to_akn
from codify.pipeline.enrich.cobalt import set_work_uri

REPEALED = 4

GROUPS = {
    "Livro": "BOOK",
    "Parte": "PART",
    "Título": "TITLE",
    "Subtítulo": "SUBTITLE",
    "Capítulo": "CHAPTER",
    "Secção": "SECTION",
    "Subsecção": "SUBSECTION",
    "Divisão": "DIVISION",
    "Subdivisão": "SUBDIVISION",
}
UNITS = {"Artigo", "Base"}
PARA = re.compile(r"^(\d+(?:\.\d+)*)\s*-\s+(.*)$")
POINT = re.compile(r"^([a-z]{1,4})\)\s+(.*)$")
ROMAN = "i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx".split()
ART_NUM = re.compile(
    r"^(?:Artigo|Base)\s+(\d+|[IVXLC]+|único)\.?\s*[º°o]?\s*(?:-\s*([A-Z]{1,3}))?", re.IGNORECASE
)
TITLE_LINE = re.compile(
    r"^(Decreto-Lei|Lei|Portaria|Lei Orgânica)\b.*\bn\.º|^de \d{1,2} de \w+|^Sumário:"
)
FORMULA = re.compile(r"seguinte\s*:\s*$")
OPEN_QUOTE = {"«", '"', "“"}


class UnmappedFragment(ValueError):
    """The fragments form no act the mapping can place; refused rather than dropped."""


def lines(text: str | None) -> list[str]:
    return [x.strip() for x in re.split(r"[\r\n]+", text or "") if x.strip()]


def word(label: str) -> str:
    return (label.strip().split() or [""])[0]


def fragments(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The consolidation's fragments in source order, one flat record each."""
    out = []
    for item in (data.get("LegConsBase") or {}).get("List") or []:
        cf, fv = item["ConsolidacaoFragmento"], item["FragmentoVersao"]
        out.append(
            {
                "id": str(cf["Id"]),
                "parent": str(cf["PaiId"]),
                "level": item.get("Nivel"),
                "label": fv.get("Tituo") or cf.get("Name") or "",
                "heading": fv.get("Epigrafe") or cf.get("Epigrafe") or "",
                "text": fv.get("Texto") or "",
                "version_date": (fv.get("DataVersao") or "")[:10],
                "in_force_from": (fv.get("DataEntradaVigor") or "")[:10],
                "state": fv.get("VersaoEstadoId"),
            }
        )
    return out


def _head(keyword: str, num: str, heading: str, pad: str) -> str:
    h = " ".join(heading.split())
    # Epígrafes before 1990 are printed in parentheses.
    h = h[1:-1].strip() if h.startswith("(") and h.endswith(")") else h
    return f"{pad}{keyword} {num}".rstrip() + (f" - {escape(h)}" if h else "")


def quote_depth(depth: int, line: str) -> int:
    """Quotes open after `line`. Guillemets nest; straight quotes close only at line end."""
    depth += line.count("«") + line.count("“") - line.count("»") - line.count("”")
    if '"' in line and re.search(r'"[.;,]?\s*$', line) and not (depth == 0 and line[:1] == '"'):
        return 0
    if line[:1] == '"' and not re.search(r'"[.;,]?\s*$', line[1:]):
        return max(depth, 1)
    return max(0, depth)


def _next_marker(body: list[str], n: int) -> str:
    for line in body[n + 1 :]:
        p = POINT.match(line)
        if p or PARA.match(line):
            return p.group(1) if p else ""
    return ""


def article_body(text: str, pad: str) -> list[str]:
    """n.º as PARA, alínea as POINT, subalínea as INDENT; quoted amendments stay prose."""
    out: list[str] = []
    depth = 0
    in_para = False
    last_letter = ""
    last_roman = -1
    quote = 0
    body = lines(text)
    for n, line in enumerate(body):
        if quote or line[:1] in OPEN_QUOTE:
            out.append(f"{pad}{'  ' * (depth + 1)}{escape(line)}")
            quote = quote_depth(quote, line)
            continue
        m, p = PARA.match(line), POINT.match(line)
        if m:
            in_para, depth, last_letter, last_roman = True, 1, "", -1
            out += [f"{pad}  PARA {m.group(1)}", f"{pad}    {escape(m.group(2))}"]
            continue
        if p:
            tok = p.group(1)
            # An i) straight after h) is the next alínea unless ii) follows.
            roman = (
                tok in ROMAN
                and bool(last_letter)
                and (
                    (last_roman >= 0 and ROMAN.index(tok) == last_roman + 1)
                    or (tok == "i" and (last_letter != "h" or _next_marker(body, n) == "ii"))
                )
            )
            if roman:
                last_roman = ROMAN.index(tok)
                depth = (2 if in_para else 1) + 1
                keyword = "INDENT"
            else:
                last_letter, last_roman = tok, -1
                depth = 2 if in_para else 1
                keyword = "POINT"
            ind = "  " * depth
            out += [f"{pad}{ind}{keyword} {tok})", f"{pad}{ind}  {escape(p.group(2))}"]
            continue
        out.append(f"{pad}{'  ' * (depth + 1)}{escape(line)}")
    return out


Tree = dict[str, list[dict[str, Any]]]


def _emit(node: dict[str, Any], kids: Tree, pad: str, out: list[str], annexes: list[str]) -> None:
    w = word(node["label"])
    first = (lines(node["label"]) or [""])[0]
    if w in UNITS:
        m = ART_NUM.match(first)
        num = (
            m.group(1).replace("único", "unico") + (m.group(2) or "").upper()
            if m
            else re.sub(r"\W+", "", first.split(" ", 1)[-1])
        )
        out.append(_head("ARTICLE", num, node["heading"], pad))
        out += article_body(node["text"], pad)
    elif w in GROUPS:
        num = first.split(" ", 1)[1].strip() if " " in first else ""
        out.append(_head(GROUPS[w], num, node["heading"], pad))
        out += [f"{pad}  {escape(x)}" for x in lines(node["text"])]
    else:
        # Notas, Lista and other unnumbered blocks: a crossheading over their prose.
        heading = " ".join(node["heading"].split())
        out.append(
            f"{pad}CROSSHEADING {escape(first)}" + (f" - {escape(heading)}" if heading else "")
        )
        out += [f"{pad}{escape(x)}" for x in lines(node["text"])]
    for k in kids.get(node["id"], []):
        if word(k["label"]) == "Anexo":
            # An annex hung below a division is still the act's annex.
            _annex(k, kids, "", annexes)
        else:
            _emit(k, kids, pad + "  ", out, annexes)


def _annex(node: dict[str, Any], kids: Tree, pad: str, out: list[str]) -> None:
    label = lines(node["label"]) or ["Anexo"]
    out += ["", f"{pad}SCHEDULE {escape(label[0])}"]
    sub = " ".join(node["heading"].split())
    if sub:
        out.append(f"{pad}  SUBHEADING {escape(sub)}")
    out += [f"{pad}  {escape(x)}" for x in label[1:] + lines(node["text"])]
    nested = [k for k in kids.get(node["id"], []) if word(k["label"]) == "Anexo"]
    later: list[str] = []
    for k in kids.get(node["id"], []):
        if k not in nested:
            _emit(k, kids, pad + "  ", out, later)
    for k in nested:
        _annex(k, kids, pad + "  ", out)
    out += later


def to_bluebell(frags: list[dict[str, Any]], *, printed: str = "") -> str:
    """Fragments in source order as one Bluebell document; repealed ones are left out.
    `printed` supplies the preamble where the consolidation's root has none."""
    if not frags:
        raise UnmappedFragment("no fragments")
    every: Tree = defaultdict(list)
    for f in frags:
        every[f["parent"]].append(f)

    def kept(f: dict[str, Any]) -> bool:
        # A repealed division still holding text marked in force keeps its place.
        return f.get("state") != REPEALED or any(kept(k) for k in every.get(f["id"], []))

    root = frags[0]
    if not kept(root):
        raise UnmappedFragment("the consolidation marks the whole act repealed")
    live = [f for f in frags if kept(f)]
    ids = {f["id"] for f in live}
    if stray := [f["label"] for f in live[1:] if f["parent"] not in ids]:
        raise UnmappedFragment(f"fragments outside the act's tree: {stray[:3]}")
    kids: Tree = defaultdict(list)
    for f in live:
        kids[f["parent"]].append(f)
    text = lines(root["text"])
    if not text:
        orig = lines(printed)
        cut = next((i for i, x in enumerate(orig) if FORMULA.search(x)), None)
        text = orig[: cut + 1] if cut is not None else []
    preface = [" ".join(root["heading"].split())] if root["heading"].strip() else []
    while text and TITLE_LINE.search(text[0]):
        preface.append(text.pop(0))
    body: list[str] = []
    concl: list[str] = []
    schedules: list[str] = []
    for k in kids.get(root["id"], []):
        w = word(k["label"])
        if w == "Assinatura":
            concl += lines(k["text"])
        elif w == "Anexo":
            _annex(k, kids, "", schedules)
        else:
            _emit(k, kids, "  ", body, schedules)
    doc: list[str] = []
    if preface:
        doc += ["PREFACE"] + [f"  {escape(x)}" for x in preface]
    if text:
        doc += ["PREAMBLE"] + [f"  {escape(x)}" for x in text]
    if not body:
        raise UnmappedFragment("no operative text to form a body")
    doc += ["BODY"] + body
    if concl:
        doc += ["CONCLUSIONS"] + [f"  {escape(x)}" for x in concl]
    return "\n".join(doc + schedules) + "\n"


DIVISIONS = [
    "PARTE",
    "LIVRO",
    "TÍTULO",
    "SUBTÍTULO",
    "CAPÍTULO",
    "SECÇÃO",
    "SUBSECÇÃO",
    "DIVISÃO",
    "SUBDIVISÃO",
]
DIV_LINE = re.compile(rf"^({'|'.join(DIVISIONS)})\s+([IVXLC]+(?:-[A-Z])?|\d+|[A-Z]|ÚNIC[OA])$")
ART_LINE = re.compile(r"^(?:Artigo\s+\d+\.?\s*º(?:-[A-Z]{1,3})?|Artigo único)$")
# Older portarias number provisions 1.º, 2.º without the word Artigo.
BARE_ART_LINE = re.compile(r"^\d+\.º(?:-[A-Z])?$")
ANY_ART_LINE = re.compile(f"{ART_LINE.pattern}|{BARE_ART_LINE.pattern}")
ANNEX_LINE = re.compile(r"^ANEXO(?:\s+[IVXLC]+(?:-[A-Z])?|\s+\d+)?$")
SIGN_LINE = re.compile(
    r"^(Visto e aprovado em Conselho|Aprovad[ao] em \d|Promulgad[ao] em \d|Publique-se)"
)


def _is_epigrafe(line: str) -> bool:
    return (
        len(line) < 200
        and not (PARA.match(line) or POINT.match(line))
        and not line.endswith((".", ":", ";"))
    )


def from_printed(text: str) -> list[dict[str, Any]]:
    """Fragments read from the printed text, for an act the gazette has not consolidated."""
    src = lines(text)
    art = ANY_ART_LINE
    first = next((i for i, x in enumerate(src) if art.match(x)), None)
    if first is None:
        raise UnmappedFragment("the printed text has no articles")
    # With no enacting formula the body opens at its first division or article.
    opening = next(i for i, x in enumerate(src) if art.match(x) or DIV_LINE.match(x))
    cut = max((i for i, x in enumerate(src[:first]) if FORMULA.search(x)), default=opening - 1)
    frags: list[dict[str, Any]] = [
        {
            "id": "0",
            "parent": "",
            "label": "Diploma",
            "heading": "",
            "text": "\n".join(src[: cut + 1]),
        }
    ]
    stack: list[tuple[int, str]] = []  # (rank, id) of the open divisions
    top = "0"  # the root, or the open annex
    cur = frags[0]
    quote = 0

    def add(label: str, parent: str, heading: str = "") -> dict[str, Any]:
        frags.append(
            {
                "id": str(len(frags)),
                "parent": parent,
                "label": label,
                "heading": heading,
                "text": "",
            }
        )
        return frags[-1]

    i = cut + 1
    while i < len(src):
        line, nxt = src[i], (src[i + 1] if i + 1 < len(src) else "")
        i += 1
        if quote or (line[:1] in OPEN_QUOTE and word(cur["label"]) in UNITS):
            quote = quote_depth(quote, line)
        elif ANNEX_LINE.match(line):
            cur = add("Anexo" + line[5:], "0")
            top, stack = cur["id"], []
            continue
        elif SIGN_LINE.match(line) and top == "0" and word(cur["label"]) != "Assinatura":
            cur = add("Assinatura", "0")
        elif m := DIV_LINE.match(line):
            rank = DIVISIONS.index(m.group(1))
            while stack and stack[-1][0] >= rank:
                stack.pop()
            labelled = art.match(nxt) or DIV_LINE.match(nxt) or ANNEX_LINE.match(nxt)
            heading = nxt if _is_epigrafe(nxt) and not labelled else ""
            i += bool(heading)
            label = f"{m.group(1).capitalize()} {m.group(2)}"
            cur = add(label, stack[-1][1] if stack else top, heading)
            stack.append((rank, cur["id"]))
            continue
        elif art.match(line):
            heading = (
                nxt if _is_epigrafe(nxt) and not (art.match(nxt) or DIV_LINE.match(nxt)) else ""
            )
            i += bool(heading)
            label = line if line.startswith("Artigo") else f"Artigo {line}"
            cur = add(label, stack[-1][1] if stack else top, heading)
            continue
        cur["text"] += line + "\n"
    return frags


def _identify(akn: str, eli: str, title: str, publication: dict[str, str]) -> str:
    """The work ELI as an `eli` alias, the act's title, and its gazette publication."""
    root = parse_xml(akn)
    work = root.find(f"./*/{{{AKN_NS}}}meta/{{{AKN_NS}}}identification/{{{AKN_NS}}}FRBRWork")
    if work is None:
        raise UnmappedFragment("the parsed AKN has no FRBRWork")
    for alias in work.findall(f"{{{AKN_NS}}}FRBRalias"):
        if alias.get("name") == "title" and title:
            alias.set("value", title)
    if eli:
        anchor = work.findall(f"{{{AKN_NS}}}FRBRalias") or work.findall(f"{{{AKN_NS}}}FRBRuri")
        anchor[-1].addnext(etree.Element(f"{{{AKN_NS}}}FRBRalias", value=eli, name="eli"))
    if publication:
        identification = work.getparent()
        assert identification is not None
        identification.addnext(etree.Element(f"{{{AKN_NS}}}publication", **publication))
    return cast(str, etree.tostring(root, encoding="unicode"))


def to_akn(
    bluebell: str,
    *,
    number: str,
    work: str,
    published: str,
    expression: str,
    as_of: str,
    eli: str = "",
    title: str = "",
    publication: dict[str, str] | None = None,
) -> str:
    """AKN XML for the Bluebell, identified as `work` and `expression`. A Bluebell
    parse error is raised, never swallowed."""
    akn, _, errors = bluebell_to_akn(
        bluebell, country="pt", date=published, number=number, language="por"
    )
    if errors:
        e = errors[0]
        raise UnmappedFragment(f"Bluebell rejected line {e.line}:{e.column}: {e.message}")
    akn = set_work_uri(akn, work, expression, as_of) or akn
    return _identify(akn, eli, title, publication or {})


__all__ = [
    "REPEALED",
    "UnmappedFragment",
    "article_body",
    "fragments",
    "from_printed",
    "quote_depth",
    "to_akn",
    "to_bluebell",
]
