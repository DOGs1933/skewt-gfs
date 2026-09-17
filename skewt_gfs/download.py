from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .planning import iso
from . import __version__
from .dataset import SECONDARY_LEVELS, DIAGNOSTIC_LAYERS, products
from .storage import atomic_json, sha256

LOG = logging.getLogger(__name__)
FILTER = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
DIRECT = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"


class DownloadError(RuntimeError):
    pass


class NotAvailable(DownloadError):
    pass


def selection(cfg, product="primary"):
    if product not in ("primary", "secondary"):
        raise ValueError("Producto GFS desconocido")
    secondary = product == "secondary"
    return {"bounds": cfg.bounds, "product": product,
            "levels": [p for p in cfg.levels if (p in SECONDARY_LEVELS) == secondary],
            "variables": ["TMP", "HGT", "UGRD", "VGRD", "RH"] + ([] if secondary else ["PRES", "DPT", "CAPE", "CIN"]),
            "extra_levels": [] if secondary else ["surface", "2_m_above_ground", "10_m_above_ground",
                *[f"{p}-0_mb_above_ground" for p in DIAGNOSTIC_LAYERS if p]]}


def build_url(cfg, cycle: datetime, hour: int, product="primary"):
    if hour not in range(121):
        raise ValueError("Plazo no admitido")
    west, east, south, north = cfg.bounds
    # Crossing Greenwich is represented by signed longitude endpoints.
    if west < 0 and east < 0:
        west, east = west + 360, east + 360
    spec = selection(cfg, product)
    suffix = "b" if product == "secondary" else ""
    params = {"file": f"gfs.t{cycle:%H}z.pgrb2{suffix}.0p25.f{hour:03d}",
              **{f"lev_{p}_mb": "on" for p in spec["levels"]},
              **{f"var_{v}": "on" for v in spec["variables"]},
              **{f"lev_{v}": "on" for v in spec["extra_levels"]},
              "subregion": "", "leftlon": west, "rightlon": east,
              "toplat": north, "bottomlat": south,
              "dir": f"/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos"}
    return FILTER.replace("0p25.pl", f"0p25{suffix}.pl") + "?" + urlencode(params)


def validate_grib(path: Path):
    """Check every complete GRIB2 message boundary, not only the first four bytes."""
    size = path.stat().st_size
    count = 0
    with path.open("rb") as stream:
        offset = 0
        while offset < size:
            stream.seek(offset)
            header = stream.read(16)
            if len(header) != 16 or header[:4] != b"GRIB" or header[7] != 2:
                raise DownloadError(f"Contenido GRIB2 inválido en byte {offset}")
            length = int.from_bytes(header[8:16], "big")
            if length < 20 or offset + length > size:
                raise DownloadError("Mensaje GRIB2 truncado")
            stream.seek(offset + length - 4)
            if stream.read(4) != b"7777":
                raise DownloadError("Fin de mensaje GRIB2 ausente")
            offset += length
            count += 1
    if not count:
        raise DownloadError("GRIB vacío")
    return count


