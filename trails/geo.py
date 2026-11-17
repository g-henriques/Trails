"""Geometry helpers: join OSM ways into tracks, measure them and export GPX."""

from __future__ import annotations

import math
from xml.sax.saxutils import escape

Point = tuple[float, float]          # (lat, lon)
Segment = list[Point]


def haversine_km(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(h))


def length_km(segments: list[Segment]) -> float:
    return sum(haversine_km(s[i], s[i + 1]) for s in segments for i in range(len(s) - 1))


def chain(ways: list[Segment], tolerance_km: float = 0.03) -> list[Segment]:
    """Join ways (in relation order) into as few continuous segments as possible.

    OSM route members are usually ordered but not oriented: each way is flipped when that
    connects it to the end of the current segment (and the segment's first way may be
    flipped too). Ways that do not connect within tolerance_km start a new segment.
    """
    segments: list[Segment] = []
    single = False                      # current segment is still a single way (free to flip)
    for way in ways:
        if len(way) < 2:
            continue
        if segments:
            cur = segments[-1]
            ends = (way[0], way[-1])
            if single and cur[-1] not in ends and cur[0] in ends:
                cur.reverse()
            d_start = haversine_km(cur[-1], way[0])
            d_end = haversine_km(cur[-1], way[-1])
            if min(d_start, d_end) <= tolerance_km:
                part = list(way) if d_start <= d_end else list(reversed(way))
                cur.extend(part[1:] if part[0] == cur[-1] else part)
                single = False
                continue
        segments.append(list(way))
        single = True
    return segments


def simplify(points: Segment, tolerance_m: float = 2.0) -> Segment:
    """Ramer–Douglas–Peucker on a local flat projection; keeps the track shape within tolerance_m."""
    if len(points) < 3:
        return points
    lat0 = math.radians(points[0][0])
    kx, ky = 111320 * math.cos(lat0), 110540
    xy = [(p[1] * kx, p[0] * ky) for p in points]
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = tolerance_m ** 2
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        norm = dx * dx + dy * dy
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            if norm == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / norm))
                d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d2 > best:
                best, idx = d2, i
        if best > tol2:
            keep[idx] = True
            stack.extend([(a, idx), (idx, b)])
    return [p for p, k in zip(points, keep, strict=True) if k]


def bbox(segments: list[Segment]) -> list[float] | None:
    pts = [p for s in segments for p in s]
    if not pts:
        return None
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    return [min(lats), min(lons), max(lats), max(lons)]


def to_gpx(name: str, description: str, segments: list[Segment], link: str = "") -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Trilhos de Portugal (dados OpenStreetMap, ODbL)" '
        'xmlns="http://www.topografix.com/GPX/1/1">',
        "<metadata>",
        f"<name>{escape(name)}</name>",
        f"<desc>{escape(description)}</desc>",
        "<copyright author=\"OpenStreetMap contributors\"><license>https://opendatacommons.org/licenses/odbl/</license></copyright>",
    ]
    if link:
        out.append(f'<link href="{escape(link, {chr(34): "&quot;"})}"><text>Página oficial</text></link>')
    out += ["</metadata>", "<trk>", f"<name>{escape(name)}</name>"]
    for seg in segments:
        out.append("<trkseg>")
        out.extend(f'<trkpt lat="{lat:.6f}" lon="{lon:.6f}"/>' for lat, lon in seg)
        out.append("</trkseg>")
    out += ["</trk>", "</gpx>", ""]
    return "\n".join(out)
