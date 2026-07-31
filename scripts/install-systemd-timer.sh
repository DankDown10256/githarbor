#!/usr/bin/env bash

set -euo pipefail

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemd is not available on this system."
  exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as the user who owns the GitHarbor installation, not as root."
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
flask_path="$(command -v flask || true)"
service_user="$(id -un)"
service_group="$(id -gn)"
unit_dir="/etc/systemd/system"
temporary_dir="$(mktemp -d)"

trap 'rm -rf "$temporary_dir"' EXIT

if [[ -z "$flask_path" ]]; then
  echo "Flask was not found. Run this script from the project's nix-shell."
  exit 1
fi

if [[ ! -f "$project_dir/.env" ]]; then
  echo "Missing .env file. Run ./scripts/installation.sh first."
  exit 1
fi

if [[ "$project_dir" =~ [[:space:]] ]]; then
  echo "The project path cannot contain spaces when generating the systemd unit."
  exit 1
fi

service_file="$temporary_dir/githarbor-backup.service"
timer_file="$temporary_dir/githarbor-backup.timer"

printf '%s\n' \
  '[Unit]' \
  'Description=GitHarbor repository backups' \
  'Wants=network-online.target' \
  'After=network-online.target' \
  '' \
  '[Service]' \
  'Type=oneshot' \
  "User=$service_user" \
  "Group=$service_group" \
  "WorkingDirectory=$project_dir" \
  "EnvironmentFile=$project_dir/.env" \
  "ExecStart=$flask_path --app app run_backups" \
  > "$service_file"

printf '%s\n' \
  '[Unit]' \
  'Description=Run GitHarbor backups every day' \
  '' \
  '[Timer]' \
  'OnCalendar=*-*-* 03:00:00' \
  'Persistent=true' \
  'Unit=githarbor-backup.service' \
  '' \
  '[Install]' \
  'WantedBy=timers.target' \
  > "$timer_file"

sudo install -m 0644 "$service_file" "$unit_dir/githarbor-backup.service"
sudo install -m 0644 "$timer_file" "$unit_dir/githarbor-backup.timer"
sudo systemctl daemon-reload
sudo systemctl enable --now githarbor-backup.timer

echo "GitHarbor backup timer installed."
echo "Test it with: sudo systemctl start githarbor-backup.service"
echo "View logs with: journalctl -u githarbor-backup.service"