class Downloader:
    def __init__(self, cfg, store, opener=urlopen, sleeper=time.sleep, monotonic=time.monotonic):
        self.cfg, self.store = cfg, store
        self.opener, self.sleep, self.clock = opener, sleeper, monotonic
        self.last_request_end = None
        self.transferred_bytes = 0

    def _get(self, url, path=None):
        if self.last_request_end is not None:
            self.sleep(max(0, self.cfg.request_pause_seconds - (self.clock() - self.last_request_end)))
        started = self.clock()
        request = Request(url, headers={"User-Agent": f"skewt-gfs/{__version__} regional sounding application"})
        try:
            with self.opener(request, timeout=self.cfg.timeout_seconds) as response:
                if path is None:
                    content = response.read(8192)
                    self.transferred_bytes += len(content)
                    return content
                total = 0
                with path.open("wb") as stream:
                    while True:
                        if self.clock() - started > self.cfg.download_deadline_seconds:
                            raise DownloadError("Tiempo total de descarga excedido")
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        self.transferred_bytes += len(chunk)
                        if total > self.cfg.max_download_mb * 1_000_000:
                            raise DownloadError("Descarga excede límite; compruebe el recorte regional")
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                return total
        finally:
            self.last_request_end = self.clock()

    def available(self, cycle, hour):
        return all(self._available_product(cycle, hour, product) for product in products(self.cfg))

    def _available_product(self, cycle, hour, product):
        suffix = "b" if product == "secondary" else ""
        url = f"{DIRECT}/gfs.{cycle:%Y%m%d}/{cycle:%H}/atmos/gfs.t{cycle:%H}z.pgrb2{suffix}.0p25.f{hour:03d}.idx"
        for attempt in range(self.cfg.retries + 1):
            try:
                text = self._get(url).decode("utf-8", errors="replace")
                if f":d={cycle:%Y%m%d%H}" not in text:
                    raise DownloadError("El inventario NOAA no corresponde al ciclo esperado")
                return True
            except HTTPError as exc:
                if exc.code == 404:
                    return False
                if exc.code not in (408, 429, 500, 502, 503, 504):
                    raise DownloadError(f"NOAA devolvió HTTP {exc.code}; no se considerará un ciclo antiguo como solución") from exc
                failure = exc
            except (URLError, TimeoutError, OSError) as exc:
                failure = exc
            if attempt < self.cfg.retries:
                self.sleep(min(60, 10 * 2 ** attempt))
        raise DownloadError(f"No se pudo consultar NOAA: {failure}")

    def download(self, cycle, hour):
        parts = [self._download_product(cycle, hour, product) for product in products(self.cfg)]
        if len(parts) == 1:
            return parts[0]
        # Keep each source and hash independently; only assemble a complete pair.
        sources = [meta for _, meta in parts]
        signature = hashlib.sha256(json.dumps([m["sha256"] for m in sources]).encode()).hexdigest()[:12]
        path = parts[0][0].with_name(f"profile_f{hour:03d}_{signature}.grib2")
        meta_path = path.with_suffix(".json")
        if path.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta["sha256"] == sha256(path) and meta["sources"] == sources:
                    validate_grib(path)
                    return path, meta
            except (ValueError, KeyError, DownloadError):
                pass
        temporary = path.with_suffix(".part")
        try:
            with temporary.open("wb") as target:
                for source, _ in parts:
                    with source.open("rb") as stream:
                        shutil.copyfileobj(stream, target)
                target.flush()
                os.fsync(target.fileno())
            count = validate_grib(temporary)
            temporary.replace(path)
            meta = {"cycle_utc": iso(cycle), "forecast_hour": hour, "sources": sources,
                    "bytes": path.stat().st_size, "messages": count, "sha256": sha256(path),
                    "assembly": "pgrb2 + pgrb2b; same cycle, forecast hour and regional bounds"}
            atomic_json(meta_path, meta)
            return path, meta
        finally:
            if temporary.exists():
                self.store.delete_owned_file(temporary)

    def _download_product(self, cycle, hour, product):
        spec = selection(self.cfg, product)
        key = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12]
        folder = self.store.root / "raw" / f"{cycle:%Y%m%dT%H}Z_{key}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"gfs_f{hour:03d}.grib2"
        meta_path = path.with_suffix(".json")
        if path.is_file() and meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta["sha256"] == sha256(path) and meta["cycle_utc"] == iso(cycle) and meta["forecast_hour"] == hour:
                    validate_grib(path)
                    return path, meta
            except (ValueError, KeyError, DownloadError):
                LOG.warning("Caché incompleta o corrupta: %s", path)
        url = build_url(self.cfg, cycle, hour, product)
        temporary = path.with_suffix(".part")
        for attempt in range(self.cfg.retries + 1):
            try:
                self._get(url, temporary)
                messages = validate_grib(temporary)
                temporary.replace(path)
                meta = {"cycle_utc": iso(cycle), "forecast_hour": hour, "url": url,
                        "selection": spec, "bytes": path.stat().st_size, "messages": messages,
                        "sha256": sha256(path)}
                atomic_json(meta_path, meta)
                return path, meta
            except HTTPError as exc:
                if exc.code == 404:
                    raise NotAvailable(f"Archivo f{hour:03d} todavía no disponible") from exc
                if exc.code not in (408, 429, 500, 502, 503, 504):
                    raise DownloadError(f"HTTP {exc.code} al descargar") from exc
                failure = exc
            except (URLError, TimeoutError, OSError, DownloadError) as exc:
                failure = exc
            finally:
                if temporary.exists():
                    self.store.delete_owned_file(temporary)
            if attempt < self.cfg.retries:
                self.sleep(min(60, 10 * 2 ** attempt))
        raise DownloadError(f"Descarga fallida tras reintentos: {failure}")
