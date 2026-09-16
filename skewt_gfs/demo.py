"""Explicitly synthetic input, isolated from the live data directory."""
import math
from dataclasses import replace
from datetime import datetime, timezone

from .grib import Grid
from .planning import floor_cycle, target_hours
from .runner import produce
from .storage import Store, Lock


def synthetic_grid(cfg, cycle, valid):
    west, east, south, north = cfg.bounds
    coordinates = [(south + a*.25, west + b*.25)
                   for a in range(round((north-south)*4)+1) for b in range(round((east-west)*4)+1)]
    lats, lons = map(list, zip(*coordinates))
    terrain = [max(150, min(3950, 150 + (-lon-64)*1000)) for lon in lons]
    fields = {("terrain_m", 0): terrain,
              ("surface_pressure_pa", 0): [101325*math.exp(-z/8200) for z in terrain],
              ("surface_temperature_k", 0): [300-z*.006 for z in terrain],
              ("surface_dewpoint_k", 0): [292-z*.006 for z in terrain],
              ("surface_u_ms", 0): [3.0]*len(lats), ("surface_v_ms", 0): [2.0]*len(lats)}
    phase = (valid-cycle).total_seconds()/3600
    for p in cfg.levels:
        h = -8200*math.log(p/1013.25)
        values = {"height_m": h, "temperature_k": max(205., 301-.0065*h)+.3*math.sin(phase),
                  "rh_percent": 45+15*math.sin(p/180), "u_ms": 3+h/1800, "v_ms": 2+h/3000}
        for name, value in values.items():
            fields[(name, p)] = [value]*len(lats)
    return Grid(lats, lons, fields, cycle, valid)


def demo(cfg, now=None):
    now = now or datetime.now(timezone.utc)
    root = cfg.source.parent / "demo"
    if root.resolve() == cfg.root.resolve():
        raise ValueError("La carpeta demo debe ser distinta de los datos reales")
    cfg = replace(cfg, root=root, png=False)
    store = Store(root)
    with Lock(root / ".run.lock"):
        return produce(cfg, store, floor_cycle(now), target_hours(now, cfg.horizon_hours, cfg.step_hours), synthetic_grid, demo=True)
