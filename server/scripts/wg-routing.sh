#!/usr/bin/env bash
# Idempotent IPv4 forwarding for the personal WireGuard gateway.
# Called by wg-quick on every wg0 start/stop and safe to run manually.
set -euo pipefail

action="${1:-}"
interface="${2:-wg0}"
subnet="${HKVPN_WG_SUBNET:-10.88.0.0/24}"

[ "$(id -u)" = "0" ] || { echo "Run as root" >&2; exit 1; }
[ "$action" = "up" ] || [ "$action" = "down" ] || { echo "Usage: $0 up|down [interface]" >&2; exit 2; }

wan_interface="$(ip -4 route show default | awk 'NR == 1 {print $5}')"
[ -n "$wan_interface" ] || { echo "Cannot detect the IPv4 default-route interface" >&2; exit 1; }

run_iptables() { iptables -w 10 "$@"; }
ensure_rule() {
  local table="$1"; shift
  if ! run_iptables $table -C "$@" 2>/dev/null; then
    run_iptables $table -I "$@"
  fi
}
delete_rule() {
  local table="$1"; shift
  while run_iptables $table -C "$@" 2>/dev/null; do
    run_iptables $table -D "$@"
  done
}

if [ "$action" = "up" ]; then
  sysctl -w net.ipv4.ip_forward=1 >/dev/null
  # Insert before Docker/Baota policy chains.  Appending can leave the rule
  # behind a DROP policy, which produces a WireGuard handshake but no Internet.
  ensure_rule "" FORWARD -i "$interface" -o "$wan_interface" -s "$subnet" -j ACCEPT
  ensure_rule "" FORWARD -i "$wan_interface" -o "$interface" -d "$subnet" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  ensure_rule "-t nat" POSTROUTING -s "$subnet" -o "$wan_interface" -j MASQUERADE
  ensure_rule "" INPUT -p udp --dport 51820 -j ACCEPT
  echo "WireGuard routing ready: $subnet -> $wan_interface"
else
  delete_rule "" FORWARD -i "$interface" -o "$wan_interface" -s "$subnet" -j ACCEPT
  delete_rule "" FORWARD -i "$wan_interface" -o "$interface" -d "$subnet" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
  delete_rule "-t nat" POSTROUTING -s "$subnet" -o "$wan_interface" -j MASQUERADE
fi
