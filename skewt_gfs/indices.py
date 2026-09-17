"""Explicit parcel definitions and virtual-temperature CAPE/CIN (MetPy 1.7.1)."""
from __future__ import annotations

import math


def calculate(profile, step_hpa=1.0):
    import numpy as np
    import metpy.calc as calc
    from metpy.units import units

    rows = profile["rows"]
    p0 = np.array([r["pressure_hpa"] for r in rows])
    z0 = np.array([r["altitude_m"] for r in rows])
    terrain = profile["model_terrain_m"]
    if z0[-1] < terrain + 3000 or z0[0] > terrain + 500:
        raise ValueError("Cobertura insuficiente para parcelas MU 0–3 km y ML 0–500 m")
    # Include the actual layer boundaries, native levels and a numerical integration mesh.
    # Interpolation adds no observations or model resolution.
    layer_p = np.exp(np.interp([terrain + 500, terrain + 3000], z0, np.log(p0)))
    pv = np.unique(np.r_[p0, layer_p, np.arange(p0[-1], p0[0], step_hpa)])[::-1]

    def interpolate(values, pressures=pv):
        return np.interp(np.log(pressures), np.log(p0[::-1]), np.asarray(values)[::-1])

    p = pv * units.hPa
    t = units.Quantity(interpolate([r["temperature_c"] for r in rows]), "degC")
    td = units.Quantity(interpolate([r["dewpoint_c"] for r in rows]), "degC")

    def height(pressure):
        if pressure is None or not p0[-1] <= pressure <= p0[0]:
            return None  # Never extrapolate an LCL/EL height outside the sounding.
        return float(interpolate(z0, [pressure])[0] - terrain)

    def scalar(value, unit):
        value = float(value.to(unit).magnitude)
        return value if math.isfinite(value) else None

    def evaluate(pp, tt, dd, definition):
        pp, tt, dd, parcel = calc.parcel_profile_with_lcl(pp, tt, dd)
        cape, cin = calc.cape_cin(pp, tt, dd, parcel, which_lfc="bottom", which_el="top")
        lcl_p, lcl_t = calc.lcl(pp[0], tt[0], dd[0])
        # Use the SAME virtual-temperature profiles and crossing selection as cape_cin.
        mixing = np.where(pp > lcl_p, calc.saturation_mixing_ratio(pp[0], dd[0]),
                          calc.saturation_mixing_ratio(pp, parcel))
        tv = calc.virtual_temperature_from_dewpoint(pp, tt, dd)
        parcel_tv = calc.virtual_temperature(parcel, mixing)
        lfc_p, _ = calc.lfc(pp, tv, dd, parcel_temperature_profile=parcel_tv, which="bottom")
        el_p, _ = calc.el(pp, tv, dd, parcel_temperature_profile=parcel_tv, which="top")
        lfc, el = scalar(lfc_p, "hPa"), scalar(el_p, "hPa")
        cape_value, raw_cin = scalar(cape, "J/kg"), scalar(cin, "J/kg")
        lcl = scalar(lcl_p, "hPa")
        if cape_value is None or raw_cin is None or lcl is None:
            raise ValueError("Índices no finitos; perfil rechazado")
        result = {
            "definition": definition, "CAPE_J_kg": cape_value,
            "CIN_J_kg": raw_cin if lfc is not None else None,
            "CIN_metpy_J_kg": raw_cin,
            "status": "no_lfc" if lfc is None else ("el_above_profile" if el is None else "ok"),
            "start_pressure_hPa": scalar(pp[0], "hPa"),
            "start_temperature_C": scalar(tt[0], "degC"),
            "start_dewpoint_C": scalar(dd[0], "degC"),
            "LCL_hPa": lcl, "LCL_m_AGL": height(lcl),
            "LCL_temperature_C": scalar(lcl_t, "degC"),
            "LFC_hPa": lfc, "LFC_m_AGL": height(lfc),
            "EL_hPa": el, "EL_m_AGL": height(el),
            "integration_top_hPa": float(pp[-1].magnitude),
        }
        trace = {"pressure_hpa": pp.magnitude.tolist(),
                 "temperature_c": parcel.to("degC").magnitude.tolist()}
        return result, trace

    sb, trace = evaluate(p, t, td, "SB: presión superficial y T/Td a 2 m del modelo")
    # Same search layer as thundeR: maximum equivalent potential temperature below 3 km.
    candidates = pv >= layer_p[1]
    theta_e = calc.equivalent_potential_temperature(p[candidates], t[candidates], td[candidates])
    mu_index = int(np.argmax(theta_e.magnitude))
    mu, _ = evaluate(p[mu_index:], t[mu_index:], td[mu_index:],
                     "MU: máxima theta-e entre superficie y 3000 m AGL del modelo")
    # MetPy mixes potential temperature and mixing ratio, weighted by pressure.
    # Convert the 500 m layer to its ACTUAL model pressure depth, not a fixed 100 hPa.
    depth = (pv[0] - layer_p[0]) * units.hPa
    ml_p, ml_t, ml_td = calc.mixed_parcel(p, t, td, depth=depth)
    above = pv < layer_p[0]
    ml, _ = evaluate(np.r_[ml_p.magnitude, pv[above]] * units.hPa,
                     units.Quantity(np.r_[ml_t.to("degC").magnitude, t[above].magnitude], "degC"),
                     units.Quantity(np.r_[ml_td.to("degC").magnitude, td[above].magnitude], "degC"),
                     "ML: theta y mezcla de vapor medias, ponderadas por presión, hasta 500 m AGL")
    profile["parcels"] = {"SB": sb, "MU": mu, "ML": ml}
    profile["parcel_trace"] = trace
    profile["indices"] = {
        **{f"{name}_{key}": result[key] for name, result in profile["parcels"].items()
           for key in ("CAPE_J_kg", "CIN_J_kg")},
        "LCL_hPa": sb["LCL_hPa"], "LCL_temperature_C": sb["LCL_temperature_C"],
        "LCL_m_AGL": sb["LCL_m_AGL"], "parcel": sb["definition"],
        "integration_top_hPa": rows[-1]["pressure_hpa"],
        "method": "MetPy 1.7.1; temperatura virtual; LFC inferior y EL superior",
        "numerical_interpolation": f"T/Td lineales en log(p), paso máximo {step_hpa:g} hPa; no aumenta resolución GFS",
        "CIN_convention": "null sin LFC; cero convencional de MetPy conservado en parcels.*.CIN_metpy_J_kg",
        "height_reference": "HGT interpolada en log(p), menos terreno GFS; no altura de estación",
    }
    for row in rows:
        row["parcel_temperature_c"] = float(np.interp(
            np.log(row["pressure_hpa"]), np.log(trace["pressure_hpa"][::-1]),
            trace["temperature_c"][::-1]))
    return profile
