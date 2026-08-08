#!/usr/bin/env bash
set -euo pipefail

repo=https://github.com/Evander-8/Arena-Hero-Conservative.git
app=/opt/arena-hero-conservative
service=arena-hero.service
temp_dir=
service_stopped=0

cleanup() {
  exit_code=$?
  if [[ -n "$temp_dir" && -d "$temp_dir" ]]; then
    rm -rf -- "$temp_dir"
  fi
  if [[ $service_stopped -eq 1 ]]; then
    systemctl start "$service" || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT

if [[ $EUID -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

if [[ ! -x "$app/.venv/bin/python" ]]; then
  echo "Existing virtual environment not found: $app/.venv" >&2
  exit 1
fi

if [[ -d "$app/.git" ]]; then
  systemctl stop "$service"
  service_stopped=1
  runuser -u arena-hero -- git -C "$app" pull --ff-only "$repo" main
else
  temp_dir=$(mktemp -d)
  git clone --depth 1 --branch main "$repo" "$temp_dir/source"
  systemctl stop "$service"
  service_stopped=1
  cp -a "$temp_dir/source/." "$app/"
  chown -R arena-hero:arena-hero "$app"
fi

install -m 0644 "$app/deploy/arena-hero.service" \
  "/etc/systemd/system/$service"
systemctl daemon-reload
systemctl start "$service"
service_stopped=0
systemctl status "$service" --no-pager

echo
echo "Update complete. Open the Dashboard and submit the API Key again."
