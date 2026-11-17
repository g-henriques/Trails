"""Scrape every source, merge them into one catalog and download the OSM tracks.

Run inside the app (scheduler) or by hand: python -m trails.scrape [--catalog-only] [--limit N]
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import math
import re
import sqlite3
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta

from . import config, db
from .geo import bbox, length_km, simplify
from .http import Client
from .model import DISTRICTS, Trail, fold
from .sources import osm, osmwiki, wikipedia

log = logging.getLogger(__name__)
_lock = threading.Lock()

_FILL_FIELDS = [
    "name", "district", "municipality", "distance_km", "duration_min", "difficulty", "route_type",
    "promoter", "website", "osm_relation", "notes",
]
_STOP = re.compile(r"\b(trilho|rota|percurso|pedestre|caminho|pr|gr|d[aeo]s?|e|a|o)\b")


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- merge


def similarity(a: str, b: str) -> float:
    a, b = _STOP.sub(" ", fold(a)), _STOP.sub(" ", fold(b))
    a, b = " ".join(a.split()), " ".join(b.split())
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _fill(target: Trail, other: Trail) -> None:
    for f in _FILL_FIELDS:
        if not getattr(target, f) and getattr(other, f):
            setattr(target, f, getattr(other, f))
    target.closed = target.closed or other.closed
    for s in other.sources:
        if s not in target.sources:
            target.sources.append(s)


def merge(lists: list[list[Trail]]) -> dict[str, Trail]:
    """Merge source lists, earlier lists winning field by field.

    The same reference can be two trails (a municipality that renumbered its network, or a
    list with the old and new name). Inside one source a different name means a different
    trail (stored as "<id>-2"); across sources the most similar name is the same trail.
    """
    out: dict[str, Trail] = {}
    by_key: dict[str, list[str]] = {}
    for trails in lists:
        seen_in_this_source: set[str] = set()
        for t in trails:
            base = t.key
            candidates = by_key.get(base, [])
            target = None
            if candidates:
                scored = sorted(((similarity(out[c].name, t.name), c) for c in candidates), reverse=True)
                best_score, best = scored[0]
                untouched = [c for c in candidates if c not in seen_in_this_source]
                if best_score >= 0.6 or not t.name:
                    target = best
                elif untouched:
                    target = max(untouched, key=lambda c: similarity(out[c].name, t.name))
            if target:
                _fill(out[target], t)
                seen_in_this_source.add(target)
                continue
            new_id = base if base not in out else f"{base}-{len(candidates) + 1}"
            t.id = new_id
            out[new_id] = t
            by_key.setdefault(base, []).append(new_id)
            seen_in_this_source.add(new_id)
    return out


# ---------------------------------------------------------------- districts


def _point_district(lat: float, lon: float) -> str:
    if lon < -24:
        return "Açores"
    if lon < -15 and lat < 34:
        return "Madeira"
    return ""


def resolve_districts(trails: dict[str, Trail], points: dict[str, tuple[float, float]]) -> None:
    """Fill missing districts from municipality and code (learnt from the lists), then from the map."""
    by_municipality: dict[str, Counter] = {}
    by_code: dict[str, Counter] = {}
    municipality_by_code: dict[str, Counter] = {}
    for t in trails.values():
        if t.code and t.municipality and t.kind != "GR":
            municipality_by_code.setdefault(t.code, Counter())[t.municipality] += 1
        if t.district in DISTRICTS:
            if t.municipality and t.kind != "GR":
                by_municipality.setdefault(fold(t.municipality), Counter())[t.district] += 1
            if t.code:
                by_code.setdefault(t.code, Counter())[t.district] += 1
    for t in trails.values():
        if not t.municipality and t.code in municipality_by_code and t.district not in ("Açores", "Madeira"):
            t.municipality = municipality_by_code[t.code].most_common(1)[0][0]
        if t.district in DISTRICTS:
            continue
        t.district = ""
        if t.code in by_code:
            t.district = by_code[t.code].most_common(1)[0][0]
            continue
        for m in re.split(r"\s+[-–]\s+|,|;|/|\(|\)", t.municipality):
            hit = by_municipality.get(fold(m))
            if hit:
                t.district = hit.most_common(1)[0][0]
                break
    # Nearest trail with a known district (start point or OSM centre), within 25 km.
    known = [(points[i], t.district) for i, t in trails.items() if t.district and i in points]
    for i, t in trails.items():
        if t.district or i not in points:
            continue
        lat, lon = points[i]
        t.district = _point_district(lat, lon)
        if t.district or not known:
            continue
        coslat = math.cos(math.radians(lat))
        (plat, plon), d = min(known, key=lambda k: (k[0][0] - lat) ** 2 + ((k[0][1] - lon) * coslat) ** 2)
        if math.hypot(plat - lat, (plon - lon) * coslat) * 111 <= 25:
            t.district = d


# ---------------------------------------------------------------- status


class Status:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.data = db.get_meta(conn, "scrape", {}) or {}

    def update(self, **kw) -> None:
        self.data.update(kw)
        db.set_meta(self.conn, "scrape", self.data)


def get_status(conn: sqlite3.Connection) -> dict:
    data = db.get_meta(conn, "scrape", {}) or {}
    data["running"] = _lock.locked()
    return data


# ---------------------------------------------------------------- steps


def collect(client: Client, status: Status) -> tuple[dict[str, Trail], dict[str, tuple[float, float]], list[str]]:
    lists: list[list[Trail]] = []
    errors: list[str] = []
    steps = [
        ("Wikipédia (PR)", wikipedia.PR_URL, wikipedia.parse_pr_list),
        ("Wikipédia (GR)", wikipedia.GR_URL, wikipedia.parse_gr_list),
        ("OpenStreetMap Wiki", osmwiki.URL, osmwiki.parse),
    ]
    for label, url, parser in steps:
        status.update(phase=f"A ler {label}")
        try:
            trails = parser(client.get(url).text)
            if not trails:
                raise ValueError("no trails found (page layout changed?)")
            log.info("%s: %d trails", label, len(trails))
            lists.append(trails)
        except Exception as e:
            log.exception("%s failed", label)
            errors.append(f"{label}: {e}")

    centres: dict[int, tuple[float, float]] = {}
    status.update(phase="A procurar percursos no OpenStreetMap (Overpass)")
    try:
        found = osm.discover(client)
        log.info("Overpass: %d relations", len(found))
        known_relations = {t.osm_relation for ts in lists for t in ts if t.osm_relation}
        extra = []
        for trail, centre in found:
            if centre:
                centres[trail.osm_relation] = centre
            if trail.osm_relation in known_relations:
                continue
            if not trail.code and trail.kind != "GR":
                # "PR1" alone is ambiguous: a placeholder id, the district comes from the map.
                trail.municipality = f"osm{trail.osm_relation}"
            extra.append(trail)
        lists.append(extra)
    except Exception as e:
        # Optional: the lists already link most trails to OSM.
        log.warning("Overpass discovery skipped: %s", e)
        errors.append(f"Overpass: {e}")

    trails = merge(lists)
    for t in trails.values():
        if t.municipality.startswith("osm"):
            t.municipality = ""
    points = {i: centres[t.osm_relation] for i, t in trails.items() if t.osm_relation in centres}
    return trails, points, errors


def save_catalog(conn: sqlite3.Connection, trails: dict[str, Trail], prune: bool) -> None:
    stamp = datetime.now(UTC).isoformat()   # unique per run (microseconds): used for pruning
    cols = db.CATALOG_COLUMNS
    with db.transaction(conn):
        for i, t in trails.items():
            values = {
                "ref": t.ref, "kind": t.kind, "number": t.number, "code": t.code, "name": t.name or t.ref,
                "district": t.district, "municipality": t.municipality, "distance_km": t.distance_km,
                "duration_min": t.duration_min, "difficulty": t.difficulty, "route_type": t.route_type,
                "promoter": t.promoter, "website": t.website, "osm_relation": t.osm_relation, "notes": t.notes,
                "closed": int(t.closed), "sources": json.dumps(t.sources, ensure_ascii=False),
                "seen_at": stamp, "updated_at": stamp,
            }
            conn.execute(
                f"INSERT INTO trails(id, {', '.join(cols)}) VALUES (?, {', '.join('?' for _ in cols)}) "
                f"ON CONFLICT(id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in cols)}",
                [i] + [values[c] for c in cols],
            )
        if prune:
            # Only when every list was read: a failed source must not delete its trails.
            conn.execute("DELETE FROM trails WHERE seen_at IS NOT ?", (stamp,))


def fetch_tracks(conn: sqlite3.Connection, client: Client, status: Status, limit: int | None = None) -> int:
    max_age = timedelta(hours=max(config.SCRAPE_INTERVAL_HOURS, 1) * 0.9)
    cutoff = (datetime.now(UTC) - max_age).replace(microsecond=0).isoformat()
    rows = conn.execute(
        "SELECT id, osm_relation FROM trails WHERE osm_relation IS NOT NULL AND "
        "(track_relation IS NOT osm_relation OR track_fetched_at IS NULL OR track_fetched_at < ?) "
        "ORDER BY track_fetched_at IS NOT NULL, track_fetched_at",
        (cutoff,),
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]
    done = 0
    for n, row in enumerate(rows, 1):
        if n % 10 == 1:
            status.update(phase="A descarregar percursos do OpenStreetMap", done=n - 1, total=len(rows))
        try:
            _, segments = osm.fetch_route(client, row["osm_relation"])
            if not segments:
                raise ValueError("relation has no ways")
            km = length_km(segments)
            if config.SIMPLIFY_METERS > 0:
                segments = [simplify(s, config.SIMPLIFY_METERS) for s in segments]
            track = [[[round(lat, 6), round(lon, 6)] for lat, lon in s] for s in segments]
            start = track[0][0]
            conn.execute(
                "UPDATE trails SET track = ?, track_km = ?, track_relation = ?, track_fetched_at = ?, "
                "track_error = NULL, start_lat = ?, start_lon = ?, bbox = ? WHERE id = ?",
                (json.dumps(track, separators=(",", ":")), round(km, 2), row["osm_relation"], now(),
                 start[0], start[1], json.dumps(bbox(segments)), row["id"]),
            )
            done += 1
        except Exception as e:
            log.warning("track %s (relation %s): %s", row["id"], row["osm_relation"], e)
            conn.execute(
                "UPDATE trails SET track_error = ?, track_fetched_at = ?, track_relation = ? WHERE id = ?",
                (str(e)[:300], now(), row["osm_relation"], row["id"]),
            )
    status.update(done=len(rows), total=len(rows))
    return done


def assign_districts_from_tracks(conn: sqlite3.Connection) -> None:
    """Trails still without a district get the one of the nearest trail, using the track start points."""
    rows = conn.execute("SELECT id, district, start_lat, start_lon FROM trails WHERE start_lat IS NOT NULL").fetchall()
    trails = {r["id"]: Trail(kind="PR", number="0", district=r["district"]) for r in rows}
    points = {r["id"]: (r["start_lat"], r["start_lon"]) for r in rows}
    missing = [i for i, t in trails.items() if not t.district]
    if not missing:
        return
    resolve_districts(trails, points)
    with db.transaction(conn):
        for i in missing:
            if trails[i].district:
                conn.execute("UPDATE trails SET district = ? WHERE id = ?", (trails[i].district, i))


def run(conn: sqlite3.Connection | None = None, catalog_only: bool = False, limit: int | None = None) -> dict:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("a scrape is already running")
    conn = conn or db.connect()
    status = Status(conn)
    client = Client()
    try:
        status.update(started_at=now(), finished_at=None, phase="A começar", error=None, done=0, total=0)
        trails, points, errors = collect(client, status)
        if not trails:
            raise RuntimeError("no source answered: " + "; ".join(errors))
        resolve_districts(trails, points)
        list_errors = [e for e in errors if not e.startswith("Overpass")]
        save_catalog(conn, trails, prune=not list_errors)
        fetched = 0
        if not catalog_only:
            fetched = fetch_tracks(conn, client, status, limit)
            assign_districts_from_tracks(conn)
        counts = dict(conn.execute(
            "SELECT count(*) AS trails, count(track) AS tracks FROM trails"
        ).fetchone())
        status.update(
            phase="Concluído", finished_at=now(), last_success=now(), warnings=errors,
            counts=counts, fetched=fetched,
        )
        return status.data
    except Exception as e:
        log.exception("scrape failed")
        status.update(phase="Falhou", finished_at=now(), error=str(e))
        raise
    finally:
        client.close()
        _lock.release()


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape the Portuguese homologated trails")
    parser.add_argument("--catalog-only", action="store_true", help="skip the OSM tracks")
    parser.add_argument("--limit", type=int, help="download at most N tracks")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    result = run(catalog_only=args.catalog_only, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
