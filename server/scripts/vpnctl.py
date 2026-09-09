#!/usr/bin/env python3
"""Privileged, narrow-scope peer and IKE credential manager for HK VPN Suite.

Install this file root-owned and run it only through the limited sudo rule that
deploy-bt.sh creates. It never accepts shell snippets and validates all values
before invoking wg/swanctl.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

STATE_DIR = Path("/etc/hk-vpn")
STATE_PATH = STATE_DIR / "devices.json"
IKE_SECRETS_PATH = Path("/etc/swanctl/conf.d/hk-vpn-users.conf")
WG_INTERFACE = os.environ.get("HKVPN_WG_INTERFACE", "wg0")
WG_SUBNET = ipaddress.ip_network(os.environ.get("HKVPN_WG_SUBNET", "10.88.0.0/24"))
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
SAFE_USER = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def command(*args: str) -> None:
    subprocess.run(args, check=True, timeout=30)


def command_output(*args: str) -> str:
    return subprocess.run(args, check=False, timeout=15, capture_output=True, text=True).stdout


def read_state() -> dict[str, dict[str, str]]:
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid state file: {exc}")


def write_state(data: dict[str, dict[str, str]]) -> None:
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="devices.", dir=STATE_DIR)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, sort_keys=True)
        os.chmod(name, 0o600)
        os.replace(name, STATE_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate(device_id: str, public_key: str | None = None, wg_ip: str | None = None, ike_user: str | None = None) -> None:
    if not SAFE_ID.fullmatch(device_id):
        raise SystemExit("Invalid device id")
    if public_key is not None:
        try:
            if len(base64.b64decode(public_key, validate=True)) != 32:
                raise ValueError
        except ValueError:
            raise SystemExit("Invalid WireGuard public key")
    if wg_ip is not None:
        try:
            ip = ipaddress.ip_address(wg_ip)
            if ip not in WG_SUBNET or ip == WG_SUBNET.network_address:
                raise ValueError
        except ValueError:
            raise SystemExit("Invalid WireGuard address")
    if ike_user is not None and not SAFE_USER.fullmatch(ike_user):
        raise SystemExit("Invalid IKE user")


def write_ike_secrets(state: dict[str, dict[str, str]]) -> None:
    IKE_SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Managed by HK VPN Suite. Do not edit manually.", "secrets {"]
    for record in state.values():
        lines.extend([f"  {record['ike_user']} {{", f"    id = {record['ike_user']}", f"    secret = {record['ike_password']}", "  }"])
    lines.append("}")
    fd, name = tempfile.mkstemp(prefix="hk-vpn-users.", dir=IKE_SECRETS_PATH.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        os.chmod(name, 0o600)
        os.replace(name, IKE_SECRETS_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    command("swanctl", "--load-creds")


def add_device(args: argparse.Namespace) -> None:
    validate(args.id, args.wg_public_key, args.wg_ip, args.ike_user)
    state = read_state()
    state[args.id] = {"wg_public_key": args.wg_public_key, "wg_ip": args.wg_ip, "ike_user": args.ike_user, "ike_password": args.ike_password}
    write_state(state)
    command("wg", "set", WG_INTERFACE, "peer", args.wg_public_key, "allowed-ips", f"{args.wg_ip}/32")
    write_ike_secrets(state)


def remove_device(args: argparse.Namespace) -> None:
    validate(args.id)
    state = read_state()
    record = state.pop(args.id, None)
    if record:
        command("wg", "set", WG_INTERFACE, "peer", record["wg_public_key"], "remove")
        write_state(state)
        write_ike_secrets(state)


def reconcile(_: argparse.Namespace) -> None:
    state = read_state()
    for record in state.values():
        validate("device-01", record["wg_public_key"], record["wg_ip"], record["ike_user"])
        command("wg", "set", WG_INTERFACE, "peer", record["wg_public_key"], "allowed-ips", f"{record['wg_ip']}/32")
    write_ike_secrets(state)


def status(_: argparse.Namespace) -> None:
    """Return non-secret service checks for the web dashboard."""
    listeners = command_output("ss", "-H", "-l", "-n", "-u")
    nat_rules = command_output("iptables", "-t", "nat", "-S", "POSTROUTING")
    forwarding = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "1"
    print(json.dumps({
        "wireguardInterface": Path(f"/sys/class/net/{WG_INTERFACE}").exists(),
        "wireguardPort": bool(re.search(r"(?:\\[::\\]|0\\.0\\.0\\.0):51820\\b", listeners)),
        "ipForward": forwarding,
        "nat": "MASQUERADE" in nat_rules,
        "strongSwan": subprocess.run(["systemctl", "is-active", "--quiet", "strongswan-swanctl.service"], check=False).returncode == 0
            or subprocess.run(["systemctl", "is-active", "--quiet", "strongswan-starter.service"], check=False).returncode == 0,
    }, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(required=True)
    add = sub.add_parser("add-device")
    add.add_argument("--id", required=True); add.add_argument("--wg-public-key", required=True); add.add_argument("--wg-ip", required=True)
    add.add_argument("--ike-user", required=True); add.add_argument("--ike-password", required=True); add.set_defaults(func=add_device)
    remove = sub.add_parser("remove-device"); remove.add_argument("--id", required=True); remove.set_defaults(func=remove_device)
    reconcile_cmd = sub.add_parser("reconcile"); reconcile_cmd.set_defaults(func=reconcile)
    status_cmd = sub.add_parser("status"); status_cmd.set_defaults(func=status)
    args = parser.parse_args(); args.func(args)


if __name__ == "__main__":
    main()
