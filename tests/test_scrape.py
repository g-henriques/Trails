import json
from pathlib import Path

from trails import db, scrape
from trails.geo import chain, length_km, simplify, to_gpx
from trails.model import Trail
from trails.sources import osm, osmwiki, wikipedia

FIX = Path(__file__).parent / "fixtures"


def test_chain_orients_and_joins_ways():
    a = [(0.0, 0.0), (0.0, 0.01)]
    b = [(0.0, 0.02), (0.0, 0.01)]          # reversed
    c = [(0.0, 0.02), (0.0, 0.03)]
    far = [(1.0, 1.0), (1.0, 1.01)]
    segs = chain([a, b, c, far])
    assert segs[0] == [(0.0, 0.0), (0.0, 0.01), (0.0, 0.02), (0.0, 0.03)]
    assert len(segs) == 2


def test_chain_flips_first_way():
    a = [(0.0, 0.01), (0.0, 0.0)]           # first way drawn backwards
    b = [(0.0, 0.0), (0.0, -0.01)]
    assert chain([b, a]) == [[(0.0, -0.01), (0.0, 0.0), (0.0, 0.01)]]


def test_length_and_simplify():
    line = [(38.0, -9.0 + i * 0.0001) for i in range(101)]   # straight ~0.88 km
    assert 0.85 < length_km([line]) < 0.9
    assert simplify(line, 2) == [line[0], line[-1]]


def test_gpx_is_escaped():
    gpx = to_gpx("PR1 <A&B>", "desc", [[(38.1, -9.2), (38.2, -9.3)]], "https://x.pt/?a=1&b=2")
    assert "&lt;A&amp;B&gt;" in gpx and 'lat="38.100000" lon="-9.200000"' in gpx and "a=1&amp;b=2" in gpx


def test_merge_sources():
    pr = wikipedia.parse_pr_list((FIX / "wiki_pr.html").read_text())
    gr = wikipedia.parse_gr_list((FIX / "wiki_gr.html").read_text())
    ow = osmwiki.parse((FIX / "osmwiki.html").read_text())
    merged = scrape.merge([pr, gr, ow])
    t = merged["pr1-arc"]
    assert t.osm_relation == 1234567 and t.difficulty == "Médio"
    assert t.sources == ["Wikipédia", "OpenStreetMap Wiki"]
    assert merged["pr1-bgc"].name != merged["pr1-bgc-2"].name
    assert merged["gr28"].osm_relation == 7654321
    assert merged["pr2-arc"].district == "Aveiro"     # only in the OSM wiki


def test_merge_across_sources_prefers_similar_name():
    a = [Trail(kind="PR", number="1", code="BGC", name="Entre Carvalhais"),
         Trail(kind="PR", number="1", code="BGC", name="Colado")]
    b = [Trail(kind="PR", number="1", code="BGC", name="Colado", osm_relation=9)]
    merged = scrape.merge([a, b])
    assert merged["pr1-bgc-2"].osm_relation == 9 and merged["pr1-bgc"].osm_relation is None


def test_resolve_districts():
    trails = {
        "pr1-arc": Trail(kind="PR", number="1", code="ARC", district="Aveiro", municipality="Arouca"),
        "pr9-arc": Trail(kind="PR", number="9", code="ARC"),
        "gr28": Trail(kind="GR", number="28", municipality="Arouca - Castelo de Paiva"),
        "x": Trail(kind="PR", number="1"),
        "y": Trail(kind="PR", number="2"),
    }
    points = {"pr1-arc": (40.93, -8.25), "x": (40.95, -8.2), "y": (37.75, -25.6)}
    scrape.resolve_districts(trails, points)
    assert (trails["pr9-arc"].district, trails["pr9-arc"].municipality) == ("Aveiro", "Arouca")
    assert trails["gr28"].district == "Aveiro"
    assert trails["x"].district == "Aveiro"
    assert trails["y"].district == "Açores"


