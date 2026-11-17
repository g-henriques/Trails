"""OpenStreetMap: route geometry from the OSM API and trail discovery through Overpass."""

from __future__ import annotations

import logging

from .. import config
from ..geo import Segment, chain
from ..http import Client
from ..model import Trail, parse_ref, parse_route_type

log = logging.getLogger(__name__)
SOURCE = "OpenStreetMap"

# Members that are not part of the walkable line.
_SKIP_ROLES = {"platform", "stop", "guidepost", "information", "start", "finish"}


class RouteNotFound(Exception):
    pass


def fetch_route(client: Client, relation_id: int, depth: int = 0) -> tuple[dict, list[Segment]]:
    """Tags and track of a route relation. Super-routes (relations of relations) are followed one level."""
    try:
        r = client.get(f"{config.OSM_API}/relation/{relation_id}/full.json")
    except Exception as e:
        if getattr(getattr(e, "response", None), "status_code", None) in (404, 410):
            raise RouteNotFound(f"relation {relation_id} no longer exists in OpenStreetMap") from e
        raise
    elements = r.json()["elements"]
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in elements if e["type"] == "node"}
    ways = {e["id"]: e for e in elements if e["type"] == "way"}
    rel = next(e for e in elements if e["type"] == "relation" and e["id"] == relation_id)
    tags = rel.get("tags", {})
    lines: list[Segment] = []
    segments: list[Segment] = []
    for m in rel.get("members", []):
        if m.get("role") in _SKIP_ROLES:
            continue
        if m["type"] == "way" and m["ref"] in ways:
            line = [nodes[n] for n in ways[m["ref"]].get("nodes", []) if n in nodes]
            if len(line) >= 2:
                lines.append(line)
        elif m["type"] == "relation" and depth < 1:
            try:
                _, child = fetch_route(client, m["ref"], depth + 1)
            except Exception as e:  # one broken stage must not lose the whole GR
                log.warning("relation %s: child %s failed: %s", relation_id, m["ref"], e)
                continue
            segments.extend(child)
    return tags, chain(lines) + segments


DISCOVER_QUERY = """
[out:json][timeout:300];
area["ISO3166-1"="PT"][admin_level=2]->.pt;
rel(area.pt)["type"="route"]["route"~"^(hiking|foot)$"]["ref"];
out tags center;
"""


def discover(client: Client) -> list[tuple[Trail, tuple[float, float] | None]]:
    """Every PR/PRC/PL/GR hiking relation in Portugal, with its centre. Raises when no endpoint answers."""
    last: Exception | None = None
    for url in config.OVERPASS_URLS:
        try:
            r = client.post(url, data={"data": DISCOVER_QUERY}, timeout=360, retries=2)
            elements = r.json().get("elements", [])
            break
        except Exception as e:
            log.warning("Overpass %s failed: %s", url, e)
            last = e
    else:
        raise RuntimeError(f"no Overpass endpoint answered: {last}")
    found = []
    for e in elements:
        tags = e.get("tags", {})
        parsed = parse_ref(tags.get("ref", "").upper())
        if not parsed:
            continue
        kind, number, code, _ = parsed
        name = tags.get("name", "")
        # Names often repeat the reference: "PR3 ARC - Rota do Ouro" -> "Rota do Ouro"
        name_ref = parse_ref(name)
        if name_ref:
            name = name_ref[3] or name
        km = None
        try:
            km = float(tags.get("distance", "").replace(",", ".").replace("km", "").strip())
        except ValueError:
            pass
        trail = Trail(
            kind=kind, number=number, code=code, name=name.strip(" -–"), distance_km=km,
            route_type="Circular" if tags.get("roundtrip") == "yes" else parse_route_type(tags.get("description", "")),
            website=tags.get("website", "") or tags.get("url", ""), osm_relation=e["id"],
            promoter=tags.get("operator", ""), sources=[SOURCE],
        )
        center = e.get("center")
        found.append((trail, (center["lat"], center["lon"]) if center else None))
    return found
