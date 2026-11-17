"""OpenStreetMap wiki page that tracks every Portuguese trail and its OSM relation."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..model import DISTRICTS, Trail, clean, fold, island, parse_km, parse_ref, parse_route_type

URL = "https://wiki.openstreetmap.org/wiki/Portugal/Percursos_Pedestres"
SOURCE = "OpenStreetMap Wiki"

_DISTRICT_BY_FOLD = {fold(d): d for d in DISTRICTS}


def _district(name: str) -> str:
    return _DISTRICT_BY_FOLD.get(fold(name), "")


def parse(html: str) -> list[Trail]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(".mw-parser-output") or soup
    headings: dict[str, str] = {}
    trails: list[Trail] = []
    for el in root.find_all(["h2", "h3", "h4", "h5", "h6", "table"]):
        if el.name != "table":
            headings[el.name] = clean(el.get_text(" "))
            for level in [k for k in headings if k > el.name]:
                del headings[level]
            continue
        rows = el.find_all("tr")
        if not rows:
            continue
        header = [fold(c.get_text(" ")) for c in rows[0].find_all(["th", "td"])]
        if not header or "referencia" not in header[0] or not any("rela" in h for h in header):
            continue
        rel_col = next(i for i, h in enumerate(header) if "rela" in h)
        for tr in rows[1:]:
            cells = tr.find_all("td")
            if len(cells) < len(header):
                continue
            ref_text = clean(cells[0].get_text(" "))
            name_cell = cells[1]
            name = clean(name_cell.get_text(" "))
            parsed = parse_ref(ref_text) or parse_ref(name)
            if not parsed:
                continue  # trails without an official reference
            kind, number, code, rest = parsed
            if not ref_text and rest:
                name = rest
            link = next((a["href"] for a in name_cell.find_all("a", href=True) if a["href"].startswith("http")), "")
            relation = None
            m = re.search(r"\b(\d{4,})\b", cells[rel_col].get_text(" "))
            if m:
                relation = int(m.group(1))
            km = parse_km(cells[rel_col + 1].get_text(" ") + " km") if len(cells) > rel_col + 1 else None
            notes = clean(cells[rel_col + 2].get_text(" ")) if len(cells) > rel_col + 2 else ""

            region = headings.get("h4", "")
            sub = headings.get("h5", "")
            municipality = ""
            if fold(region) in ("acores", "madeira"):
                district = "Açores" if fold(region) == "acores" else "Madeira"
                municipality = island(sub)
            else:
                district = _district(sub)
                if not district and kind == "GR":
                    # "Braga - Vieira do Minho / Circular"
                    first = re.split(r"[;/,-]", notes)[0]
                    district = _district(first.strip())
            closed = "encerrad" in fold(notes)
            trails.append(Trail(
                kind=kind, number=number, code=code, name=name, district=district,
                municipality=municipality, distance_km=km, route_type=parse_route_type(notes),
                website=link, osm_relation=relation, notes=notes, closed=closed, sources=[SOURCE],
            ))
    return trails
