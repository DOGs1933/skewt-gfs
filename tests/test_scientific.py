"""Dependency integration tests; automatically run by the Linux installer."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from skewt_gfs.config import load_config
from skewt_gfs.demo import synthetic_grid
from skewt_gfs.planning import parse_time
from skewt_gfs.profile import extract_profile, compute_indices
from skewt_gfs.render import png_plot
from skewt_gfs.grib import read_grib

AVAILABLE = all(importlib.util.find_spec(name) for name in ("metpy", "eccodes", "matplotlib"))


@unittest.skipUnless(AVAILABLE, "MetPy/ecCodes/matplotlib no instalados")
class ScientificIntegration(unittest.TestCase):
    def test_indices_and_png_for_all_sites(self):
        cfg = load_config(Path(__file__).resolve().parents[1] / "config.toml")
        cycle = parse_time("2026-09-16T00:00:00Z")
        grid = synthetic_grid(cfg, cycle, cycle)
        with tempfile.TemporaryDirectory() as tmp:
            for station in cfg.stations:
                profile = compute_indices(extract_profile(grid, station, cfg))
                self.assertGreaterEqual(profile["indices"]["SB_CAPE_J_kg"], 0)
                self.assertLessEqual(profile["indices"]["LCL_hPa"], profile["rows"][0]["pressure_hpa"])
                path = Path(tmp) / f"{station.id}.png"
                png_plot(profile, path, cfg)
                self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_eccodes_roundtrip_fields_times_and_units(self):
        import eccodes as ec
        cfg = load_config(Path(__file__).resolve().parents[1] / "config.toml")
        cycle = parse_time("2026-09-16T00:00:00Z")
        params = [("temperature_k",0,0), ("height_m",3,5), ("u_ms",2,2), ("v_ms",2,3), ("rh_percent",1,1)]
        fields = [(name,cat,num,"isobaricInhPa",p) for p in cfg.levels for name,cat,num in params]
        fields += [("surface_pressure_pa",3,0,"surface",0), ("terrain_m",3,5,"surface",0),
                   ("surface_temperature_k",0,0,"heightAboveGround",2), ("surface_dewpoint_k",0,6,"heightAboveGround",2),
                   ("surface_u_ms",2,2,"heightAboveGround",10), ("surface_v_ms",2,3,"heightAboveGround",10)]
        fields += [(name,7,num,"surface" if depth == 0 else "pressureFromGroundLayer",depth*100)
                   for name,num in (("gfs_cape",6),("gfs_cin",7)) for depth in (0,90,180,255)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.grib2"
            with path.open("wb") as stream:
                for name,cat,num,kind,level in fields:
                    handle = ec.codes_grib_new_from_samples("regular_ll_sfc_grib2")
                    try:
                        for key,value in {"centre":7,"discipline":0,"parameterCategory":cat,"parameterNumber":num,
                                          "typeOfLevel":kind,"level":level,"dataDate":20260916,"dataTime":0,
                                          "stepType":"instant","stepUnits":1,"forecastTime":6,
                                          "Ni":2,"Nj":2,"latitudeOfFirstGridPointInDegrees":-16,
                                          "latitudeOfLastGridPointInDegrees":-16.25,"longitudeOfFirstGridPointInDegrees":292,
                                          "longitudeOfLastGridPointInDegrees":292.25,"iDirectionIncrementInDegrees":.25,
                                          "jDirectionIncrementInDegrees":.25,"uvRelativeToGrid":0}.items():
                            ec.codes_set(handle,key,value)
                        if kind == "pressureFromGroundLayer":
                            ec.codes_set(handle,"topLevel",level)
                            ec.codes_set(handle,"bottomLevel",0)
                        ec.codes_set_values(handle,[9999.,301.,302.,303.] if name == "height_m" else [300.,301.,302.,303.])
                        ec.codes_write(handle,stream)
                    finally:
                        ec.codes_release(handle)
            grid = read_grib(path,cycle,6,cfg.levels)
            self.assertEqual(len(grid.fields),len(fields))
            self.assertEqual(grid.longitudes,[-68.,-67.75,-68.,-67.75])
            self.assertEqual(grid.fields[("height_m",500)],[9999.,301.,302.,303.])
            self.assertIn(("gfs_cape",180), grid.fields)
            self.assertEqual(grid.field_metadata[("gfs_cin",90)]["top_offset_hPa"],90)
            with self.assertRaisesRegex(ValueError,"hora válida"):
                read_grib(path,cycle,7,cfg.levels)
            for case in ("missing_diagnostic", "wrong_secondary_time"):
                bad = Path(tmp) / (case + ".grib2")
                with path.open("rb") as source, bad.open("wb") as target:
                    while (handle := ec.codes_grib_new_from_file(source)) is not None:
                        try:
                            if case == "missing_diagnostic" and ec.codes_get(handle,"parameterCategory") == 7:
                                continue
                            if case == "wrong_secondary_time" and ec.codes_get(handle,"typeOfLevel") == "isobaricInhPa" and ec.codes_get(handle,"level") == 675:
                                ec.codes_set(handle,"forecastTime",7)
                            ec.codes_write(handle,target)
                        finally:
                            ec.codes_release(handle)
                # ecCodes/cffi retains a C FILE wrapper until the Python object dies.
                # Drop test-loop references before Windows removes the temporary files.
                del source, target
                with self.assertRaisesRegex(ValueError,"Faltan campos|hora válida"):
                    read_grib(bad,cycle,6,cfg.levels)
