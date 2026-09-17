import io
import json
import math
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from skewt_gfs.config import load_config, Station
from skewt_gfs.demo import synthetic_grid
from skewt_gfs.download import Downloader, DownloadError, NotAvailable, build_url, validate_grib
from skewt_gfs.planning import parse_time, floor_cycle, target_hours, forecast_hours, iso
from skewt_gfs.profile import extract_profile, wind_direction, dewpoint, select_nodes
from skewt_gfs.runner import produce, run
from skewt_gfs.storage import Store, Lock, AlreadyRunning, atomic_json

CONFIG = Path(__file__).resolve().parents[1] / "config.toml"
NOW = parse_time("2026-09-15T23:45:00Z")
CYCLE = floor_cycle(NOW)


def message():
    return b"GRIB" + bytes([0, 0, 0, 2]) + (20).to_bytes(8, "big") + b"7777"


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.cfg = replace(load_config(CONFIG), root=self.base / "data", png=False)

    def test_targets_cross_midnight(self):
        targets = target_hours(NOW, 6, 1)
        self.assertEqual(iso(targets[0]), "2026-09-16T00:00:00Z")
        self.assertEqual(forecast_hours(CYCLE, targets), list(range(6, 13)))

    def test_offset_and_naive_time(self):
        self.assertEqual(parse_time("2026-09-15T19:45:00-04:00"), NOW)
        with self.assertRaises(ValueError):
            parse_time("2026-09-15T19:45:00")
        with self.assertRaises(ValueError):
            forecast_hours(CYCLE, [CYCLE + timedelta(hours=121)])

    def test_url_contains_bounded_region_and_required_fields(self):
        params = parse_qs(urlparse(build_url(self.cfg, CYCLE, 12)).query)
        self.assertEqual(params["file"], ["gfs.t18z.pgrb2.0p25.f012"])
        self.assertEqual(params["dir"], ["/gfs.20260915/18/atmos"])
        self.assertEqual(params["lev_2_m_above_ground"], ["on"])
        self.assertEqual(params["var_PRES"], ["on"])
        self.assertLess(float(params["rightlon"][0])-float(params["leftlon"][0]), 10)
        self.assertEqual(params["var_CAPE"], ["on"])
        self.assertEqual(params["var_CIN"], ["on"])
        self.assertEqual(params["lev_255-0_mb_above_ground"], ["on"])
        self.assertNotIn("lev_675_mb", params)
        secondary = parse_qs(urlparse(build_url(self.cfg, CYCLE, 12, "secondary")).query)
        self.assertEqual(secondary["file"], ["gfs.t18z.pgrb2b.0p25.f012"])
        self.assertEqual(secondary["lev_675_mb"], ["on"])
        self.assertNotIn("lev_700_mb", secondary)
        self.assertNotIn("lev_surface", secondary)

    def test_secondary_levels_enabled_for_existing_configuration(self):
        self.assertEqual(len(self.cfg.levels),39)
        self.assertEqual(self.cfg.levels[:37], list(range(1000,99,-25)))
        target = self.base / "without-secondary.toml"
        target.write_text(CONFIG.read_text(encoding="utf-8").replace("[forecast]", "[forecast]\ninclude_secondary_levels = false"), encoding="utf-8")
        self.assertEqual(len(load_config(target).levels),23)

    def test_secondary_unavailable_prevents_readiness(self):
        requests=[]
        def opener(request, timeout):
            requests.append(request.full_url)
            if "pgrb2b" in request.full_url:
                raise HTTPError(request.full_url,404,"not ready",{},None)
            return io.BytesIO(f"1:0:d={CYCLE:%Y%m%d%H}:TMP:".encode())
        d=Downloader(self.cfg,Store(self.cfg.root),opener=opener,sleeper=lambda _:None)
        self.assertFalse(d.available(CYCLE,6))
        self.assertEqual(len(requests),2)

    def test_secondary_failure_does_not_assemble_partial_profile(self):
        store=Store(self.cfg.root)
        requests=[]
        def opener(request,timeout):
            requests.append(request.full_url)
            if "pgrb2b" in request.full_url:
                raise HTTPError(request.full_url,404,"not ready",{},None)
            return io.BytesIO(message())
        d=Downloader(self.cfg,store,opener=opener,sleeper=lambda _:None)
        with self.assertRaises(NotAvailable):
            d.download(CYCLE,6)
        self.assertFalse(list(store.root.rglob("profile_*.grib2")))
        self.assertFalse(list(store.root.rglob("*.part")))
        self.assertEqual(len(list(store.root.rglob("gfs_f006.grib2"))),1)

    def test_native_diagnostics_use_same_nodes_and_keep_layers(self):
        grid=synthetic_grid(self.cfg,CYCLE,CYCLE)
        for depth in (0,90,180,255):
            grid.fields["gfs_cape",depth]=[float(i+depth) for i in range(len(grid.latitudes))]
            grid.fields["gfs_cin",depth]=[-float(i+depth) for i in range(len(grid.latitudes))]
        cfg=replace(self.cfg,extraction="bilinear")
        station=cfg.stations[1]
        nodes,_=select_nodes(grid,station,cfg.extraction,50)
        profile=extract_profile(grid,station,cfg)
        expected=sum((i+180)*w for i,w in nodes)
        self.assertAlmostEqual(profile["gfs_diagnostics"]["layer_180hPa"]["CAPE_J_kg"],expected)
        self.assertAlmostEqual(profile["gfs_diagnostics"]["layer_180hPa"]["CIN_J_kg"],-expected)
        grid.fields["gfs_cape",0][nodes[0][0]]=math.nan
        profile=extract_profile(grid,station,cfg)
        self.assertIsNone(profile["gfs_diagnostics"]["surface"]["CAPE_J_kg"])
        self.assertTrue(any("gfs_cape" in w for w in profile["warnings"]))
        grid.fields["gfs_cin",180]=[.42]*len(grid.latitudes)
        grid.field_metadata["gfs_cin",180]={"packing_error_J_kg":.5}
        profile=extract_profile(grid,station,cfg)
        self.assertAlmostEqual(profile["gfs_diagnostics"]["layer_180hPa"]["CIN_J_kg"],.42)

    def test_config_rejects_typo_and_duplicate_station(self):
        text = CONFIG.read_text(encoding="utf-8")
        target = self.base / "config.toml"
        target.write_text(text.replace("poll_seconds", "poll_second"), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_config(target)
        cfg = load_config(CONFIG)
        self.assertEqual(len(cfg.stations), 5)
        self.assertTrue(any(abs(s.lon+63.13528)<.0001 for s in cfg.stations))

    def test_wind_convention_and_calm(self):
        self.assertEqual([wind_direction(*v) for v in [(0,-10), (-10,0), (0,10), (10,0)]], [0,90,180,270])
        self.assertIsNone(wind_direction(0,0))

    def test_dewpoint_reference(self):
        self.assertAlmostEqual(dewpoint(20,50), 9.2611, places=3)
        self.assertAlmostEqual(dewpoint(-20,100), -20)
        with self.assertRaises(ValueError):
            dewpoint(20,0)

    def test_mountain_surface_filter_and_height_units(self):
        grid = synthetic_grid(self.cfg, CYCLE, NOW.replace(minute=0))
        profile = extract_profile(grid, self.cfg.stations[0], self.cfg)
        self.assertGreater(profile["model_terrain_m"], 3000)
        self.assertTrue(all(r["pressure_hpa"] < profile["rows"][0]["pressure_hpa"] for r in profile["rows"][1:]))
        self.assertGreater(profile["rows"][-1]["altitude_m"], 15000)
        self.assertTrue(any(d["reason"] == "below_model_surface" for d in profile["dropped_levels"]))

    def test_no_replacement_with_distant_valid_nodes(self):
        grid = synthetic_grid(self.cfg, CYCLE, CYCLE)
        station = self.cfg.stations[1]
        nodes, _ = select_nodes(grid, station, "nearest", 50)
        index = nodes[0][0]
        grid.fields[("surface_temperature_k",0)][index] = math.nan
        with self.assertRaisesRegex(ValueError, "nodos fijos"):
            extract_profile(grid, station, self.cfg)

    def test_missing_upper_coverage_is_rejected(self):
        grid = synthetic_grid(self.cfg, CYCLE, CYCLE)
        for p in self.cfg.levels:
            if p <= 300:
                grid.fields[("temperature_k",p)] = [math.nan]*len(grid.latitudes)
        with self.assertRaisesRegex(ValueError, "cobertura"):
            extract_profile(grid, self.cfg.stations[1], self.cfg)

    def test_bilinear_weights_fixed_for_all_fields(self):
        grid = synthetic_grid(self.cfg, CYCLE, CYCLE)
        station = self.cfg.stations[1]
        nodes, meta = select_nodes(grid, station, "bilinear", 50)
        self.assertEqual(len(nodes), 4)
        self.assertAlmostEqual(sum(w for _,w in nodes), 1)
        profile = extract_profile(grid, station, replace(self.cfg, extraction="bilinear"))
        self.assertEqual(profile["nodes"], meta)

    def test_grib_truncation_and_html_rejected(self):
        path = self.base / "sample.grib2"
        path.write_bytes(message()*2)
        self.assertEqual(validate_grib(path), 2)
        for content in (message()[:-1], b"<html>error</html>", b"", message()+b"garbage"):
            path.write_bytes(content)
            with self.assertRaises(DownloadError):
                validate_grib(path)

    def test_store_refuses_foreign_folder_and_external_delete(self):
        foreign = self.base / "foreign"
        foreign.mkdir()
        keep = foreign / "keep.txt"
        keep.write_text("keep")
        with self.assertRaises(ValueError):
            Store(foreign)
        store = Store(self.cfg.root)
        with self.assertRaises(ValueError):
            store.delete_owned_file(keep)
        self.assertTrue(keep.exists())

    def test_concurrent_lock_rejected(self):
        with Lock(self.base / "lock"):
            with self.assertRaises(AlreadyRunning):
                with Lock(self.base / "lock"):
                    pass

    def test_cache_reuse_and_corruption_redownload(self):
        store = Store(self.cfg.root)
        calls, pauses = [], []
        def opener(request, timeout):
            calls.append(request.full_url)
            return io.BytesIO(message())
        downloader = Downloader(self.cfg, store, opener=opener, sleeper=pauses.append)
        path, meta = downloader.download(CYCLE, 6)
        downloader.download(CYCLE, 6)
        self.assertEqual(len(calls), 2)
        path.write_bytes(b"broken")
        downloader.download(CYCLE, 6)
        self.assertEqual(len(calls), 2)
        self.assertEqual(validate_grib(path), 2, "Rebuild damaged assembly from checked source files")
        component = next(store.root.rglob("gfs_f006.grib2"))
        component.write_bytes(b"broken source")
        downloader.download(CYCLE, 6)
        self.assertEqual(len(calls), 3)
        self.assertGreater(pauses[0], 9)

    def test_404_and_permission_error_distinguished(self):
        store = Store(self.cfg.root)
        def unavailable(*args, **kwargs):
            raise HTTPError("https://example", 404, "missing", {}, None)
        d = Downloader(self.cfg, store, opener=unavailable)
        self.assertFalse(d.available(CYCLE, 6))
        def forbidden(*args, **kwargs):
            raise HTTPError("https://example", 403, "forbidden", {}, None)
        d = Downloader(self.cfg, store, opener=forbidden)
        with self.assertRaisesRegex(DownloadError, "HTTP 403"):
            d.available(CYCLE, 6)

    def test_bad_download_cleans_partial(self):
        store = Store(self.cfg.root)
        d = Downloader(replace(self.cfg, retries=0), store, opener=lambda *a, **k: io.BytesIO(b"HTML error"))
        with self.assertRaises(DownloadError):
            d.download(CYCLE, 6)
        self.assertEqual(list(store.root.rglob("*.part")), [])
        self.assertEqual(list(store.root.rglob("*.grib2")), [])

    def test_complete_demo_publication_and_retention(self):
        store = Store(self.cfg.root)
        targets = target_hours(NOW, 1, 1)
        result = produce(self.cfg, store, CYCLE, targets, synthetic_grid, demo=True)
        self.assertEqual(len(result["products"]), 10)
        self.assertTrue((store.root / "index.html").exists())
        folder = store.root / "runs" / result["run_id"]
        self.assertEqual(len(list(folder.glob("*.csv"))), 10)
        self.assertTrue(result["synthetic"])
        store.cleanup(1,1,1, datetime.now(timezone.utc)+timedelta(days=100))
        self.assertEqual(len(list(folder.glob("*.svg"))), 10, "Latest must remain visible")

    def test_failure_does_not_publish_partial_batch(self):
        store = Store(self.cfg.root)
        targets = target_hours(NOW, 1, 1)
        def broken(cfg, cycle, valid):
            if valid == targets[1]:
                raise ValueError("injected failure")
            return synthetic_grid(cfg, cycle, valid)
        with self.assertRaisesRegex(ValueError, "injected"):
            produce(self.cfg, store, CYCLE, targets, broken, demo=True)
        self.assertFalse((store.root / "latest.json").exists())
        self.assertFalse((store.root / "index.html").exists())
        self.assertEqual(len(list((store.root / "staging").rglob("failure.json"))), 1)

    def test_existing_cycle_is_idempotent(self):
        store = Store(self.cfg.root)
        atomic_json(store.root / "latest.json", {"cycle_utc": iso(CYCLE), "last_valid_utc": iso(NOW+timedelta(hours=2)),
            "config_fingerprint": self.cfg.fingerprint, "run_id": "previous"})
        with patch("skewt_gfs.runner.Downloader.available", side_effect=AssertionError("unexpected request")):
            self.assertEqual(run(self.cfg, now=NOW)["status"], "unchanged")

    def test_historical_batch_does_not_replace_latest(self):
        store = Store(self.cfg.root)
        atomic_json(store.root / "latest.json", {"cycle_utc": iso(CYCLE+timedelta(hours=24)),
            "last_valid_utc": iso(NOW+timedelta(hours=30)), "run_id": "future"})
        produce(self.cfg, store, CYCLE, target_hours(NOW, 1, 1), synthetic_grid, demo=True)
        self.assertEqual(json.loads((store.root / "latest.json").read_text())["run_id"], "future")

    def test_live_pipeline_carries_source_metadata(self):
        metadata = {"url": "https://example.test/source.grib2", "bytes": 20, "sha256": "fixture"}
        with patch("skewt_gfs.runner.Downloader.available", return_value=True), \
             patch("skewt_gfs.runner.Downloader.download", return_value=(self.base / "fixture", metadata)), \
             patch("skewt_gfs.runner.read_grib", side_effect=lambda path,cycle,hour,levels: synthetic_grid(self.cfg,cycle,cycle+timedelta(hours=hour))), \
             patch("skewt_gfs.runner.compute_indices", side_effect=lambda profile: profile):
            result = run(self.cfg, now=NOW)
        self.assertEqual(result["status"], "complete")
        self.assertFalse(result["synthetic"])
        self.assertEqual(len(result["products"]), 35)
        profile = json.loads((self.cfg.root / "runs" / result["run_id"] / (result["products"][0]["stem"]+".json")).read_text(encoding="utf-8"))
        self.assertEqual(profile["input"], metadata)

    def test_new_cycle_not_ready_keeps_existing_batch(self):
        store = Store(self.cfg.root)
        earlier = CYCLE-timedelta(hours=6)
        atomic_json(store.root / "latest.json", {"cycle_utc": iso(earlier), "last_valid_utc": iso(NOW+timedelta(hours=2)),
            "config_fingerprint": self.cfg.fingerprint, "run_id": "previous"})
        with patch("skewt_gfs.runner.Downloader.available", return_value=False) as probe:
            self.assertEqual(run(self.cfg, now=NOW)["status"], "unchanged")
        self.assertEqual(probe.call_count, 1)


if __name__ == "__main__":
    unittest.main()
