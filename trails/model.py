"""Trail record shared by every source, plus the text parsing helpers they use."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field

# Districts of mainland Portugal plus the two autonomous regions, in display order.
DISTRICTS = [
    "Aveiro", "Beja", "Braga", "Bragança", "Castelo Branco", "Coimbra", "Évora", "Faro",
    "Guarda", "Leiria", "Lisboa", "Portalegre", "Porto", "Santarém", "Setúbal",
    "Viana do Castelo", "Vila Real", "Viseu", "Açores", "Madeira",
]

# Difficulty scale used across the site (FCMP sheets use similar words).
DIFFICULTIES = ["Fácil", "Médio", "Algo difícil", "Difícil", "Muito difícil"]


@dataclass
class Trail:
    kind: str                      # PR (pequena rota), PRC/PRL (Açores), PL (local), GR (grande rota)
    number: str                    # "3", "1.2"
    code: str = ""                 # municipality code, e.g. "ARC" for Arouca
    name: str = ""
    district: str = ""
    municipality: str = ""         # municipality, or island in the autonomous regions
    distance_km: float | None = None
    duration_min: int | None = None
    difficulty: str = ""
    route_type: str = ""           # Circular | Linear
    promoter: str = ""
    website: str = ""
    osm_relation: int | None = None
    notes: str = ""
    closed: bool = False
    sources: list[str] = field(default_factory=list)
    id: str = ""                   # set when merging; defaults to key

    @property
    def ref(self) -> str:
        return f"{self.kind}{self.number} {self.code}".strip()

    @property
    def key(self) -> str:
        return trail_key(self.kind, self.number, self.code, self.municipality or self.district)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ref"] = self.ref
        d["id"] = self.id or self.key
        return d


def trail_key(kind: str, number: str, code: str, place: str = "") -> str:
    """Stable id: "pr3-arc". GR numbers are national; a PR without a code (Madeira) gets its island."""
    if kind == "GR" or (not code and not place):
        return slug(f"{kind}{number}")
    return slug(f"{kind}{number}-{code or place}")


def island(name: str) -> str:
    """'Ilha do Porto Santo' -> 'Porto Santo'."""
    return re.sub(r"^Ilha\s+(?:de\s+|do\s+|da\s+)?", "", name or "").strip()


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9.]+", "-", text.lower()).strip("-")


def fold(text: str) -> str:
    """Lower-case, accent-free text for comparisons."""
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower().strip()


# "PR3 ARC", "PR 03-ARC", "PRC05SMI", "PR1.2", "GR11-E9", "PL2 AMR"
REF_RE = re.compile(
    r"^\s*(PRC|PRL|PR|PL|GR)\s*[-–]?\s*0*(\d+(?:\.\d+)?)"
    r"(?:\s*[-–]?\s*([A-Z]{2,4})(?![a-zà-ÿ]))?",
)


def parse_ref(text: str) -> tuple[str, str, str, str] | None:
    """Split "PR3 ARC Rest" into (kind, number, code, rest). Codes like E9 (GR11-E9) are kept in rest."""
    m = REF_RE.match(text or "")
    if not m:
        return None
    kind, number, code = m.group(1), m.group(2), m.group(3) or ""
    return kind, number, code, text[m.end():].strip(" -–:")


def parse_km(text: str) -> float | None:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:km|quil[oó]metros)\b", text or "", re.I)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return value if 0 < value < 2000 else None


def parse_duration(text: str) -> int | None:
    """Minutes from "4h", "3h30", "2 h 30 min", "45 min", "3 horas"."""
    text = text or ""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:h|horas?)\b\s*(?:e\s*)?(\d{1,2})?\s*(?:m|min|minutos)?\b", text, re.I)
    if not m:
        m = re.search(r"(\d+)\s*h(\d{2})\b", text, re.I)
    if m:
        hours = float(m.group(1).replace(",", "."))
        minutes = int(m.group(2) or 0)
        total = round(hours * 60) + minutes
        return total if 0 < total < 60 * 24 * 30 else None
    m = re.search(r"\b(\d{2,3})\s*(?:min|minutos)\b", text, re.I)
    return int(m.group(1)) if m else None


def parse_difficulty(text: str) -> str:
    t = fold(text)
    if "muito dificil" in t or "muito exigente" in t:
        return "Muito difícil"
    if "algo dificil" in t or "medio/dificil" in t or "media/alta" in t:
        return "Algo difícil"
    if re.search(r"\bdificil\b|\bexigente\b|\belevad[ao]\b", t):
        return "Difícil"
    if re.search(r"\bmedi[oa]\b|\bmoderad[oa]\b", t):
        return "Médio"
    if re.search(r"\bfacil\b|\bbaix[ao]\b", t):
        return "Fácil"
    return ""


def parse_route_type(text: str) -> str:
    t = fold(text)
    if "circular" in t:
        return "Circular"
    if "linear" in t:
        return "Linear"
    return ""


def clean(text: str) -> str:
    text = re.sub(r"\[\s*\d+\s*\]", "", text or "")       # wiki footnotes: [ 2 ]
    return re.sub(r"\s+", " ", text).strip()


def estimate_duration(distance_km: float | None, difficulty: str) -> int | None:
    """Walking time without stops, from distance and difficulty (no elevation data)."""
    if not distance_km:
        return None
    speeds = {"Fácil": 4.0, "Médio": 3.5, "Algo difícil": 3.0, "Difícil": 2.75, "Muito difícil": 2.5}
    speed = speeds.get(difficulty, 3.5)
    minutes = distance_km / speed * 60
    return int(round(minutes / 15) * 15) or 15


def estimate_difficulty(distance_km: float | None) -> str:
    if not distance_km:
        return ""
    if distance_km < 8:
        return "Fácil"
    if distance_km < 15:
        return "Médio"
    if distance_km < 22:
        return "Algo difícil"
    if distance_km < 35:
        return "Difícil"
    return "Muito difícil"
