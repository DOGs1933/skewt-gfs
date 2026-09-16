from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class Grid:
    latitudes: list[float]
    longitudes: list[float]
    fields: dict[tuple[str, int], list[float]]
    cycle: datetime
    valid: datetime


def read_grib(path: Path, cycle: datetime, hour: int, levels: list[int]) -> Grid:
    try:
        import eccodes as ec
    except ImportError as exc:
        raise RuntimeError("Falta ecCodes. Ejecute bash scripts/install_linux.sh para preparar el ambiente virtual.") from exc
    fields = {}
    latitudes = longitudes = None
    # WMO GRIB2 discipline 0, category/number. Avoid implementation-specific short names.
    pressure_params = {(0, 0): "temperature_k", (3, 5): "height_m", (2, 2): "u_ms", (2, 3): "v_ms", (1, 1): "rh_percent"}
    valid = cycle + timedelta(hours=hour)
    with path.open("rb") as stream:
        while (handle := ec.codes_grib_new_from_file(stream)) is not None:
            try:
                if int(ec.codes_get(handle, "discipline")) != 0:
                    continue
                param = (int(ec.codes_get(handle, "parameterCategory")), int(ec.codes_get(handle, "parameterNumber")))
                level_type = ec.codes_get(handle, "typeOfLevel")
                level = int(ec.codes_get(handle, "level"))
                key = None
                if level_type == "isobaricInhPa" and level in levels and param in pressure_params:
                    key = (pressure_params[param], level)
                elif level_type == "surface" and param == (3, 0):
                    key = ("surface_pressure_pa", 0)
                elif level_type == "surface" and param == (3, 5):
                    key = ("terrain_m", 0)
                elif level_type == "heightAboveGround" and level == 2 and param in ((0, 0), (0, 6)):
                    key = ("surface_temperature_k" if param == (0, 0) else "surface_dewpoint_k", 0)
                elif level_type == "heightAboveGround" and level == 10 and param in ((2, 2), (2, 3)):
                    key = ("surface_u_ms" if param == (2, 2) else "surface_v_ms", 0)
                if key is None:
                    continue
                if ec.codes_get(handle, "gridType") != "regular_ll":
                    raise ValueError("Se esperaba una malla geográfica regular")
                ref = datetime.strptime(f"{int(ec.codes_get(handle, 'dataDate')):08d}{int(ec.codes_get(handle, 'dataTime')):04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
                stamp = datetime.strptime(f"{int(ec.codes_get(handle, 'validityDate')):08d}{int(ec.codes_get(handle, 'validityTime')):04d}", "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
                if ref != cycle or stamp != valid or ec.codes_get(handle, "stepType") != "instant":
                    raise ValueError("GRIB contiene un ciclo, hora válida o tipo temporal inesperado")
                if param in ((2, 2), (2, 3)) and int(ec.codes_get(handle, "uvRelativeToGrid")) != 0:
                    raise ValueError("Componentes de viento relativas a la malla no admitidas")
                lats = [float(v) for v in ec.codes_get_array(handle, "latitudes")]
                lons = [((float(v) + 180) % 360) - 180 for v in ec.codes_get_array(handle, "longitudes")]
                if latitudes is None:
                    latitudes, longitudes = lats, lons
                elif lats != latitudes or lons != longitudes:
                    raise ValueError("Las variables no comparten exactamente la misma malla")
                if key in fields:
                    raise ValueError(f"Campo duplicado: {key}")
                values = [float(v) for v in ec.codes_get_values(handle)]
                # The default missingValue sentinel can also be a legitimate height
                # (e.g. 9999 m). Only the bitmap establishes a missing grid point.
                if int(ec.codes_get(handle, "bitmapPresent")):
                    bitmap = ec.codes_get_array(handle, "bitmap")
                    if len(bitmap) != len(values):
                        raise ValueError("Bitmap y valores GRIB incompatibles")
                    values = [v if present else float("nan") for v, present in zip(values, bitmap)]
                values = [float("nan") if abs(v) > 1e15 else v for v in values]
                if len(values) != len(lats):
                    raise ValueError("Longitud de campo y coordenadas incompatible")
                fields[key] = values
            finally:
                ec.codes_release(handle)
    expected = {(name, p) for name in pressure_params.values() for p in levels}
    expected |= {(name, 0) for name in ("surface_pressure_pa", "terrain_m", "surface_temperature_k", "surface_dewpoint_k", "surface_u_ms", "surface_v_ms")}
    if missing_fields := expected - fields.keys():
        raise ValueError(f"Faltan campos GRIB solicitados: {sorted(missing_fields)}")
    return Grid(latitudes, longitudes, fields, cycle, valid)
