from pathlib import Path

import pytest

from trails.model import (
    Trail,
    estimate_difficulty,
    estimate_duration,
    parse_difficulty,
    parse_duration,
    parse_km,
    parse_ref,
    parse_route_type,
)
from trails.sources import osmwiki, wikipedia

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("text,expected", [
    ("PR3 ARC Rota", ("PR", "3", "ARC", "Rota")),
    ("PR 03-ARC - Rota", ("PR", "3", "ARC", "Rota")),
    ("PRC05SMI", ("PRC", "5", "SMI", "")),
    ("PR8 Malara (11 km)", ("PR", "8", "", "Malara (11 km)")),
    ("PR1.2 Vereda", ("PR", "1.2", "", "Vereda")),
    ("GR11-E9 Caminho do Atlântico", ("GR", "11", "", "E9 Caminho do Atlântico")),
    ("PL2 AMR Abadia", ("PL", "2", "AMR", "Abadia")),
])
def test_parse_ref(text, expected):
    assert parse_ref(text) == expected


def test_parse_ref_rejects_other_text():
    assert parse_ref("Rota do Ouro") is None


def test_parse_details():
    assert parse_km("PR circular de 14 km") == 14
    assert parse_km("(10,8 km - Algo difícil)") == 10.8
    assert parse_km("sem distância") is None
    assert parse_duration("14 km - 4h - Fácil") == 240
    assert parse_duration("3h30") == 210
    assert parse_duration("2 h 30 min") == 150
    assert parse_duration("45 min") == 45
    assert parse_duration("14 km") is None
    assert parse_difficulty("Algo difícil") == "Algo difícil"
    assert parse_difficulty("médio (nível III)") == "Médio"
    assert parse_difficulty("moderado") == "Médio"
    assert parse_difficulty("muito difícil") == "Muito difícil"
    assert parse_difficulty("difícil") == "Difícil"
    assert parse_difficulty("Fácil (nível II)") == "Fácil"
    assert parse_difficulty("circular") == ""
    assert parse_route_type("PR circular de 14 km") == "Circular"
    assert parse_route_type("Linear") == "Linear"


def test_estimates():
    assert estimate_duration(12, "Fácil") == 180
    assert estimate_duration(None, "Fácil") is None
    assert estimate_difficulty(5) == "Fácil"
    assert estimate_difficulty(40) == "Muito difícil"


def test_keys():
    assert Trail(kind="PR", number="3", code="ARC").key == "pr3-arc"
    assert Trail(kind="GR", number="11").key == "gr11"
    assert Trail(kind="PR", number="1", municipality="Porto Santo", district="Madeira").key == "pr1-porto-santo"


def test_wikipedia_pr_list():
    trails = wikipedia.parse_pr_list((FIX / "wiki_pr.html").read_text())
    by = {}
    for t in trails:
        by.setdefault(t.key, []).append(t)
    assert "pr9-xyz" not in by  # navbox ignored

    t = by["pr1-arc"][0]
    assert (t.name, t.district, t.municipality, t.distance_km, t.difficulty, t.route_type) == (
        "Caminhos do Montemuro", "Aveiro", "Arouca", 19, "Médio", "Circular")

    t = by["pr3-arc"][0]
    assert (t.name, t.duration_min, t.difficulty) == ("Caminhos do Sol Nascente", 270, "Fácil")

    assert by["pr1-agd"][0].website == "https://www.cm-agueda.pt/pr1"
    assert by["pr8-bgc"][0].name == "Malara"          # code borrowed from the municipality
    assert len(by["pr1-bgc"]) == 2                     # two trails, same reference
    t = by["pr3-cvr"][0]
    assert t.promoter.startswith("LPN") and t.distance_km == 19 and "[" not in t.promoter

    madeira = by["pr1-madeira"][0]
    assert (madeira.district, madeira.difficulty) == ("Madeira", "Difícil")
    assert by["pr1-porto-santo"][0].district == "Madeira"


def test_wikipedia_gr_list():
    trails = wikipedia.parse_gr_list((FIX / "wiki_gr.html").read_text())
    assert [t.key for t in trails] == ["gr11", "gr28"]
    assert trails[0].name == "Caminho do Atlântico (E9)"
    assert (trails[0].distance_km, trails[0].route_type) == (263, "Linear")
    assert trails[1].municipality == "Arouca"


def test_osm_wiki():
    trails = {t.key: t for t in osmwiki.parse((FIX / "osmwiki.html").read_text())}
    t = trails["pr1-sma"]
    assert (t.name, t.district, t.municipality, t.osm_relation, t.distance_km, t.route_type) == (
        "Costa Norte", "Açores", "Santa Maria", 5633397, 8.4, "Linear")
    assert t.website == "https://trails.visitazores.com/costa-norte"
    assert trails["prl1-sma"].closed and trails["prl1-sma"].osm_relation is None
    assert trails["pr1-arc"].district == "Aveiro"
    assert (trails["gr28"].district, trails["gr28"].osm_relation) == ("Aveiro", 7654321)
