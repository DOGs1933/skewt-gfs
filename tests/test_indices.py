"""Scientific regressions: no network, no blanket conversion of errors to zero."""
import copy
import importlib.util
import json
import math
import unittest
from pathlib import Path

from skewt_gfs.indices import calculate
from skewt_gfs.render import index_lines


def sounding(inversion=False):
    rows = []
    for h in range(0, 16001, 250):
        t = max(-65., 30 - 7.5 * h / 1000)
        td = t - 6
        if inversion and h < 1000:
            t = 24 + 3 * h / 1000
            td = 20 - 2 * h / 1000
        rows.append(dict(pressure_hpa=1000 * math.exp(-h / 8000), altitude_m=h + 2,
                         temperature_c=t, dewpoint_c=td))
    return dict(rows=rows, model_terrain_m=0)


@unittest.skipUnless(importlib.util.find_spec("metpy"), "MetPy no instalado")
class ParcelTests(unittest.TestCase):
    def test_unstable_and_inhibited_profiles(self):
        for inversion in (False, True):
            result = calculate(sounding(inversion))
            self.assertGreater(result["indices"]["SB_CAPE_J_kg"], 500)
            self.assertLessEqual(result["indices"]["SB_CIN_J_kg"], 0)
            if inversion:
                self.assertLess(result["indices"]["SB_CIN_J_kg"], -10)
            for parcel in result["parcels"].values():
                self.assertGreater(parcel["LCL_m_AGL"], 0)
                self.assertLessEqual(parcel["LCL_hPa"], parcel["start_pressure_hPa"])
            json.dumps(result, allow_nan=False)

    def test_numerical_convergence(self):
        coarse = calculate(sounding(True))
        fine = calculate(sounding(True), step_hpa=.5)
        for name in ("SB", "MU", "ML"):
            for key in ("CAPE_J_kg", "CIN_J_kg"):
                expected = fine["parcels"][name][key]
                self.assertAlmostEqual(coarse["parcels"][name][key], expected,
                                       delta=max(1., abs(expected) * .01))

    def test_real_cochabamba_no_lfc_and_lcl_height(self):
        path = Path(__file__).parent / "fixtures" / "cochabamba_20260916_18_f006.json"
        result = calculate(json.loads(path.read_text(encoding="utf-8")))
        sb = result["parcels"]["SB"]
        self.assertEqual(sb["CAPE_J_kg"], 0)
        self.assertIsNone(sb["CIN_J_kg"])
        self.assertEqual(sb["CIN_metpy_J_kg"], 0)
        self.assertEqual(sb["status"], "no_lfc")
        self.assertIsNone(sb["LFC_hPa"])
        self.assertAlmostEqual(sb["LCL_hPa"], 620.7115, delta=.1)
        self.assertAlmostEqual(sb["LCL_m_AGL"], 1117.384, delta=1)
        self.assertIn("s/LFC", " ".join(index_lines(result)))
        json.dumps(result, allow_nan=False)

    def test_mountain_heights_and_layer_depth_use_model_reference(self):
        base = sounding(True)
        elevated = copy.deepcopy(base)
        elevated["model_terrain_m"] += 4000
        for row in elevated["rows"]:
            row["altitude_m"] += 4000
        a, b = calculate(base), calculate(elevated)
        for name in ("SB", "MU", "ML"):
            self.assertAlmostEqual(a["parcels"][name]["LCL_m_AGL"], b["parcels"][name]["LCL_m_AGL"])
            self.assertAlmostEqual(a["parcels"][name]["CAPE_J_kg"], b["parcels"][name]["CAPE_J_kg"])

    def test_shallow_profile_rejected(self):
        profile = sounding()
        profile["rows"] = profile["rows"][:5]
        with self.assertRaisesRegex(ValueError, "Cobertura"):
            calculate(profile)
