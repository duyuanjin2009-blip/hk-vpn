#!/usr/bin/env bash
# Debian 12 + Baota deployment. Run as root from the extracted project folder.
set -euo pipefail

domain=""
admin_password=""
email=""
install_dir="/opt/hk-vpn-suite"
data_dir="/var/lib/hk-vpn-panel"

usage() {
  echo "Usage: $0 --domain panel.example.com --admin-password 'long password' [--email admin@example.com]"
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --domain) domain="$2"; shift 2 ;;
    --admin-password) admin_password="$2"; shift 2 ;;
    --email) email="$2"; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done
[ -n "$domain" ] && [ -n "$admin_password" ] || { usage; exit 2; }
[ "$(id -u)" = "0" ] || { echo "Run as root"; exit 1; }
command -v bt >/dev/null 2>&1 || echo "Warning: Baota command was not found. Continue only if Baota/Nginx is installed another way."

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 sudo iproute2 wireguard wireguard-tools iptables strongswan-swanctl strongswan-charon strongswan-pki libcharon-extra-plugins qrencode curl

id -u hkvpn >/dev/null 2>&1 || useradd --system --home "$data_dir" --shell /usr/sbin/nologin hkvpn
install -d -m 700 -o hkvpn -g hkvpn "$data_dir"
install -d -m 750 -o root -g hkvpn /etc/hk-vpn
install -d -m 700 /etc/swanctl/conf.d
# hkvpn may stat the helper, but only sudo may execute it as root.
install -d -m 711 /usr/local/libexec/hk-vpn
install -d -m 755 "$install_dir"

project_root="$(cd "$(dirname "$0")/../.." && pwd)"
cp -a "$project_root/server" "$install_dir/"
chmod 755 "$install_dir/server/scripts/vpnctl.py" "$install_dir/server/scripts/deploy-bt.sh" "$install_dir/server/scripts/configure-ikev2-cert.sh" "$install_dir/server/scripts/update-panel.sh"
install -m 700 "$install_dir/server/scripts/vpnctl.py" /usr/local/libexec/hk-vpn/vpnctl.py
# Keep optional nodes disabled by default, but render the real deployment
# hostname so a future manual enablement never leaks the example domain.
sed "s/vpn\.example\.com/$domain/g" "$install_dir/server/scripts/protocols.example.json" > /etc/hk-vpn/protocols.json
chmod 660 /etc/hk-vpn/protocols.json
chown root:hkvpn /etc/hk-vpn/protocols.json
cat > /etc/sudoers.d/hk-vpn-panel <<'EOF'
# The helper validates every supplied device field before touching WireGuard or IKEv2.
hkvpn ALL=(root) NOPASSWD: /usr/local/libexec/hk-vpn/vpnctl.py *
EOF
chmod 440 /etc/sudoers.d/hk-vpn-panel
visudo -cf /etc/sudoers.d/hk-vpn-panel >/dev/null

server_private="$(wg genkey)"
server_public="$(printf '%s\n' "$server_private" | wg pubkey)"
wan_if="$(ip -4 route list default | awk '/dev/ {for(i=1;i<=NF;i++) if($i=="dev"){print $(i+1); exit}}')"
[ -n "$wan_if" ] || wan_if="$(ip -o -4 addr show scope global | awk 'NR==1{print $2}')"
[ -n "$wan_if" ] || { echo "Could not detect WAN interface"; exit 1; }

