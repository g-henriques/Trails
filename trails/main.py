"""Web app: JSON API, GPX downloads, the static site and the background scrape scheduler."""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, db, scrape
from .geo import to_gpx
from .model import DISTRICTS, estimate_difficulty, estimate_duration, slug

log = logging.getLogger("trails")
STATIC = Path(__file__).parent / "static"

# District capitals (Ponta Delgada and Funchal for the islands): where the district markers go.
DISTRICT_CENTRES = {
    "Aveiro": (40.6405, -8.6538), "Beja": (38.0151, -7.8632), "Braga": (41.5454, -8.4265),
    "Bragança": (41.8061, -6.7567), "Castelo Branco": (39.8222, -7.4909), "Coimbra": (40.2033, -8.4103),
    "Évora": (38.5714, -7.9135), "Faro": (37.0194, -7.9322), "Guarda": (40.5373, -7.2676),
    "Leiria": (39.7436, -8.8071), "Lisboa": (38.7223, -9.1393), "Portalegre": (39.2967, -7.4285),
    "Porto": (41.1579, -8.6291), "Santarém": (39.2369, -8.6850), "Setúbal": (38.5244, -8.8882),
    "Viana do Castelo": (41.6932, -8.8329), "Vila Real": (41.3006, -7.7441), "Viseu": (40.6566, -7.9125),
    "Açores": (37.7412, -25.6756), "Madeira": (32.6669, -16.9241),
}


# ---------------------------------------------------------------- scheduler


def _due(conn) -> bool:
    if config.SCRAPE_INTERVAL_HOURS <= 0:
        return False
    status = scrape.get_status(conn)
    if status.get("running"):
        return False
    last = status.get("last_success")
    if not last:
        # Never succeeded: retry, but not more than once an hour after a failure.
        started = status.get("started_at")
        return not started or status.get("phase") not in ("Falhou",) or _older(started, timedelta(hours=1))
    return _older(last, timedelta(hours=config.SCRAPE_INTERVAL_HOURS))


def _older(stamp: str, age: timedelta) -> bool:
    try:
        return datetime.fromisoformat(stamp) < datetime.now(UTC) - age
    except ValueError:
        return True


def _scheduler(stop: threading.Event) -> None:
    stop.wait(5)
    while not stop.is_set():
        try:
            conn = db.connect()
            if _due(conn):
                log.info("starting scheduled scrape")
                scrape.run(conn)
        except Exception:
            log.exception("scheduled scrape failed")
        stop.wait(900)


def start_scrape_thread() -> bool:
    if scrape.get_status(db.connect()).get("running"):
        return False

    def work():
        try:
            scrape.run()
        except Exception:
            log.exception("scrape failed")

    threading.Thread(target=work, name="scrape", daemon=True).start()
    time.sleep(0.1)
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    conn = db.connect()
    # A container restart in the middle of a scrape leaves "running" phases behind.
    status = db.get_meta(conn, "scrape", {}) or {}
    if status and not status.get("finished_at"):
        status.update(phase="Interrompido", finished_at=scrape.now())
        db.set_meta(conn, "scrape", status)
    stop = threading.Event()
    thread = threading.Thread(target=_scheduler, args=(stop,), name="scheduler", daemon=True)
    thread.start()
    yield
    stop.set()


app = FastAPI(title="Trilhos de Portugal", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)


# ---------------------------------------------------------------- helpers


def _summary(row) -> dict:
    official_km = row["distance_km"]
    km = official_km if official_km else row["track_km"]
    # Multi-day routes (GR, > 40 km): a single difficulty or walking time would be misleading.
    multi_day = row["kind"] == "GR" or (km or 0) > 40
    difficulty = row["difficulty"] or ("" if multi_day else estimate_difficulty(km))
    duration = row["duration_min"] or (None if multi_day else estimate_duration(km, difficulty))
    return {
        "id": row["id"], "ref": row["ref"], "kind": row["kind"], "name": row["name"],
        "district": row["district"], "municipality": row["municipality"],
        "distance_km": round(km, 1) if km else None,
        "distance_source": "oficial" if official_km else ("gpx" if km else None),
        "duration_min": duration, "duration_estimated": bool(duration and not row["duration_min"]),
        "difficulty": difficulty, "difficulty_estimated": bool(difficulty and not row["difficulty"]),
        "route_type": row["route_type"], "closed": bool(row["closed"]),
        "has_track": row["track_km"] is not None and row["has_track"],
        "lat": row["start_lat"], "lon": row["start_lon"],
    }


