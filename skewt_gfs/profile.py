from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .planning import iso


@dataclass
class Row:
    pressure_hpa: float
    altitude_m: float
    temperature_c: float
    dewpoint_c: float
    rh_percent: float
    u_ms: float
    v_ms: float
    wind_speed_ms: float
    wind_direction_deg: float | None
    source: str = "isobaric"


def distance_km(lat1, lon1, lat2, lon2):
    lat1, lat2 = math.radians(lat1), math.radians(lat2)
    dlat = lat2 - lat1
    dlon = math.radians(((lon2 - lon1 + 180) % 360) - 180)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(min(1, a)))


def wind_direction(u, v):
    return None if math.hypot(u, v) < .05 else (270 - math.degrees(math.atan2(v, u))) % 360


def dewpoint(temperature_c, rh_percent):
    # Magnus over liquid water. Very cold upper-air dewpoints remain an approximation.
    if not math.isfinite(rh_percent) or not 0 < rh_percent <= 100:
        raise ValueError("RH fuera de (0,100]")
    gamma = math.log(rh_percent / 100) + 17.625 * temperature_c / (243.04 + temperature_c)
    return 243.04 * gamma / (17.625 - gamma)


def select_nodes(grid, station, method, max_distance):
    distances = [distance_km(station.lat, station.lon, a, b) for a, b in zip(grid.latitudes, grid.longitudes)]
    if not distances:
        raise ValueError("Malla vacía")
    if method == "nearest":
        index = min(range(len(distances)), key=distances.__getitem__)
        nodes = [(index, 1.0)]
    else:
        lats, lons = sorted(set(grid.latitudes)), sorted(set(grid.longitudes))
        def bracket(axis, value):
            if not axis[0] <= value <= axis[-1]:
                raise ValueError("Estación fuera de la región descargada")
            lower = max(v for v in axis if v <= value)
            upper = min(v for v in axis if v >= value)
            if lower == upper:
                return [(lower, 1.0)]
            f = (value - lower) / (upper - lower)
            return [(lower, 1-f), (upper, f)]
        coordinates = {(a, b): i for i, (a, b) in enumerate(zip(grid.latitudes, grid.longitudes))}
        nodes = [(coordinates[(a, b)], wa * wb)
                 for a, wa in bracket(lats, station.lat) for b, wb in bracket(lons, station.lon)]
    if any(distances[i] > max_distance for i, _ in nodes):
        raise ValueError(f"Nodo a más de {max_distance} km; revise región/coordenadas")
    return nodes, [{"lat": grid.latitudes[i], "lon": grid.longitudes[i],
                    "weight": w, "distance_km": round(distances[i], 3)} for i, w in nodes]


