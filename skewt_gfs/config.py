from __future__ import annotations

import hashlib
import json
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    lat: float
    lon: float
    elevation_m: float | None = None


@dataclass
class Config:
    source: Path
    root: Path
    stations: list[Station]
    levels: list[int]
    timezone: str = "America/La_Paz"
    horizon_hours: int = 6
    step_hours: int = 1
    poll_seconds: int = 900
    max_cycle_age_hours: int = 18
    request_pause_seconds: float = 10.0
    timeout_seconds: float = 60.0
    download_deadline_seconds: float = 180.0
    retries: int = 2
    max_download_mb: int = 20
    raw_days: int = 30
    image_days: int = 90
    failed_days: int = 7
    extraction: str = "nearest"
    max_distance_km: float = 50.0
    terrain_warning_m: float = 500.0
    min_levels: int = 8
    max_top_hpa: float = 200.0
    max_gap_hpa: float = 150.0
    png: bool = True
    dpi: int = 130
    logo: Path | None = None
    include_secondary_levels: bool = True

    def scientific_dict(self):
        return {
            "stations": [vars(s) for s in self.stations], "levels": self.levels,
            "include_secondary_levels": self.include_secondary_levels,
            "extraction": self.extraction, "max_distance_km": self.max_distance_km,
            "terrain_warning_m": self.terrain_warning_m, "min_levels": self.min_levels,
            "max_top_hpa": self.max_top_hpa, "max_gap_hpa": self.max_gap_hpa,
            "horizon_hours": self.horizon_hours, "step_hours": self.step_hours,
            "timezone": self.timezone, "png": self.png, "dpi": self.dpi,
            "logo_sha256": hashlib.sha256(self.logo.read_bytes()).hexdigest() if self.logo else None,
        }

    @property
    def fingerprint(self):
        from . import __version__
        content = {"version": __version__, **self.scientific_dict()}
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:12]

    @property
    def bounds(self):
        # A common region, including enough grid points to interpolate at its edges.
        west = max(-180.0, math.floor((min(s.lon for s in self.stations) - .5) * 4) / 4)
        east = min(180.0, math.ceil((max(s.lon for s in self.stations) + .5) * 4) / 4)
        south = max(-90.0, math.floor((min(s.lat for s in self.stations) - .5) * 4) / 4)
        north = min(90.0, math.ceil((max(s.lat for s in self.stations) + .5) * 4) / 4)
        return west, east, south, north


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    allowed = {"storage", "forecast", "download", "quality", "graphics", "stations"}
    if set(data) - allowed:
        raise ValueError(f"Secciones desconocidas: {set(data) - allowed}")
    sections = {
        "storage": {"root", "raw_days", "image_days", "failed_days"},
        "forecast": {"timezone", "horizon_hours", "step_hours", "poll_seconds", "max_cycle_age_hours", "levels", "include_secondary_levels"},
        "download": {"request_pause_seconds", "timeout_seconds", "download_deadline_seconds", "retries", "max_download_mb"},
        "quality": {"extraction", "max_distance_km", "terrain_warning_m", "min_levels", "max_top_hpa", "max_gap_hpa"},
        "graphics": {"png", "dpi", "logo"},
    }
    values = {}
    for section, keys in sections.items():
        block = data.get(section, {})
        if set(block) - keys:
            raise ValueError(f"Opciones desconocidas en {section}: {set(block) - keys}")
        values.update(block)
    root = Path(values.pop("root", "data")).expanduser()
    root = (path.parent / root).resolve() if not root.is_absolute() else root.resolve()
    if root == path.parent or root == Path(root.anchor):
        raise ValueError("La carpeta de datos debe ser una subcarpeta dedicada, no la del programa ni la raíz.")
    logo = values.pop("logo", "")
    logo_path = (path.parent / logo).resolve() if logo else None
    if logo_path and not logo_path.is_file():
        raise ValueError(f"No se encuentra el logo: {logo_path}")
    stations = []
    for row in data.get("stations", []):
        if set(row) - {"id", "name", "lat", "lon", "elevation_m"}:
            raise ValueError("Campos de estación desconocidos")
        s = Station(**row)
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,63}", s.id):
            raise ValueError("El identificador de estación debe usar A-Z, 0-9, _ o -.")
        if not s.name or len(s.name) > 100:
            raise ValueError("Nombre de estación vacío o demasiado largo")
        if not math.isfinite(s.lat) or not -89.5 <= s.lat <= 89.5 or not math.isfinite(s.lon) or not -179.5 <= s.lon <= 179.5:
            raise ValueError(f"Coordenadas no admitidas para {s.id}")
        if s.elevation_m is not None and (not math.isfinite(s.elevation_m) or not -500 <= s.elevation_m <= 9000):
            raise ValueError("Elevación de referencia inválida")
        stations.append(s)
    if not stations or len({s.id for s in stations}) != len(stations):
        raise ValueError("Debe haber estaciones con identificadores únicos")
    if max(s.lon for s in stations) - min(s.lon for s in stations) > 30 or max(s.lat for s in stations) - min(s.lat for s in stations) > 30:
        raise ValueError("Región demasiado extensa o cruza el antimeridiano; separe las estaciones en configuraciones.")
    levels = values.pop("levels", [1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100, 70, 50])
    if not levels or any(type(p) is not int or not 1 <= p <= 1000 for p in levels) or len(set(levels)) != len(levels):
        raise ValueError("Niveles inválidos o duplicados")
    from .dataset import SECONDARY_LEVELS
    extended = values.get("include_secondary_levels", True)
    if type(extended) is not bool:
        raise ValueError("include_secondary_levels debe ser true o false")
    if extended:
        levels = sorted(set(levels) | {p for p in SECONDARY_LEVELS if min(levels) <= p <= max(levels)}, reverse=True)
    cfg = Config(path, root, stations, sorted(levels, reverse=True), logo=logo_path, **values)
    ZoneInfo(cfg.timezone)
    limits = {"horizon_hours": (1, 72), "step_hours": (1, 6), "poll_seconds": (60, 86400),
              "max_cycle_age_hours": (6, 48), "retries": (0, 5), "max_download_mb": (1, 200),
              "raw_days": (1, 3650), "image_days": (1, 3650), "failed_days": (1, 365),
              "min_levels": (5, 50), "dpi": (72, 300)}
    for key, (lower, upper) in limits.items():
        value = getattr(cfg, key)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"{key} debe ser entero entre {lower} y {upper}")
    for key, low, high in [("request_pause_seconds", 10, 3600), ("timeout_seconds", 5, 300),
                           ("download_deadline_seconds", 10, 900), ("max_distance_km", 1, 100),
                           ("terrain_warning_m", 0, 5000), ("max_top_hpa", 50, 300), ("max_gap_hpa", 25, 300)]:
        value = getattr(cfg, key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} debe estar entre {low} y {high}")
    if cfg.extraction not in ("nearest", "bilinear") or type(cfg.png) is not bool:
        raise ValueError("Método de extracción o opción PNG inválido")
    if cfg.horizon_hours % cfg.step_hours:
        raise ValueError("El horizonte debe ser múltiplo del intervalo")
    if len(cfg.levels) < cfg.min_levels or min(cfg.levels) > cfg.max_top_hpa:
        raise ValueError("Los niveles configurados no satisfacen la cobertura vertical mínima")
    return cfg
