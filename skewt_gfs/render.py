from __future__ import annotations

import csv
import html
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .planning import parse_time
from .storage import atomic_json


def index_lines(profile):
    if not profile.get("parcels"):
        return ["Sin índices calculados en esta vista"]
    def fmt(value):
        return "N/D" if value is None else f"{value:.0f}"
    lines = ["Parcela  CAPE    CIN    LCL", "         J/kg   J/kg   m AGL"]
    for name, parcel in profile["parcels"].items():
        cin = "s/LFC" if parcel["status"] == "no_lfc" else fmt(parcel["CIN_J_kg"])
        lines.append(f"{name:3} {fmt(parcel['CAPE_J_kg']):>8} {cin:>6} {fmt(parcel['LCL_m_AGL']):>6}")
    lines += ["s/LFC: CIN no definido sin LFC", "AGL: sobre el terreno del modelo",
              "SB: superficie · MU: 0–3 km", "ML: capa hasta 500 m AGL",
              f"LCL SB: {profile['indices']['LCL_hPa']:.0f} hPa"]
    if any(p["status"] == "el_above_profile" for p in profile["parcels"].values()):
        lines.append("Sin EL: CAPE limitado al tope")
    return lines


def write_profile(profile, folder: Path, stem: str):
    atomic_json(folder / f"{stem}.json", profile)
    rows = profile["rows"]
    columns = ["station_id", "cycle_utc", "valid_utc", "forecast_hour", *rows[0].keys()]
    with (folder / f"{stem}.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({"station_id": profile["station"]["id"], "cycle_utc": profile["cycle_utc"],
                             "valid_utc": profile["valid_utc"], "forecast_hour": profile["forecast_hour"], **row})


