"""Lists of homologated PR and GR trails on Portuguese Wikipedia (by district and municipality)."""

from __future__ import annotations

import re
from collections import Counter

from bs4 import BeautifulSoup, Tag

from ..model import (
    DISTRICTS,
    Trail,
    clean,
    fold,
    island,
    parse_difficulty,
    parse_duration,
    parse_km,
    parse_ref,
    parse_route_type,
)

PR_URL = "https://pt.wikipedia.org/wiki/Lista_de_percursos_pedestres_de_pequena_rota_em_Portugal"
GR_URL = "https://pt.wikipedia.org/wiki/Lista_de_percursos_pedestres_de_grande_rota_em_Portugal"
SOURCE = "Wikipédia"

_DISTRICT_BY_FOLD = {fold(d): d for d in DISTRICTS}


def _content(html: str) -> Tag:
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one(".mw-parser-output") or soup


def _own_text(li: Tag) -> str:
    """Text of a list item without its nested lists."""
    parts = []
    for child in li.children:
        if isinstance(child, Tag):
            if child.name in ("ul", "ol"):
                continue
            if child.name == "sup":            # footnote markers
                continue
            parts.append(child.get_text(" "))
        else:
            parts.append(str(child))
    return clean(" ".join(parts))


def _external_link(li: Tag) -> str:
    for a in li.find_all("a", href=True):
        if a.find_parent("li") is not li or a.find_parent("sup"):
            continue
        href = a["href"]
        if href.startswith("http") and "wikipedia.org" not in href and "wikidata" not in href:
            return href
    return ""


def _split_name(rest: str) -> tuple[str, str]:
    """'"Name" (14 km - fácil)' or 'Name - 14 km - 4h' -> (name, details)."""
    m = re.match(r'^[“"«]([^”"»]+)[”"»]\s*(.*)$', rest)
    if m:
        return m.group(1).strip(), m.group(2)
    cut = len(rest)
    for sep in (" (", " - ", " – ", " — ", ", "):
        i = rest.find(sep)
        if 0 < i < cut:
            cut = i
    return rest[:cut].strip(' "'), rest[cut:]


def _region(h2: str) -> str:
    t = fold(h2)
    if "acores" in t:
        return "Açores"
    if "madeira" in t:
        return "Madeira"
    return ""


def parse_pr_list(html: str) -> list[Trail]:
    root = _content(html)
    headings: dict[str, str] = {}
    trails: list[Trail] = []
    for el in root.find_all(["h2", "h3", "h4", "h5", "li"]):
        if el.name.startswith("h"):
            headings[el.name] = clean(el.get_text(" "))
            for level in [k for k in headings if k > el.name]:
                del headings[level]
            continue
        if el.find_parent(class_=["navbox", "reflist", "references", "toc"]) or el.find_parent(id="toc"):
            continue
        text = _own_text(el)
        parsed = parse_ref(text)
        if not parsed:
            continue
        kind, number, code, rest = parsed
        if kind == "GR":
            continue  # GR come from the GR list
        region = _region(headings.get("h2", ""))
        if region:
            district = region
            municipality = island(headings.get("h4") or headings.get("h3") or "")
        else:
            district = _DISTRICT_BY_FOLD.get(fold(headings.get("h3", "")), "")
            municipality = headings.get("h4", "")
        name, details = _split_name(rest)
        promoter = ""
        m = re.search(r"Entidade Promotora\s*:\s*(.+)$", details, re.I)
        if m:
            promoter = m.group(1).strip()
            details = details[: m.start()]
        trails.append(Trail(
            kind=kind, number=number, code=code, name=name, district=district,
            municipality=municipality, distance_km=parse_km(details),
            duration_min=parse_duration(details), difficulty=parse_difficulty(details),
            route_type=parse_route_type(details), promoter=promoter,
            website=_external_link(el), sources=[SOURCE],
        ))
    _fill_missing_codes(trails)
    return trails


def _fill_missing_codes(trails: list[Trail]) -> None:
    """'PR8 Malara' under Bragança: borrow the code the other trails of that municipality use."""
    codes: dict[str, Counter] = {}
    for t in trails:
        if t.code and t.municipality:
            codes.setdefault(t.municipality, Counter())[t.code] += 1
    for t in trails:
        if not t.code and t.municipality in codes and t.district not in ("Açores", "Madeira"):
            t.code = codes[t.municipality].most_common(1)[0][0]


def parse_gr_list(html: str) -> list[Trail]:
    root = _content(html)
    trails: list[Trail] = []
    for table in root.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header = [fold(c.get_text(" ")) for c in rows[0].find_all(["th", "td"])]
        if not any("designa" in h for h in header):
            continue
        names = ("designa", "tipo", "extens", "regi", "munic")
        col = {name: i for i, h in enumerate(header) for name in names if name in h}
        for tr in rows[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < len(header):
                continue

            def cell(name: str, cells=cells, col=col) -> str:
                return clean(cells[col[name]].get_text(" ")) if name in col else ""

            parsed = parse_ref(cell("designa"))
            if not parsed or parsed[0] != "GR":
                continue
            kind, number, code, rest = parsed
            link = ""
            for a in cells[col["designa"]].find_all("a", href=True):
                if a["href"].startswith("http") and "wikipedia.org" not in a["href"]:
                    link = a["href"]
                    break
            name = rest
            m = re.match(r"^(E\d+)\s*(.*)$", rest)       # GR11-E9 Caminho do Atlântico
            if m:
                name = f"{m.group(2)} ({m.group(1)})".strip()
            municipalities = cell("munic")
            trails.append(Trail(
                kind="GR", number=number, code="", name=name.strip(' "') or f"GR{number}",
                municipality=municipalities, distance_km=parse_km(cell("extens")),
                route_type=parse_route_type(cell("tipo")), notes=cell("regi"),
                website=link, sources=[SOURCE],
            ))
    return trails
