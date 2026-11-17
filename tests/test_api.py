import json

import pytest
from fastapi.testclient import TestClient

from trails import config, db, scrape
from trails.model import Trail


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "api.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "SCRAPE_INTERVAL_HOURS", 0)
    conn = db.connect(path)
    trails = scrape.merge([[
        Trail(kind="PR", number="1", code="ARC", name="Caminhos do Montemuro", district="Aveiro",
              municipality="Arouca", distance_km=19, difficulty="Médio", route_type="Circular",
              website="https://example.pt/pr1", osm_relation=1),
        Trail(kind="PR", number="2", code="ARC", name="Sem traçado", district="Aveiro", municipality="Arouca"),
    ]])
    scrape.save_catalog(conn, trails, prune=True)
    track = [[[40.9, -8.2], [40.91, -8.21], [40.92, -8.2]]]
    conn.execute("UPDATE trails SET track = ?, track_km = 2.6, start_lat = 40.9, start_lon = -8.2, bbox = ? "
                 "WHERE id = 'pr1-arc'", (json.dumps(track), json.dumps([40.9, -8.21, 40.92, -8.2])))
    from trails.main import app
    with TestClient(app) as c:
        yield c


def test_health_and_index(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/")
    assert r.status_code == 200 and "Trilhos de Portugal" in r.text
    assert client.get("/static/app.js").status_code == 200


def test_list_and_detail(client):
    trails = {t["id"]: t for t in client.get("/api/trails").json()}
    t = trails["pr1-arc"]
    assert (t["distance_km"], t["distance_source"]) == (19, "oficial")
    assert (t["difficulty"], t["difficulty_estimated"]) == ("Médio", False)
    assert t["duration_estimated"] and t["duration_min"] == 330 and t["has_track"]
    assert trails["pr2-arc"]["has_track"] is False and trails["pr2-arc"]["distance_km"] is None

    d = client.get("/api/trails/pr1-arc").json()
    assert d["track"][0][0] == [40.9, -8.2] and d["osm_url"].endswith("/relation/1")
    assert client.get("/api/trails/nope").status_code == 404


def test_gpx(client):
    r = client.get("/api/trails/pr1-arc/gpx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/gpx+xml")
    assert "pr1-arc-caminhos-do-montemuro.gpx" in r.headers["content-disposition"]
    assert r.text.count("<trkpt") == 3
    assert client.get("/api/trails/pr2-arc/gpx").status_code == 404


def test_districts_and_status(client):
    districts = {d["name"]: d for d in client.get("/api/districts").json()}
    assert len(districts) == 20
    assert (districts["Aveiro"]["count"], districts["Aveiro"]["with_track"]) == (2, 1)
    s = client.get("/api/status").json()
    assert (s["trails"], s["tracks"], s["running"]) == (2, 1, False)