def svg_plot(profile, path: Path, tz: str, demo=False):
    """Self-contained vector Skew-T and hodograph. No network or optional packages."""
    esc = html.escape
    rows = profile["rows"]
    pbottom = max(1050, rows[0]["pressure_hpa"] + 20)
    ptop = max(50, rows[-1]["pressure_hpa"])
    left, top, width, height = 85, 140, 620, 590
    def y(p):
        return top + height * math.log(p / ptop) / math.log(pbottom / ptop)
    def x(t, p):
        return left + width * (t + 45 + 35 * math.log(1000 / p)) / 95
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 850" role="img"><title>{esc(profile["station"]["name"])} - Skew-T</title>',
             '<rect width="1120" height="850" fill="#f4f7fa"/>',
             '<style>text{font-family:Arial,sans-serif;fill:#183044;font-size:13px}.small{font-size:11px}.title{font-size:25px;font-weight:bold}</style>',
             f'<defs><clipPath id="plot"><rect x="{left}" y="{top}" width="{width}" height="{height}"/></clipPath></defs>']
    label = "DEMOSTRACIÓN SINTÉTICA · NO ES UN PRONÓSTICO" if demo else "GFS 0,25° · PERFIL MODELADO"
    valid_local = parse_time(profile["valid_utc"]).astimezone(ZoneInfo(tz)).strftime("%d/%m/%Y %H:%M %Z")
    parts += [f'<text x="50" y="38" fill="#d34a36">{label}</text>',
              f'<text x="50" y="75" class="title">{esc(profile["station"]["name"])}</text>',
              f'<text x="50" y="103">Válido: {esc(valid_local)} · {esc(profile["valid_utc"])} · Ciclo {esc(profile["cycle_utc"])} · f{profile["forecast_hour"]:03d}</text>',
              f'<rect x="{left}" y="{top}" width="{width}" height="{height}" fill="white" stroke="#b7c6d1"/>']
    for p in (1000, 900, 800, 700, 600, 500, 400, 300, 250, 200, 150, 100, 70, 50):
        if ptop <= p <= pbottom:
            parts += [f'<line x1="{left}" y1="{y(p):.1f}" x2="{left+width}" y2="{y(p):.1f}" stroke="#d9e1e7"/>',
                      f'<text x="{left-12}" y="{y(p)+4:.1f}" text-anchor="end">{p}</text>']
    parts.append('<g clip-path="url(#plot)">')
    for t in range(-150, 51, 10):
        parts.append(f'<line x1="{x(t,pbottom):.1f}" y1="{y(pbottom):.1f}" x2="{x(t,ptop):.1f}" y2="{y(ptop):.1f}" stroke="#dedede"/>')
    pressures = [pbottom * (ptop / pbottom) ** (i / 80) for i in range(81)]
    for theta in range(250, 491, 20):
        coords = " ".join(f"{x(theta*(p/1000)**(287.05/1004)-273.15,p):.1f},{y(p):.1f}" for p in pressures)
        parts.append(f'<polyline points="{coords}" fill="none" stroke="#deb887" stroke-opacity=".5" stroke-dasharray="3 5"/>')
    for key, color in [("temperature_c", "#d8453e"), ("dewpoint_c", "#12835d"), ("parcel_temperature_c", "#7761a9")]:
        if key in rows[0]:
            trace = profile.get("parcel_trace") if key == "parcel_temperature_c" else None
            points = zip(trace["pressure_hpa"], trace["temperature_c"]) if trace else ((r["pressure_hpa"], r[key]) for r in rows)
            coords = " ".join(f'{x(temp,pres):.1f},{y(pres):.1f}' for pres, temp in points)
            parts.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2.8"/>')
    parts.append('</g>')
    for t in range(-40, 51, 10):
        parts.append(f'<text x="{x(t,1000):.1f}" y="755" text-anchor="middle">{t}</text>')
    parts += ['<text x="85" y="130">Presión (hPa)</text>', '<text x="280" y="780">Temperatura (°C, referencia 1000 hPa)</text>',
              '<text x="780" y="160" font-weight="bold">Hodógrafa · u/v en m/s</text>']
    cx, cy, radius = 910, 315, 125
    speed_range = max(20, math.ceil(max(r["wind_speed_ms"] for r in rows) / 10) * 10)
    for speed in range(10, speed_range+1, 10):
        r = radius * speed / speed_range
        parts += [f'<circle cx="{cx}" cy="{cy}" r="{r:.2f}" fill="none" stroke="#bdcbd6"/>',
                  f'<text x="{cx+r:.1f}" y="{cy-5}" class="small">{speed}</text>']
    parts += [f'<line x1="{cx-radius}" y1="{cy}" x2="{cx+radius}" y2="{cy}" stroke="#bdcbd6"/>',
              f'<line x1="{cx}" y1="{cy-radius}" x2="{cx}" y2="{cy+radius}" stroke="#bdcbd6"/>']
    points = " ".join(f'{cx+r["u_ms"]/speed_range*radius:.1f},{cy-r["v_ms"]/speed_range*radius:.1f}' for r in rows)
    parts.append(f'<polyline points="{points}" fill="none" stroke="#285a91" stroke-width="2.5"/>')
    lines = index_lines(profile) + ["Rojo: T · Verde: Td · Violeta: SB",
             f'Terreno modelo: {profile["model_terrain_m"]:.0f} m',
             f'Extracción: {profile["extraction"]}', "Base: T/Td 2 m; viento 10 m"]
    lines.append(f'Avisos de calidad: {len(profile["warnings"])} (ver JSON)')
    for i, line in enumerate(lines):
        parts.append(f'<text x="755" y="{465+i*21}" style="font-family:monospace;white-space:pre">{esc(line)}</text>')
    parts += ['<text x="50" y="825" class="small">Perfil del modelo, no radiosondeo observado. Superficie referida al terreno de GFS. Datos y procedencia en CSV/JSON.</text>', '</svg>']
    path.write_text("\n".join(parts), encoding="utf-8")


