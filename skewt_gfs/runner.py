from __future__ import annotations

import importlib.metadata
import json
import logging
import time
import uuid
import platform
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from . import __version__
from .download import Downloader, DownloadError
from .grib import read_grib
from .planning import candidate_cycles, forecast_hours, target_hours, iso, parse_time
from .profile import extract_profile, compute_indices
from .render import write_profile, svg_plot, png_plot, gallery
from .storage import Store, Lock, atomic_json, sha256

LOG = logging.getLogger(__name__)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def versions():
    result = {"skewt_gfs": __version__, "python": platform.python_version()}
    for name in ("eccodes", "eccodeslib", "eckitlib", "metpy", "numpy", "matplotlib", "scipy", "pandas", "xarray", "pint", "tzdata"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "not_installed"
    return result


def produce(cfg, store, cycle, targets, grid_loader, *, downloader=None, demo=False):
    started = time.monotonic()
    created = datetime.now(timezone.utc)
    run_id = f"{cycle:%Y%m%dT%H}Z_{cfg.fingerprint}_{created:%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:8]}"
    folder = store.root / "staging" / run_id
    folder.mkdir()
    manifest = {"run_id": run_id, "status": "building", "synthetic": demo,
                "cycle_utc": iso(cycle), "created_utc": iso(created),
                "first_valid_utc": iso(targets[0]), "last_valid_utc": iso(targets[-1]),
                "config_fingerprint": cfg.fingerprint, "configuration": cfg.scientific_dict(),
                "software": versions(), "inputs": [], "products": []}
    try:
        for valid, hour in zip(targets, forecast_hours(cycle, targets)):
            if downloader:
                path, metadata = downloader.download(cycle, hour)
                manifest["inputs"].append(metadata)
                grid = grid_loader(path, cycle, hour, cfg.levels)
            else:
                grid = grid_loader(cfg, cycle, valid)
            for station in cfg.stations:
                profile = extract_profile(grid, station, cfg)
                profile["synthetic"] = demo
                profile["software"] = manifest["software"]
                if not demo:
                    compute_indices(profile)
                    profile["input"] = metadata
                stem = f"{station.id}_{valid:%Y%m%dT%H%M}Z"
                write_profile(profile, folder, stem)
                svg_plot(profile, folder / (stem + ".svg"), cfg.timezone, demo=demo)
                if cfg.png and not demo:
                    png_plot(profile, folder / (stem + ".png"), cfg)
                manifest["products"].append({"station_id": station.id, "station_name": station.name,
                    "valid_utc": iso(valid), "valid_local": valid.astimezone(ZoneInfo(cfg.timezone)).strftime("%d/%m %H:%M %Z"),
                    "stem": stem, "png": cfg.png and not demo, "warnings": profile["warnings"]})
        expected = len(targets) * len(cfg.stations)
        if len(manifest["products"]) != expected:
            raise ValueError("Lote incompleto")
        manifest.update(status="complete", elapsed_seconds=round(time.monotonic()-started, 3),
                        transferred_bytes=downloader.transferred_bytes if downloader else 0)
        manifest["files"] = {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)} for p in folder.iterdir() if p.is_file()}
        atomic_json(folder / "manifest.json", manifest)
        gallery(manifest, folder / "index.html", demo=demo)
        destination = store.root / "runs" / run_id
        if not folder.resolve().is_relative_to(store.root) or not destination.resolve().is_relative_to(store.root):
            raise ValueError("Ruta de publicación fuera del almacén")
        folder.rename(destination)
        previous = read_json(store.root / "latest.json")
        # Historical/manual runs are archived without replacing a newer forecast.
        promote = not previous or (cycle >= parse_time(previous["cycle_utc"]) and targets[-1] >= parse_time(previous["last_valid_utc"]))
        if promote:
            temporary = store.root / "index.html.tmp"
            gallery(manifest, temporary, prefix=f"runs/{run_id}/", demo=demo)
            temporary.replace(store.root / "index.html")
            atomic_json(store.root / "latest.json", {k: manifest[k] for k in ("run_id", "cycle_utc", "last_valid_utc", "config_fingerprint", "synthetic")})
        LOG.info("Lote %s: %d perfiles, %d bytes transferidos, %.1f segundos", run_id, expected, manifest["transferred_bytes"], manifest["elapsed_seconds"])
        return manifest
    except Exception as exc:
        if folder.exists():
            atomic_json(folder / "failure.json", {"error": str(exc), "created_utc": iso(created), "run_id": run_id})
        raise


def run(cfg, *, now=None, cycle=None, force=False):
    now = now or datetime.now(timezone.utc)
    targets = target_hours(now, cfg.horizon_hours, cfg.step_hours)
    store = Store(cfg.root)
    with Lock(store.root / ".run.lock"):
        try:
            last = read_json(store.root / "latest.json")
            downloader = Downloader(cfg, store)
            chosen = None
            candidates = [cycle] if cycle is not None else candidate_cycles(now, cfg.max_cycle_age_hours)
            for candidate in candidates:
                if cycle is None and last and candidate < parse_time(last["cycle_utc"]):
                    break
                # One batch per cycle; refresh that cycle if its displayed window has expired.
                if not force and last.get("cycle_utc") == iso(candidate) and last.get("config_fingerprint") == cfg.fingerprint and parse_time(last["last_valid_utc"]) > now:
                    status = {"status": "unchanged", "checked_utc": iso(now), "run_id": last["run_id"]}
                    atomic_json(store.root / "status.json", status)
                    return status
                hours = forecast_hours(candidate, targets)
                if downloader.available(candidate, max(hours)):
                    chosen = candidate
                    break
            if chosen is None:
                raise DownloadError("No hay un ciclo disponible dentro de la antigüedad permitida")
            result = produce(cfg, store, chosen, targets, read_grib, downloader=downloader)
            atomic_json(store.root / "status.json", {"status": "complete", "checked_utc": iso(now), "run_id": result["run_id"],
                "elapsed_seconds": result["elapsed_seconds"], "transferred_bytes": result["transferred_bytes"]})
            try:
                result["cleanup"] = store.cleanup(cfg.raw_days, cfg.image_days, cfg.failed_days)
            except Exception:
                LOG.exception("Lote publicado, pero falló la limpieza; revisar espacio de disco")
            return result
        except Exception as exc:
            atomic_json(store.root / "status.json", {"status": "error", "checked_utc": iso(now), "error": str(exc)})
            raise