cat > /etc/wireguard/wg0.conf <<EOF
[Interface]
Address = 10.88.0.1/24
ListenPort = 51820
PrivateKey = $server_private
PostUp = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -A FORWARD -s 10.89.0.0/24 -j ACCEPT; iptables -A FORWARD -d 10.89.0.0/24 -j ACCEPT; iptables -t nat -A POSTROUTING -o $wan_if -j MASQUERADE; iptables -t nat -A POSTROUTING -s 10.89.0.0/24 -o $wan_if -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -D FORWARD -s 10.89.0.0/24 -j ACCEPT; iptables -D FORWARD -d 10.89.0.0/24 -j ACCEPT; iptables -t nat -D POSTROUTING -o $wan_if -j MASQUERADE; iptables -t nat -D POSTROUTING -s 10.89.0.0/24 -o $wan_if -j MASQUERADE
EOF
chmod 600 /etc/wireguard/wg0.conf
cat > /etc/sysctl.d/99-hk-vpn.conf <<EOF
net.ipv4.ip_forward=1
EOF
sysctl --system >/dev/null

session_secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
cat > /etc/hk-vpn/panel.env <<EOF
HKVPN_DATA_DIR=$data_dir
HKVPN_ADMIN_PASSWORD=$admin_password
HKVPN_SESSION_SECRET=$session_secret
HKVPN_PUBLIC_URL=https://$domain
HKVPN_WG_ENDPOINT=$domain:51820
HKVPN_WG_PUBLIC_KEY=$server_public
HKVPN_WG_NETWORK=10.88.0
HKVPN_DNS=1.1.1.1,1.0.0.1
HKVPN_PROTOCOLS_FILE=/etc/hk-vpn/protocols.json
HKVPN_CTL=/usr/local/libexec/hk-vpn/vpnctl.py
HKVPN_BIND=127.0.0.1
HKVPN_PORT=8787
EOF
chmod 640 /etc/hk-vpn/panel.env
chown root:hkvpn /etc/hk-vpn/panel.env

cat > /etc/swanctl/conf.d/hk-vpn.conf <<EOF
connections {
  hk-vpn-eap {
    version = 2
    local_addrs = %any
    remote_addrs = %any
    pools = hk-vpn-pool
    proposals = aes256gcm16-prfsha256-ecp256,aes256-sha256-modp2048
    local {
      auth = pubkey
      certs = hk-vpn-cert.pem
      id = $domain
    }
    remote {
      auth = eap-dynamic
      eap_id = %any
    }
    children {
      net {
        local_ts = 0.0.0.0/0
        esp_proposals = aes256gcm16-ecp256,aes256-sha256-modp2048
      }
    }
  }
}
pools {
  hk-vpn-pool {
    addrs = 10.89.0.0/24
    dns = 1.1.1.1,1.0.0.1
  }
}
EOF
if ! grep -qs 'include conf.d/\*\.conf' /etc/swanctl/swanctl.conf; then
  printf '\ninclude conf.d/*.conf\n' >> /etc/swanctl/swanctl.conf
fi
cat > /etc/swanctl/conf.d/hk-vpn-users.conf <<'EOF'
secrets {
}
EOF
chmod 600 /etc/swanctl/conf.d/hk-vpn-users.conf

install -m 644 "$install_dir/server/systemd/hk-vpn-panel.service" /etc/systemd/system/hk-vpn-panel.service
install -m 644 "$install_dir/server/systemd/hk-vpn-reconcile.service" /etc/systemd/system/hk-vpn-reconcile.service
systemctl daemon-reload
# A previous installation may already have wg0 active.  Restart so the newly
# written PostUp rules (forwarding and NAT) are always applied.
systemctl enable wg-quick@wg0.service
systemctl restart wg-quick@wg0.service
if systemctl cat strongswan-swanctl.service >/dev/null 2>&1; then
  systemctl enable --now strongswan-swanctl.service
else
  systemctl enable --now strongswan-starter.service
fi
systemctl enable hk-vpn-reconcile.service
systemctl enable --now hk-vpn-panel.service

echo ""
echo "Base installation complete."
echo "WireGuard server public key: $server_public"
echo "Next: issue a Baota certificate for $domain, install the Nginx template, copy the certificate to /etc/swanctl/x509/, then load swanctl."
echo "See docs/BAOTA_DEPLOYMENT.md for the exact remaining steps."