def extract_profile(grid, station, cfg):
    nodes, node_meta = select_nodes(grid, station, cfg.extraction, cfg.max_distance_km)
    notes = []
    def values(name, p=0):
        return [grid.fields[(name, p)][i] for i, _ in nodes]
    def finite(seq):
        return all(math.isfinite(v) for v in seq)
    def mean(seq):
        return sum(v * w for v, (_, w) in zip(seq, nodes))
    surface_fields = {name: values(name) for name in ("surface_pressure_pa", "terrain_m", "surface_temperature_k", "surface_dewpoint_k", "surface_u_ms", "surface_v_ms")}
    if not all(finite(v) for v in surface_fields.values()):
        raise ValueError(f"{station.id}: falta superficie en los nodos fijos; no se buscarán vecinos lejanos")
    sp = surface_fields["surface_pressure_pa"]
    terrain = surface_fields["terrain_m"]
    if not all(20000 < p < 110000 for p in sp) or not all(-500 < h < 9000 for h in terrain):
        raise ValueError("Presión superficial o terreno inválidos")
    for t, d in zip(surface_fields["surface_temperature_k"], surface_fields["surface_dewpoint_k"]):
        if not (150 < t < 340 and 130 < d <= t + .5):
            raise ValueError("Temperatura o punto de rocío superficial inválidos")
    if any(abs(v) > 200 for name in ("surface_u_ms", "surface_v_ms") for v in surface_fields[name]):
        raise ValueError("Viento superficial fuera de rango")
    h0 = mean(terrain)
    if station.elevation_m is None:
        notes.append("Sin elevación de estación verificada; referencia vertical = terreno del modelo.")
    elif abs(h0 - station.elevation_m) > cfg.terrain_warning_m:
        notes.append(f"Terreno modelo ({h0:.0f} m) difiere de referencia ({station.elevation_m:.0f} m); representatividad local limitada.")
    t0 = mean(surface_fields["surface_temperature_k"]) - 273.15
    d0 = min(t0, mean(surface_fields["surface_dewpoint_k"]) - 273.15)
    u0, v0 = mean(surface_fields["surface_u_ms"]), mean(surface_fields["surface_v_ms"])
    rh0 = min(100., 100 * math.exp(17.625 * d0 / (243.04 + d0) - 17.625 * t0 / (243.04 + t0)))
    rows = [Row(mean(sp) / 100, h0 + 2, t0, d0, rh0, u0, v0, math.hypot(u0, v0), wind_direction(u0, v0), "surface_2m_T_Td_10m_wind")]
    dropped = []
    for p in cfg.levels:
        raw = {name: values(name, p) for name in ("temperature_k", "height_m", "u_ms", "v_ms", "rh_percent")}
        if any(p * 100 >= ps for ps in sp):
            dropped.append({"pressure_hpa": p, "reason": "below_model_surface"})
            continue
        if not all(finite(v) for v in raw.values()):
            dropped.append({"pressure_hpa": p, "reason": "missing_at_fixed_nodes"})
            continue
        if any(h <= z + 2 for h, z in zip(raw["height_m"], terrain)):
            dropped.append({"pressure_hpa": p, "reason": "height_below_model_surface"})
            continue
        if not all(150 < t < 340 for t in raw["temperature_k"]):
            raise ValueError(f"Temperatura inválida a {p} hPa")
        if not all(0 <= r <= 105 for r in raw["rh_percent"]):
            raise ValueError(f"RH inválida a {p} hPa")
        if any(abs(v) > 200 for name in ("u_ms", "v_ms") for v in raw[name]):
            raise ValueError(f"Viento inválido a {p} hPa")
        rh = mean(raw["rh_percent"])
        if rh < .1 or rh > 100:
            notes.append(f"RH a {p} hPa acotada desde {rh:.3f}% a [0.1,100]% para calcular Td.")
        rh = min(100, max(.1, rh))
        t = mean(raw["temperature_k"]) - 273.15
        u, v = mean(raw["u_ms"]), mean(raw["v_ms"])
        rows.append(Row(p, mean(raw["height_m"]), t, dewpoint(t, rh), rh, u, v, math.hypot(u, v), wind_direction(u, v)))
    if len(rows) < cfg.min_levels or rows[-1].pressure_hpa > cfg.max_top_hpa:
        raise ValueError(f"{station.id}: cobertura vertical insuficiente")
    for lower, upper in zip(rows, rows[1:]):
        if upper.pressure_hpa >= lower.pressure_hpa or upper.altitude_m <= lower.altitude_m:
            raise ValueError("Perfil sin monotonía de presión/altura")
        if lower.pressure_hpa - upper.pressure_hpa > cfg.max_gap_hpa:
            raise ValueError("Hueco vertical excesivo")
    return {"station": asdict(station), "cycle_utc": iso(grid.cycle), "valid_utc": iso(grid.valid),
            "forecast_hour": int((grid.valid - grid.cycle).total_seconds() / 3600),
            "extraction": cfg.extraction, "nodes": node_meta, "model_terrain_m": h0,
            "surface_reference": "Superficie modelada: T/Td a 2 m y viento a 10 m; no observación de estación.",
            "dewpoint_method": "Magnus sobre agua: a=17.625,b=243.04; aproximación en aire muy frío",
            "warnings": notes, "dropped_levels": dropped, "rows": [asdict(row) for row in rows]}


def compute_indices(profile):
    from .indices import calculate
    return calculate(profile)
