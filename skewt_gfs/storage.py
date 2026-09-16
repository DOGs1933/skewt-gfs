from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def sha256(path: Path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class AlreadyRunning(RuntimeError):
    pass


class Lock:
    """Kernel lock released even if a process crashes; never unlink a live lock file."""
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise AlreadyRunning("Ya hay una ejecución activa") from exc
        return self

    def __exit__(self, *args):
        if self.stream:
            self.stream.close()


class Store:
    MARKER = ".skewt-gfs-data-v1"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / self.MARKER
        if not marker.exists():
            if any(self.root.iterdir()):
                raise ValueError("La carpeta de datos ya contiene archivos ajenos. Seleccione una carpeta vacía.")
            marker.write_text("Datos administrados por skewt_gfs\n", encoding="utf-8")
        for name in ("raw", "runs", "staging", "logs"):
            path = self.root / name
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise ValueError("No se admiten enlaces simbólicos en carpetas administradas")
            path.mkdir(exist_ok=True)

    def delete_owned_file(self, path: Path):
        if path.is_symlink() or not path.resolve().is_relative_to(self.root) or not (self.root / self.MARKER).is_file():
            raise ValueError("Borrado rechazado: ruta fuera de la carpeta administrada")
        if path.is_file():
            path.unlink()

    def cleanup(self, raw_days, image_days, failed_days, now=None):
        now = now or datetime.now(timezone.utc)
        result = {"raw_files": 0, "images": 0, "failed_files": 0}
        latest = None
        if (self.root / "latest.json").exists():
            latest = json.loads((self.root / "latest.json").read_text(encoding="utf-8"))["run_id"]
        # Only generated filenames within dedicated directories, never shell globs in cwd.
        for path in (self.root / "raw").rglob("*"):
            if path.is_file() and path.suffix in (".grib2", ".json", ".part"):
                if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < now - timedelta(days=raw_days):
                    self.delete_owned_file(path)
                    result["raw_files"] += 1
        for run in (self.root / "runs").iterdir():
            manifest_path = run / "manifest.json"
            if run.name == latest or run.is_symlink() or not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            created = datetime.fromisoformat(manifest["created_utc"].replace("Z", "+00:00"))
            if created < now - timedelta(days=image_days):
                for path in run.iterdir():
                    if path.suffix in (".png", ".svg", ".html"):
                        self.delete_owned_file(path)
                        result["images"] += 1
                manifest["images_expired"] = True
                atomic_json(manifest_path, manifest)
        for path in (self.root / "staging").rglob("*"):
            if path.is_file() and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < now - timedelta(days=failed_days):
                self.delete_owned_file(path)
                result["failed_files"] += 1
        return result
