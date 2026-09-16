#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_dir"
python_bin="${PYTHON_BIN:-python3}"
"$python_bin" -c 'import sys; assert sys.version_info >= (3,11), "Se necesita Python 3.11 o posterior; recomendado 3.12"'
"$python_bin" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m skewt_gfs doctor
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip freeze > installed-versions.txt
printf '%s\n' 'Instalado. Primera ejecución real: .venv/bin/python -m skewt_gfs run' 'Después: bash scripts/install_systemd.sh'
