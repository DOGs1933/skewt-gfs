#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# Keep unit quoting unambiguous. Other paths still work with manual execution.
if [[ "$project_dir" =~ [^a-zA-Z0-9/_.-] ]]; then
  printf '%s\n' 'Para systemd coloque el programa en una ruta sin espacios ni caracteres especiales.' >&2
  exit 1
fi
cd -- "$project_dir"
.venv/bin/python -m skewt_gfs doctor
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p -- "$unit_dir"
unit_path="$unit_dir/skewt-gfs.service"
if [[ -e "$unit_path" ]] && ! grep -Fq '# Managed by skewt_gfs installer' "$unit_path"; then
  printf '%s\n' 'Ya existe un servicio ajeno con ese nombre; no se sobrescribirá.' >&2
  exit 1
fi
cat > "$unit_path" <<EOF
# Managed by skewt_gfs installer
[Unit]
Description=Perfiles GFS y Skew-T
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$project_dir
ExecStart=$project_dir/.venv/bin/python -m skewt_gfs --config $project_dir/config.toml watch
Restart=on-failure
RestartSec=60
TimeoutStopSec=300
UMask=0022
NoNewPrivileges=true

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now skewt-gfs.service
printf '%s\n' 'Servicio activado. Para mantenerlo tras cerrar sesión, un administrador debe habilitar linger para este usuario.' 'Consultar: systemctl --user status skewt-gfs.service' 'Detener: systemctl --user disable --now skewt-gfs.service'
