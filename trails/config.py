"""Settings from environment variables (see .env.example)."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


DB_PATH = os.environ.get("TRAILS_DB", "/data/trails.db")
# Hours between full scrapes (catalog + tracks). 0 disables the automatic scrape.
SCRAPE_INTERVAL_HOURS = _int("TRAILS_SCRAPE_INTERVAL_HOURS", 168)
# Seconds between requests to the same site (OpenStreetMap asks for moderate use).
REQUEST_DELAY = _float("TRAILS_REQUEST_DELAY", 1.0)
USER_AGENT = os.environ.get(
    "TRAILS_USER_AGENT", "TrilhosPortugal/1.0 (self-hosted trail catalogue; +https://github.com/g-henriques/trails)"
)
OSM_API = os.environ.get("TRAILS_OSM_API", "https://api.openstreetmap.org/api/0.6").rstrip("/")
# Overpass is only used to discover trails missing from the lists; comma-separated, tried in order.
# Empty = the public servers below; "off" disables the discovery step.
_OVERPASS_DEFAULT = (
    "https://overpass-api.de/api/interpreter,https://overpass.kumi.systems/api/interpreter,"
    "https://overpass.private.coffee/api/interpreter"
)
_overpass = os.environ.get("TRAILS_OVERPASS_URLS", "").strip() or _OVERPASS_DEFAULT
OVERPASS_URLS = [] if _overpass == "off" else [u.strip() for u in _overpass.split(",") if u.strip()]
# Tracks are simplified to this tolerance (metres) before being stored; 0 keeps every OSM node.
SIMPLIFY_METERS = _float("TRAILS_SIMPLIFY_METERS", 2.0)
