#!/usr/bin/env bash
# Clean personal reinstallation with an on-server backup. It intentionally
# never stores the panel password in this repository.
set -euo pipefail

domain=""
confirmed=false

usage() {
  echo "Usage: $0 --domain vpn.example.com --confirm-reset"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --domain) domain="$2"; shift 2 ;;
    --confirm-reset) confirmed=true; shift ;;
    *) usage; exit 2 ;;
  esac
done

[ "$confirmed" = true ] && [ -n "$domain" ] || { usage; exit 2; }
[ "$(id -u)" = "0" ] || { echo "Run as root"; exit 1; }

read -r -s -p "Set HK VPN panel password: " admin_password
echo
[ -n "$admin_password" ] || { echo "Password cannot be empty"; exit 1; }

project_root="$(cd "$(dirname "$0")/../.." && pwd)"
timestamp="$(date +%Y%m%d-%H%M%S)"
backup="/root/hk-vpn-backup-$timestamp.tar.gz"

tar -czf "$backup" --ignore-failed-read \
  /var/lib/hk-vpn-panel /etc/hk-vpn /etc/wireguard/wg0.conf \
  /etc/swanctl/conf.d/hk-vpn.conf /etc/swanctl/conf.d/hk-vpn-users.conf \
  /etc/systemd/system/hk-vpn-panel.service /etc/systemd/system/hk-vpn-reconcile.service \
  /usr/local/libexec/hk-vpn /etc/sudoers.d/hk-vpn-panel 2>/dev/null || true
echo "Backup created: $backup"

systemctl disable --now hk-vpn-panel.service hk-vpn-reconcile.service wg-quick@wg0.service 2>/dev/null || true
rm -rf /var/lib/hk-vpn-panel /etc/hk-vpn /usr/local/libexec/hk-vpn
rm -f /etc/wireguard/wg0.conf /etc/swanctl/conf.d/hk-vpn.conf /etc/swanctl/conf.d/hk-vpn-users.conf
rm -f /etc/systemd/system/hk-vpn-panel.service /etc/systemd/system/hk-vpn-reconcile.service /etc/sudoers.d/hk-vpn-panel
systemctl daemon-reload

"$project_root/server/scripts/deploy-bt.sh" --domain "$domain" --admin-password "$admin_password"

if [ -f "/www/server/panel/vhost/cert/$domain/fullchain.pem" ] && [ -f "/www/server/panel/vhost/cert/$domain/privkey.pem" ]; then
  sed "s/__DOMAIN__/$domain/g" "$project_root/server/nginx/hk-vpn-panel.conf.template" > "/www/server/panel/vhost/nginx/$domain.conf"
  nginx -t && (systemctl reload nginx || /etc/init.d/nginx reload)
  "$project_root/server/scripts/configure-ikev2-cert.sh" "$domain"
else
  echo "Baota certificate is not present yet. Issue a certificate for $domain, then run:"
  echo "  $project_root/server/scripts/configure-ikev2-cert.sh $domain"
fi

echo "Reinstallation completed. Open https://$domain"
