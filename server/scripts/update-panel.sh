#!/usr/bin/env bash
# Safely refresh the control-panel code without changing WireGuard keys,
# devices, subscriptions, /etc/hk-vpn settings, or the wg0 service.
set -euo pipefail

[ "$(id -u)" = "0" ] || { echo "Run as root"; exit 1; }

project_root="$(cd "$(dirname "$0")/../.." && pwd)"
install_dir="/opt/hk-vpn-suite"
source_dir="$project_root/server"

[ -f "$source_dir/app/main.py" ] || { echo "Run this script from an extracted HK VPN Suite source folder."; exit 1; }
python3 -m py_compile "$source_dir/app/main.py" "$source_dir/scripts/vpnctl.py"

install -d -m 755 "$install_dir/server"
cp -a "$source_dir/." "$install_dir/server/"
chmod 755 "$install_dir/server/scripts/vpnctl.py" "$install_dir/server/scripts/update-panel.sh"

install -d -m 711 /usr/local/libexec/hk-vpn
install -m 700 "$install_dir/server/scripts/vpnctl.py" /usr/local/libexec/hk-vpn/vpnctl.py
install -m 644 "$install_dir/server/systemd/hk-vpn-panel.service" /etc/systemd/system/hk-vpn-panel.service
install -m 644 "$install_dir/server/systemd/hk-vpn-reconcile.service" /etc/systemd/system/hk-vpn-reconcile.service

systemctl daemon-reload
systemctl restart hk-vpn-panel.service
if ! systemctl is-active --quiet hk-vpn-panel.service; then
  echo "Panel failed to start. Recent logs:"
  journalctl -u hk-vpn-panel.service -n 40 --no-pager || true
  exit 1
fi

echo "Panel update complete. WireGuard wg0 was not restarted and no VPN keys or device records were changed."
