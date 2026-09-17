from __future__ import annotations

import argparse
import importlib
import json
import logging
import signal
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from .config import load_config
from .download import build_url
from .dataset import products
from .planning import candidate_cycles, forecast_hours, target_hours, parse_time, iso
from .runner import run, versions
from .storage import Store, AlreadyRunning


def doctor(cfg):
    problems = []
    for module in ("numpy", "eccodes", "metpy.calc", "matplotlib"):
        try:
            imported = importlib.import_module(module)
            if module == "eccodes":
                imported.codes_get_api_version()
        except Exception as exc:
            problems.append(f"{module}: {exc}")
    print(json.dumps({"versions": versions(), "problems": problems, "stations": len(cfg.stations), "data": str(cfg.root)}, indent=2))
    return 1 if problems else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Perfiles GFS regionales y Skew-T para Linux")
    parser.add_argument("--config", default="config.toml", help="Ruta del archivo de configuración")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "doctor", "demo", "watch", "status"):
        sub.add_parser(name)
    once = sub.add_parser("run")
    once.add_argument("--cycle", help="Ciclo explícito, por ejemplo 2026-09-16T06:00:00Z")
    once.add_argument("--at", help="Inicio de validez ISO con zona; por defecto hora actual")
    once.add_argument("--force", action="store_true", help="Crear otro lote aunque ya exista ese ciclo")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.command == "doctor":
        return doctor(cfg)
    if args.command == "plan":
        now = datetime.now(timezone.utc)
        targets = target_hours(now, cfg.horizon_hours, cfg.step_hours)
        candidates = list(candidate_cycles(now, cfg.max_cycle_age_hours))
        print(json.dumps({"note": "Plan sin descargar. La disponibilidad se comprueba al ejecutar.", "bounds": cfg.bounds,
            "valid_times": list(map(iso, targets)), "stations": [vars(s) for s in cfg.stations],
            "products": len(targets)*len(cfg.stations), "cycles": list(map(iso, candidates)),
            "levels": cfg.levels,
            "example_urls": [build_url(cfg, candidates[0], h, product) for h in forecast_hours(candidates[0], targets) for product in products(cfg)]}, indent=2))
        return 0
    if args.command == "demo":
        from .demo import demo
        result = demo(cfg)
        print(f"Demostración sintética: {cfg.source.parent / 'demo' / 'index.html'} ({len(result['products'])} perfiles)")
        return 0
    if args.command == "status":
        path = cfg.root / "status.json"
        print(path.read_text(encoding="utf-8") if path.exists() else "Todavía no hay ejecuciones reales.")
        return 0
    store = Store(cfg.root)
    handler = RotatingFileHandler(store.root / "logs" / "application.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.StreamHandler(), handler])
    if doctor(cfg):
        return 1
    if args.command == "run":
        cycle = parse_time(args.cycle) if args.cycle else None
        if cycle and (cycle.hour % 6 or cycle.minute or cycle.second or cycle.microsecond):
            raise ValueError("El ciclo debe ser 00, 06, 12 o 18 UTC, sin minutos/segundos")
        result = run(cfg, now=parse_time(args.at) if args.at else None, cycle=cycle, force=args.force)
        print(json.dumps({k: result[k] for k in ("status", "run_id", "elapsed_seconds", "transferred_bytes") if k in result}, indent=2))
        return 0
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        try:
            result = run(cfg)
            logging.info("Estado: %s", result["status"])
        except AlreadyRunning:
            logging.info("Ya existe una ejecución; se esperará al siguiente intervalo")
        except Exception:
            logging.exception("La ejecución falló; se mantiene el último lote publicado")
        stop.wait(cfg.poll_seconds)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(0)
    except Exception as exc:
        logging.exception("Error: %s", exc)
        sys.exit(1)
