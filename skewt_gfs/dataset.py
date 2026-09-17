"""GFS pressure levels and diagnostic layers selected from the NOAA inventories."""

SECONDARY_LEVELS = frozenset(range(125, 900, 50))
DIAGNOSTIC_LAYERS = (0, 90, 180, 255)  # 0 = surface; other values are Pa/100 above ground.


def products(cfg):
    return ("primary", "secondary") if any(p in SECONDARY_LEVELS for p in cfg.levels) else ("primary",)