class FakeResponse:
    def __init__(self, data):
        self._data = data
        self.text = json.dumps(data)

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, routes=None, overpass=None):
        self.routes = routes or {}
        self.overpass = overpass

    def get(self, url, **kw):
        rid = int(url.split("/relation/")[1].split("/")[0])
        return FakeResponse(self.routes[rid])

    def post(self, url, **kw):
        if self.overpass is None:
            raise ConnectionError("down")
        return FakeResponse(self.overpass)


def _route(rid, ways, members=None, tags=None):
    elements = [{"type": "relation", "id": rid, "tags": tags or {}, "members": members or [
        {"type": "way", "ref": w, "role": ""} for w in ways]}]
    for w, pts in ways.items():
        ids = []
        for lat, lon in pts:
            nid = int(lat * 1e6) * 10 + int(abs(lon) * 1e6)
            ids.append(nid)
            elements.append({"type": "node", "id": nid, "lat": lat, "lon": lon})
        elements.append({"type": "way", "id": w, "nodes": ids})
    return {"elements": elements}


def test_fetch_route_and_superroute():
    child = _route(2, {20: [(40.0, -8.0), (40.0, -8.01)]})
    parent = _route(1, {10: [(41.0, -8.0), (41.0, -8.01)]},
                    members=[{"type": "way", "ref": 10, "role": ""}, {"type": "relation", "ref": 2, "role": ""},
                             {"type": "node", "ref": 5, "role": "guidepost"}],
                    tags={"ref": "GR1"})
    tags, segs = osm.fetch_route(FakeClient({1: parent, 2: child}), 1)
    assert tags["ref"] == "GR1" and len(segs) == 2


def test_discover():
    overpass = {"elements": [
        {"type": "relation", "id": 11, "tags": {"ref": "PR 4 ARC", "name": "PR4 ARC - Cercanias da Freita",
                                                "distance": "13,3", "roundtrip": "yes"},
         "center": {"lat": 40.9, "lon": -8.2}},
        {"type": "relation", "id": 12, "tags": {"ref": "E9"}},
    ]}
    found = osm.discover(FakeClient(overpass=overpass))
    assert len(found) == 1
    trail, centre = found[0]
    assert (trail.key, trail.name, trail.distance_km, trail.route_type, trail.osm_relation, centre) == (
        "pr4-arc", "Cercanias da Freita", 13.3, "Circular", 11, (40.9, -8.2))


def test_save_catalog_and_tracks(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    trails = scrape.merge([[Trail(kind="PR", number="1", code="ARC", name="A", district="Aveiro", osm_relation=1),
                            Trail(kind="PR", number="2", code="ARC", name="B", district="Aveiro", osm_relation=2)]])
    scrape.save_catalog(conn, trails, prune=True)
    routes = {1: _route(1, {10: [(40.9, -8.2), (40.91, -8.21)]})}   # relation 2 is missing (404-like)
    status = scrape.Status(conn)

    class C(FakeClient):
        def get(self, url, **kw):
            if "/relation/2/" in url:
                raise RuntimeError("gone")
            return super().get(url)

    assert scrape.fetch_tracks(conn, C(routes), status) == 1
    row = conn.execute("SELECT * FROM trails WHERE id = 'pr1-arc'").fetchone()
    assert row["track_km"] > 1 and row["start_lat"] == 40.9
    assert conn.execute("SELECT track_error FROM trails WHERE id = 'pr2-arc'").fetchone()[0] == "gone"
    # Second run: nothing is due any more.
    assert scrape.fetch_tracks(conn, C(routes), status) == 0
    # A catalog without pr2 prunes it, but only when prune=True.
    scrape.save_catalog(conn, {"pr1-arc": trails["pr1-arc"]}, prune=False)
    assert conn.execute("SELECT count(*) FROM trails").fetchone()[0] == 2
    scrape.save_catalog(conn, {"pr1-arc": trails["pr1-arc"]}, prune=True)
    assert conn.execute("SELECT count(*) FROM trails").fetchone()[0] == 1
    # The track survives a catalog update.
    assert conn.execute("SELECT track IS NOT NULL FROM trails").fetchone()[0] == 1