def png_plot(profile, path: Path, cfg):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from metpy.plots import SkewT, Hodograph
    from metpy.units import units
    rows = profile["rows"]
    p = np.array([r["pressure_hpa"] for r in rows]) * units.hPa
    t = units.Quantity([r["temperature_c"] for r in rows], "degC")
    td = units.Quantity([r["dewpoint_c"] for r in rows], "degC")
    u = np.array([r["u_ms"] for r in rows]) * units("m/s")
    v = np.array([r["v_ms"] for r in rows]) * units("m/s")
    fig = plt.figure(figsize=(12, 8), facecolor="#f4f7fa")
    try:
        skew = SkewT(fig, rotation=45, rect=(.08, .13, .55, .72))
        skew.plot(p, t, color="#d8453e", linewidth=2, label="Temperatura")
        skew.plot(p, td, color="#12835d", linewidth=2, label="Rocío")
        trace = profile["parcel_trace"]
        skew.plot(np.array(trace["pressure_hpa"]) * units.hPa,
                  units.Quantity(trace["temperature_c"], "degC"),
                  color="#7761a9", linewidth=1.5, label="Parcela SB modelo")
        skew.plot(profile["indices"]["LCL_hPa"] * units.hPa,
                  units.Quantity(profile["indices"]["LCL_temperature_C"], "degC"),
                  marker="o", color="#7761a9", markersize=5, label="LCL SB")
        skew.plot_barbs(p, u.to("knots"), v.to("knots"))
        skew.ax.set_ylim(min(1050, rows[0]["pressure_hpa"]+25), 100)
        skew.ax.set_xlim(-40, 45)
        skew.plot_dry_adiabats(alpha=.3)
        skew.plot_moist_adiabats(alpha=.3)
        skew.plot_mixing_lines(alpha=.25)
        skew.ax.set_xlabel("Temperatura (°C)")
        skew.ax.set_ylabel("Presión (hPa)")
        skew.ax.legend(loc="best", fontsize=8)
        ax = fig.add_axes((.72, .49, .23, .34))
        hodo = Hodograph(ax, component_range=max(20, math.ceil(max(r["wind_speed_ms"] for r in rows)/10)*10))
        hodo.add_grid(increment=10)
        hodo.plot(u.magnitude, v.magnitude, color="#285a91", linewidth=2)
        ax.set_title("Hodógrafa · m/s", fontsize=10)
        local = parse_time(profile["valid_utc"]).astimezone(ZoneInfo(cfg.timezone)).strftime("%d/%m/%Y %H:%M %Z")
        fig.suptitle(profile["station"]["name"] + " · GFS 0,25°", fontsize=17, y=.965)
        fig.text(.08, .905, f"Válido: {local} | {profile['valid_utc']}\nCiclo: {profile['cycle_utc']} | f{profile['forecast_hour']:03d}", fontsize=9)
        text = "\n".join(index_lines(profile) + [
            f"Terreno GFS: {profile['model_terrain_m']:.0f} m",
            f"Método: {profile['extraction']} · Avisos: {len(profile['warnings'])}",
            "T/Td: 2 m · viento: 10 m",
            "Barbas: kt · hodógrafa: m/s"])
        fig.text(.70, .43, text, va="top", fontsize=8.5, linespacing=1.4, family="monospace")
        fig.text(.08, .035, "Perfil modelado, no radiosondeo observado. La orografía local puede diferir del terreno GFS.", fontsize=8)
        if cfg.logo:
            logo_ax = fig.add_axes((.88, .88, .08, .08))
            logo_ax.imshow(plt.imread(cfg.logo))
            logo_ax.axis("off")
        fig.savefig(path, dpi=cfg.dpi, facecolor=fig.get_facecolor())
    finally:
        plt.close(fig)


def gallery(manifest, path: Path, prefix="", demo=False):
    esc = html.escape
    cards = []
    for product in manifest["products"]:
        stem = product["stem"]
        figure = stem + (".png" if product.get("png") else ".svg")
        title = f'{product["station_name"]} · {product["valid_local"]}'
        cards.append(f'<article><h2>{esc(title)}</h2><a href="{esc(prefix+figure)}"><img loading="lazy" src="{esc(prefix+figure)}" alt="{esc(title)}"></a><p><a href="{esc(prefix+stem)}.csv">Datos CSV</a> · <a href="{esc(prefix+stem)}.json">Procedencia y calidad</a></p></article>')
    banner = "DEMOSTRACIÓN SINTÉTICA · NO USAR COMO PRONÓSTICO" if demo else "GFS · Perfiles atmosféricos modelados"
    text = f'''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Perfiles GFS</title>
<style>body{{font:16px system-ui;background:#eef3f7;color:#183044;margin:0}}header,main{{max-width:1200px;margin:auto;padding:24px}}header{{background:#183044;color:white}}h1{{font-size:26px}}h2{{font-size:17px}}main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:20px}}article{{background:white;border-radius:12px;padding:16px}}img{{width:100%}}a{{color:#17649a}}header a{{color:#bce3ff}}.status{{font-weight:bold}}.stale{{background:#8a2d20;color:white;padding:12px}}</style>
<header><h1>{banner}</h1><p>Ciclo: {esc(manifest['cycle_utc'])} · Generado: {esc(manifest['created_utc'])}</p><p class="status" id="age"></p><p>Vigencia mostrada en cada figura. No son observaciones. Consulte los avisos de calidad de cada perfil.</p><a href="{esc(prefix)}manifest.json">Registro completo</a></header><main>{''.join(cards)}</main>
<script>const end=new Date({__import__('json').dumps(manifest['last_valid_utc'])});function age(){{const old=Date.now()>end.getTime();const e=document.getElementById('age');e.textContent=old?'Este lote ya no cubre la hora actual. Consulte el estado de las descargas.':'Cobertura del lote hasta '+end.toLocaleString();e.className=old?'stale':'status';}}age();setInterval(age,60000);</script></html>'''
    path.write_text(text, encoding="utf-8")
