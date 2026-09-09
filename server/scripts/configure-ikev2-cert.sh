#!/usr/bin/env bash
# Copy the Baota-managed certificate to StrongSwan's expected locations.
set -euo pipefail

domain="${1:-}"
[ -n "$domain" ] || { echo "Usage: $0 vpn.example.com"; exit 2; }
[ "$(id -u)" = "0" ] || { echo "Run as root"; exit 1; }
source_dir="/www/server/panel/vhost/cert/$domain"
[ -f "$source_dir/fullchain.pem" ] && [ -f "$source_dir/privkey.pem" ] || { echo "Baota certificate files not found in $source_dir"; exit 1; }
install -d -m 755 /etc/swanctl/x509 /etc/swanctl/private
install -m 644 "$source_dir/fullchain.pem" /etc/swanctl/x509/hk-vpn-cert.pem
install -m 600 "$source_dir/privkey.pem" /etc/swanctl/private/hk-vpn-key.pem
if systemctl cat strongswan-swanctl.service >/dev/null 2>&1; then
  systemctl start strongswan-swanctl.service
else
  systemctl start strongswan-starter.service
fi
swanctl --load-creds
swanctl --load-conns
systemctl restart strongswan-swanctl 2>/dev/null || systemctl restart strongswan-starter
echo "IKEv2 certificate installed and StrongSwan reloaded."