_SUMMARY_SQL = (
    "SELECT id, ref, kind, name, district, municipality, distance_km, duration_min, difficulty, route_type, "
    "closed, start_lat, start_lon, track_km, track IS NOT NULL AS has_track FROM trails"
)


def _row(trail_id: str):
    row = db.connect().execute(
        "SELECT *, track IS NOT NULL AS has_track FROM trails WHERE id = ?", (trail_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Trilho não encontrado")
    return row


# ---------------------------------------------------------------- API


@app.get("/healthz")
def healthz():
    db.connect().execute("SELECT 1").fetchone()
    return {"status": "ok"}


@app.get("/api/trails")
def list_trails():
    rows = db.connect().execute(
        _SUMMARY_SQL + " ORDER BY district = '', district, kind = 'GR', municipality = '', municipality, "
        "CAST(number AS REAL), code"
    ).fetchall()
    return [_summary(r) for r in rows]


@app.get("/api/trails/{trail_id}")
def trail_detail(trail_id: str):
    row = _row(trail_id)
    data = _summary(row)
    data.update({
        "official_distance_km": row["distance_km"], "track_km": row["track_km"],
        "promoter": row["promoter"], "website": row["website"], "notes": row["notes"],
        "osm_relation": row["osm_relation"],
        "osm_url": f"https://www.openstreetmap.org/relation/{row['osm_relation']}" if row["osm_relation"] else None,
        "sources": json.loads(row["sources"] or "[]"),
        "track": json.loads(row["track"]) if row["track"] else None,
        "bbox": json.loads(row["bbox"]) if row["bbox"] else None,
        "track_fetched_at": row["track_fetched_at"], "track_error": row["track_error"],
        "updated_at": row["updated_at"],
    })
    return data


@app.get("/api/trails/{trail_id}/gpx")
def trail_gpx(trail_id: str):
    row = _row(trail_id)
    if not row["track"]:
        raise HTTPException(404, "Este trilho ainda não tem traçado GPX")
    s = _summary(row)
    desc = " · ".join(x for x in [
        f"{s['distance_km']} km" if s["distance_km"] else "", s["difficulty"], row["route_type"],
        f"{row['municipality']} ({row['district']})" if row["municipality"] else row["district"],
    ] if x)
    gpx = to_gpx(f"{row['ref']} {row['name']}", desc, json.loads(row["track"]), row["website"])
    filename = f"{slug(row['ref'])}-{slug(row['name'])[:60]}.gpx"
    return Response(gpx, media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/districts")
def districts():
    rows = db.connect().execute(_SUMMARY_SQL).fetchall()
    out = {d: {"name": d, "count": 0, "with_track": 0, "km": 0.0, "lat": DISTRICT_CENTRES[d][0],
               "lon": DISTRICT_CENTRES[d][1], "bbox": None} for d in DISTRICTS}
    for r in rows:
        d = out.get(r["district"])
        if d is None:
            continue
        s = _summary(r)
        d["count"] += 1
        d["km"] += s["distance_km"] or 0
        if s["has_track"]:
            d["with_track"] += 1
            lat, lon = s["lat"], s["lon"]
            b = d["bbox"] or [lat, lon, lat, lon]
            d["bbox"] = [min(b[0], lat), min(b[1], lon), max(b[2], lat), max(b[3], lon)]
    for d in out.values():
        d["km"] = round(d["km"])
    return list(out.values())


@app.get("/api/status")
def status():
    conn = db.connect()
    data = scrape.get_status(conn)
    counts = conn.execute("SELECT count(*) AS trails, count(track) AS tracks FROM trails").fetchone()
    data["trails"], data["tracks"] = counts["trails"], counts["tracks"]
    data["interval_hours"] = config.SCRAPE_INTERVAL_HOURS
    return data


@app.post("/api/scrape", status_code=202)
def start_scrape():
    if not start_scrape_thread():
        return JSONResponse({"detail": "Já está a decorrer uma atualização"}, status_code=409)
    return {"detail": "Atualização iniciada"}


# ---------------------------------------------------------------- site


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
