#!/usr/bin/env python3
"""HK VPN Suite personal control plane.

The service intentionally uses only Python's standard library so it can run on
Debian 12 behind Baota/Nginx without a package manager or a database service.
It manages device identities, renders FLClash/Mihomo subscriptions and invokes
the narrowly scoped root helper installed by scripts/deploy-bt.sh.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from wsgiref.simple_server import make_server


ROOT = Path(os.environ.get("HKVPN_DATA_DIR", "/var/lib/hk-vpn-panel"))
DB_PATH = ROOT / "panel.db"
PROTOCOLS_PATH = Path(os.environ.get("HKVPN_PROTOCOLS_FILE", "/etc/hk-vpn/protocols.json"))
ADMIN_PASSWORD = os.environ.get("HKVPN_ADMIN_PASSWORD", "change-me-before-start")
SESSION_SECRET = os.environ.get("HKVPN_SESSION_SECRET", "change-me-before-start").encode()
PUBLIC_URL = os.environ.get("HKVPN_PUBLIC_URL", "https://panel.example.com").rstrip("/")
WG_ENDPOINT = os.environ.get("HKVPN_WG_ENDPOINT", "vpn.example.com:51820")
WG_PUBLIC_KEY = os.environ.get("HKVPN_WG_PUBLIC_KEY", "REPLACE_WITH_SERVER_PUBLIC_KEY")
WG_NETWORK = os.environ.get("HKVPN_WG_NETWORK", "10.88.0")
DNS_SERVERS = [x.strip() for x in os.environ.get("HKVPN_DNS", "1.1.1.1,1.0.0.1").split(",") if x.strip()]
VPNCTL = os.environ.get("HKVPN_CTL", "/usr/local/libexec/hk-vpn/vpnctl.py")
STATIC_DIR = Path(__file__).with_name("static")
SAFE_SYSTEMD_UNIT = re.compile(r"^[A-Za-z0-9@_.:-]{1,128}$")


def now() -> int:
    return int(time.time())


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def b64(value: bytes) -> str:
    return base64.b64encode(value).decode()


def safe_name(value: str) -> str:
    # Names are rendered through YAML/HTML escaping and never put into response
    # headers, so permit normal Chinese punctuation and emoji.  Only control
    # characters are removed.
    value = "".join(ch for ch in value.strip() if ch.isprintable() and ch not in "\r\n\t")[:64]
    return value or "My device"


def constant_time_password(value: str) -> bool:
    return hmac.compare_digest(value.encode(), ADMIN_PASSWORD.encode())


def make_session() -> str:
    payload = f"admin:{now() + 60 * 60 * 24 * 30}".encode()
    signature = hmac.new(SESSION_SECRET, payload, hashlib.sha256).digest()
    return b64(payload + b"." + signature).rstrip("=")


def valid_session(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        data = base64.b64decode(raw + "=" * (-len(raw) % 4))
        payload, signature = data.rsplit(b".", 1)
        expected = hmac.new(SESSION_SECRET, payload, hashlib.sha256).digest()
        _, expiry = payload.decode().split(":", 1)
        return hmac.compare_digest(signature, expected) and int(expiry) >= now()
    except Exception:
        return False


def database() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS devices (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          platform TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          revoked_at INTEGER,
          wg_private_key TEXT NOT NULL,
          wg_public_key TEXT NOT NULL,
          wg_ip TEXT NOT NULL UNIQUE,
          ike_username TEXT NOT NULL UNIQUE,
          ike_password TEXT NOT NULL,
          subscription_token TEXT NOT NULL UNIQUE,
          client_id TEXT UNIQUE,
          provision_error TEXT
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(devices)")}
    if "client_id" not in columns:
        conn.execute("ALTER TABLE devices ADD COLUMN client_id TEXT UNIQUE")
    return conn


def wg_keypair() -> tuple[str, str]:
    """Use real wg tooling in production; provide a clearly non-production fallback for tests."""
    # The control helper is installed by deploy-bt.sh.  Requiring it here keeps
    # development/CI deterministic on runners which happen to have `wg`, while
    # production continues to generate genuine WireGuard keys.
    if os.environ.get("HKVPN_TEST_MODE") != "1" and shutil.which("wg") and Path(VPNCTL).exists():
        private = subprocess.run(["wg", "genkey"], check=True, capture_output=True, text=True).stdout.strip()
        public = subprocess.run(["wg", "pubkey"], check=True, input=private + "\n", capture_output=True, text=True).stdout.strip()
        return private, public
    # The panel can run locally without WireGuard. Deploy scripts refuse this fallback.
    return b64(secrets.token_bytes(32)), b64(secrets.token_bytes(32))


def protocol_catalog() -> dict[str, Any]:
    if not PROTOCOLS_PATH.exists():
        return {"nodes": []}
    try:
        value = json.loads(PROTOCOLS_PATH.read_text())
        return value if isinstance(value, dict) and isinstance(value.get("nodes"), list) else {"nodes": []}
    except Exception:
        return {"nodes": []}


def protocol_ready(node: dict[str, Any]) -> tuple[bool, str | None]:
    """A template must not reach a subscription before it is fully configured."""
    config = node.get("config")
    if not isinstance(config, dict):
        return False, "缺少协议配置"
    raw = json.dumps(config, ensure_ascii=False)
    if "REPLACE_WITH_" in raw or "vpn.example.com" in raw:
        return False, "尚未填写真实域名、密码或 UUID"
    protocol_type = str(config.get("type", ""))
    common = ("name", "server", "port", "service")
    required = {
        "hysteria2": ("password", "sni"),
        "tuic": ("uuid", "password"),
        "ss": ("cipher", "password"),
        "vless": ("uuid", "network"),
        "trojan": ("password", "sni"),
        # Mihomo requires a CA plus either username/password or cert/key for
        # OpenVPN. A bare endpoint must never be treated as usable.
        "openvpn": ("ca",),
    }
    if protocol_type not in required:
        return False, "不支持的 FLClash 协议类型"
    missing = [key for key in (*common, *required[protocol_type]) if not config.get(key)]
    if protocol_type == "openvpn" and not ((config.get("username") and config.get("password")) or (config.get("cert") and config.get("key"))):
        missing.append("认证信息")
    try:
        port = int(config.get("port", 0))
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        missing.append("有效端口")
    if missing:
        return False, "缺少 " + "、".join(missing)
    return True, None


def protocol_transports(config: dict[str, Any]) -> tuple[str, ...]:
    protocol_type = config.get("type")
    if protocol_type in {"hysteria2", "tuic"}:
        return ("udp",)
    if protocol_type == "ss":
        return ("tcp", "udp")
    if protocol_type == "openvpn":
        return ("udp",) if config.get("udp") is not False else ("tcp",)
    return ("tcp",)


def protocol_runtime_ready(node: dict[str, Any], status: dict[str, Any]) -> tuple[bool, str | None]:
    ready, reason = protocol_ready(node)
    if not ready:
        return False, reason
    if status.get("error"):
        return False, "无法读取服务器端口状态"
    config = node["config"]
    try:
        port = int(config["port"])
    except (KeyError, TypeError, ValueError):
        return False, "端口配置无效"
    listening = {
        "tcp": set(status.get("tcpListeningPorts", [])),
        "udp": set(status.get("udpListeningPorts", [])),
    }
    missing = [transport.upper() for transport in protocol_transports(config) if port not in listening[transport]]
    if missing:
        return False, "服务器未监听 " + "/".join(missing) + f" {port}"
    service = str(config.get("service", ""))
    if not SAFE_SYSTEMD_UNIT.fullmatch(service):
        return False, "服务名称格式无效"
    services = status.get("services")
    if not isinstance(services, dict) or services.get(service) is not True:
        return False, f"服务未运行：{service}"
    return True, None


def public_protocol(node: dict[str, Any], status: dict[str, Any] | None = None) -> dict[str, Any]:
    configured, config_reason = protocol_ready(node)
    ready, reason = protocol_runtime_ready(node, status) if status is not None else (configured, config_reason)
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    return {
        "id": node.get("id", ""), "name": config.get("name", node.get("id", "未知协议")),
        "enabled": node.get("enabled") is True, "configured": configured,
        "ready": ready, "reason": reason, "port": config.get("port"),
    }


def enabled_protocols(status: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    nodes = [x for x in protocol_catalog()["nodes"] if x.get("enabled") is True]
    if status is None:
        return [x for x in nodes if protocol_ready(x)[0]]
    return [x for x in nodes if protocol_runtime_ready(x, status)[0]]


def protocol_runtime_status() -> dict[str, Any]:
    services = []
    for node in protocol_catalog()["nodes"]:
        config = node.get("config")
        service = str(config.get("service", "")) if isinstance(config, dict) else ""
        if SAFE_SYSTEMD_UNIT.fullmatch(service) and service not in services:
            services.append(service)
    args = [part for service in services for part in ("--service", service)]
    return vpnctl_json("status", *args)


def save_protocol_catalog(value: dict[str, Any]) -> None:
    PROTOCOLS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # /etc/hk-vpn is deliberately not writable by the web-service account.  Keep
    # the directory protected and update the group-writable file in place.
    # That prevents the panel from replacing panel.env or any root helper.
    PROTOCOLS_PATH.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def call_vpnctl(*args: str) -> str | None:
    """Provisioning fails closed but does not lose the generated device record."""
    command = Path(VPNCTL)
    if not command.exists():
        return "Root helper is not installed yet; run deploy-bt.sh then click Reconcile."
    try:
        result = subprocess.run(["/usr/bin/sudo", "-n", str(command), *args], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return "服务器操作超时，请稍后重试"
    except OSError as exc:
        return f"无法执行服务器操作：{exc.strerror or exc}"
    if result.returncode:
        return (result.stderr or result.stdout or "vpnctl failed").strip()[:500]
    return None


def vpnctl_json(*args: str) -> dict[str, Any]:
    command = Path(VPNCTL)
    if not command.exists():
        return {"error": "诊断程序未安装"}
    try:
        result = subprocess.run(["/usr/bin/sudo", "-n", str(command), *args], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return {"error": "诊断操作超时"}
    except OSError as exc:
        return {"error": f"无法执行诊断：{exc.strerror or exc}"}
    if result.returncode:
        return {"error": (result.stderr or result.stdout or "诊断失败").strip()[:300]}
    try:
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else {"error": "诊断数据格式错误"}
    except json.JSONDecodeError:
        return {"error": "诊断数据格式错误"}


def allocate_ip(conn: sqlite3.Connection) -> str:
    used = {row[0] for row in conn.execute("SELECT wg_ip FROM devices")}
    for octet in range(2, 255):
        candidate = f"{WG_NETWORK}.{octet}"
        if candidate not in used:
            return candidate
    raise RuntimeError("WireGuard address pool is full")


def connection_for(row: sqlite3.Row, status: dict[str, Any]) -> dict[str, Any]:
    """Translate `wg dump` counters into a clear, server-side device state."""
    if status.get("error"):
        return {"state": "unknown", "reason": "服务器暂时无法读取 WireGuard 状态"}
    if "wireguardInterface" in status and status.get("wireguardInterface") is not True:
        return {"state": "unknown", "reason": "服务器的 wg0 接口未启动"}
    peers = status.get("peers")
    if not isinstance(peers, list):
        return {"state": "unknown", "reason": "WireGuard 状态格式异常"}
    peer = next((item for item in peers if isinstance(item, dict) and item.get("publicKey") == row["wg_public_key"]), None)
    if not peer:
        return {"state": "not-provisioned", "reason": "服务器尚未找到此设备的 Peer"}
    try:
        handshake = int(peer.get("latestHandshake", 0))
        received = max(0, int(peer.get("receivedBytes", 0)))
        sent = max(0, int(peer.get("sentBytes", 0)))
    except (TypeError, ValueError):
        return {"state": "unknown", "reason": "WireGuard 统计数据异常"}
    if handshake <= 0:
        return {
            "state": "waiting", "reason": "等待首次握手；手机刚连接时通常需要数秒",
            "clientToServerBytes": received, "serverToClientBytes": sent,
        }
    age = max(0, now() - handshake)
    return {
        "state": "connected" if age <= 180 else "idle",
        "lastHandshakeAt": handshake, "handshakeAgeSeconds": age,
        # Direction labels intentionally use the client perspective. They are
        # cumulative WireGuard counters, not a real-time speed test.
        "clientToServerBytes": received,
        "serverToClientBytes": sent,
    }


def public_device(row: sqlite3.Row, connection: dict[str, Any] | None = None) -> dict[str, Any]:
    value = {
        "id": row["id"], "name": row["name"], "platform": row["platform"],
        "createdAt": row["created_at"], "revokedAt": row["revoked_at"],
        "wireGuardIp": row["wg_ip"], "ikev2Username": row["ike_username"],
        "subscriptionUrl": f"{PUBLIC_URL}/sub/{row['subscription_token']}.yaml",
        "provisionError": row["provision_error"],
    }
    if connection is not None:
        value["connection"] = connection
    return value


def public_diagnostics(status: dict[str, Any]) -> dict[str, Any]:
    """Keep peer public keys and internal endpoint data out of the browser."""
    if status.get("error"):
        return {"error": status["error"]}
    return {
        "wireguardInterface": status.get("wireguardInterface") is True,
        "wireguardListenPort": status.get("wireguardListenPort"),
        "wireguardPort": status.get("wireguardPort") is True,
        "ipForward": status.get("ipForward") is True,
        "forwardRules": status.get("forwardRules") is True,
        "nat": status.get("nat") is True,
        "strongSwan": status.get("strongSwan") is True,
        "peerCount": len(status.get("peers", [])) if isinstance(status.get("peers"), list) else 0,
        "checkedAt": status.get("checkedAt"),
    }


def wireguard_config(row: sqlite3.Row) -> str:
    dns = ", ".join(DNS_SERVERS)
    return f"""[Interface]
PrivateKey = {row['wg_private_key']}
Address = {row['wg_ip']}/32
DNS = {dns}
MTU = 1380

[Peer]
PublicKey = {WG_PUBLIC_KEY}
Endpoint = {WG_ENDPOINT}
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""


def flclash_subscription(row: sqlite3.Row) -> str:
    base_node = {
        "name": f"{row['name']} · WireGuard",
        "type": "wireguard",
        "server": WG_ENDPOINT.rsplit(":", 1)[0],
        "port": int(WG_ENDPOINT.rsplit(":", 1)[1]),
        "ip": row["wg_ip"],
        "private-key": row["wg_private_key"],
        "public-key": WG_PUBLIC_KEY,
        "allowed-ips": ["0.0.0.0/0"],
        "udp": True,
        "mtu": 1380,
        "persistent-keepalive": 25,
    }
    # Never return an enabled-but-stopped advanced service in a subscription.
    # WireGuard remains available as the reliable fallback if a diagnostic call
    # itself fails.
    runtime_status = protocol_runtime_status()
    nodes = [base_node] + [x["config"] for x in enabled_protocols(runtime_status)]
    names = [x.get("name", "Unnamed") for x in nodes]
    document: dict[str, Any] = {
        "mixed-port": 7890,
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "tun": {"enable": True, "stack": "mixed", "auto-route": True, "auto-detect-interface": True, "dns-hijack": ["any:53"]},
        "proxies": nodes,
        "proxy-groups": [
            {"name": "Hong Kong Manual", "type": "select", "proxies": names},
            {"name": "Hong Kong Auto", "type": "url-test", "url": "https://www.gstatic.com/generate_204", "interval": 300, "proxies": names},
        ],
        "rules": ["MATCH,Hong Kong Manual"],
    }
    return yaml_dump(document)


def yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def yaml_dump(value: Any, indent: int = 0) -> str:
    """Small safe YAML renderer for the restricted subscription data model."""
    pad = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                lines.append(f"{pad}{key}:")
                lines.append(yaml_dump(child, indent + 2).rstrip())
            else:
                lines.append(f"{pad}{key}: {yaml_scalar(child)}")
        return "\n".join(lines) + "\n"
    if isinstance(value, list):
        lines = []
        for child in value:
            if isinstance(child, dict):
                items = list(child.items())
                if not items:
                    lines.append(f"{pad}- {{}}")
                    continue
                first_key, first_value = items[0]
                if isinstance(first_value, (dict, list)):
                    lines.append(f"{pad}- {first_key}:")
                    lines.append(yaml_dump(first_value, indent + 4).rstrip())
                else:
                    lines.append(f"{pad}- {first_key}: {yaml_scalar(first_value)}")
                for key, nested in items[1:]:
                    if isinstance(nested, (dict, list)):
                        lines.append(f"{pad}  {key}:")
                        lines.append(yaml_dump(nested, indent + 4).rstrip())
                    else:
                        lines.append(f"{pad}  {key}: {yaml_scalar(nested)}")
            elif isinstance(child, list):
                lines.append(f"{pad}-")
                lines.append(yaml_dump(child, indent + 2).rstrip())
            else:
                lines.append(f"{pad}- {yaml_scalar(child)}")
        return "\n".join(lines) + "\n"
    return f"{pad}{yaml_scalar(value)}\n"


@dataclass
class Request:
    environ: dict[str, Any]
    body: bytes

    @property
    def method(self) -> str:
        return self.environ["REQUEST_METHOD"].upper()

    @property
    def path(self) -> str:
        return unquote(self.environ.get("PATH_INFO", "/"))

    def cookie(self, name: str) -> str | None:
        jar = SimpleCookie(self.environ.get("HTTP_COOKIE", ""))
        return jar[name].value if name in jar else None

    def is_admin(self) -> bool:
        return valid_session(self.cookie("hkvpn_session"))

    def json(self) -> dict[str, Any]:
        try:
            return json.loads(self.body or b"{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON request body") from exc


def response(start_response, status: HTTPStatus, body: bytes = b"", content_type: str = "application/json; charset=utf-8", headers: list[tuple[str, str]] | None = None):
    items = [("Content-Type", content_type), ("Content-Length", str(len(body))), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer")]
    items.extend(headers or [])
    start_response(f"{status.value} {status.phrase}", items)
    return [body]


def json_response(start_response, status: HTTPStatus, value: Any, headers=None):
    return response(start_response, status, json_bytes(value), headers=headers)


def application(environ, start_response):
    length = int(environ.get("CONTENT_LENGTH") or 0)
    req = Request(environ, environ["wsgi.input"].read(length))
    path = req.path
    if path.startswith("/sub/") and path.endswith(".yaml") and req.method == "GET":
        token = path[len("/sub/"):-len(".yaml")]
        conn = database()
        row = conn.execute("SELECT * FROM devices WHERE subscription_token=? AND revoked_at IS NULL", (token,)).fetchone()
        if not row:
            return response(start_response, HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
        return response(start_response, HTTPStatus.OK, flclash_subscription(row).encode(), "text/yaml; charset=utf-8", [("Cache-Control", "no-store")])
    if path == "/api/client/enroll" and req.method == "POST":
        return native_enroll(req, start_response)
    if path == "/login" and req.method == "POST":
        form = parse_qs(req.body.decode(errors="ignore"))
        if not constant_time_password(form.get("password", [""])[0]):
            return response(start_response, HTTPStatus.UNAUTHORIZED, b"Invalid password", "text/plain; charset=utf-8")
        return response(start_response, HTTPStatus.FOUND, b"", "text/plain", [("Location", "/"), ("Set-Cookie", f"hkvpn_session={make_session()}; Path=/; HttpOnly; SameSite=Strict; Secure; Max-Age=2592000")])
    if path == "/logout" and req.method == "POST":
        return response(start_response, HTTPStatus.FOUND, b"", "text/plain", [("Location", "/login"), ("Set-Cookie", "hkvpn_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict; Secure")])
    if path == "/login" and req.method == "GET":
        return static_response(start_response, "login.html")
    if path in ("/", "/index.html") and req.method == "GET":
        if not req.is_admin():
            return response(start_response, HTTPStatus.FOUND, b"", "text/plain", [("Location", "/login")])
        return static_response(start_response, "index.html")
    if path.startswith("/assets/") and req.method == "GET":
        return static_response(start_response, path[len("/assets/"):])
    if not path.startswith("/api/"):
        return response(start_response, HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
    if not req.is_admin():
        return json_response(start_response, HTTPStatus.UNAUTHORIZED, {"error": "login required"})
    try:
        return api(req, start_response)
    except ValueError as exc:
        return json_response(start_response, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
    except Exception as exc:  # Internal details stay in journalctl.
        print(f"request failed: {exc}", flush=True)
        return json_response(start_response, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "server error"})


def static_response(start_response, name: str):
    target = (STATIC_DIR / name).resolve()
    if STATIC_DIR.resolve() not in target.parents or not target.exists():
        return response(start_response, HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
    types = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
    return response(start_response, HTTPStatus.OK, target.read_bytes(), types.get(target.suffix, "application/octet-stream"), [("Cache-Control", "no-store")])


def api(req: Request, start_response):
    conn = database()
    if req.path == "/api/health" and req.method == "GET":
        return json_response(start_response, HTTPStatus.OK, {"ok": True, "wireGuardEndpoint": WG_ENDPOINT, "protocols": len(enabled_protocols())})
    if req.path == "/api/diagnostics" and req.method == "GET":
        return json_response(start_response, HTTPStatus.OK, public_diagnostics(vpnctl_json("status")))
    if req.path == "/api/protocols" and req.method == "GET":
        runtime_status = protocol_runtime_status()
        return json_response(start_response, HTTPStatus.OK, {"nodes": [public_protocol(node, runtime_status) for node in protocol_catalog()["nodes"]]})
    if req.path.startswith("/api/protocols/") and req.method == "PATCH":
        protocol_id = req.path.rsplit("/", 1)[1]
        body = req.json()
        if not isinstance(body.get("enabled"), bool):
            raise ValueError("enabled must be true or false")
        catalog = protocol_catalog()
        node = next((x for x in catalog["nodes"] if x.get("id") == protocol_id), None)
        if not node:
            return json_response(start_response, HTTPStatus.NOT_FOUND, {"error": "protocol not found"})
        runtime_status = protocol_runtime_status()
        ready, reason = protocol_runtime_ready(node, runtime_status)
        if body["enabled"] and not ready:
            return json_response(start_response, HTTPStatus.CONFLICT, {"error": f"不能启用：{reason}。请先部署服务、填写 protocols.json，并确认本机端口正在监听。"})
        node["enabled"] = body["enabled"]
        save_protocol_catalog(catalog)
        return json_response(start_response, HTTPStatus.OK, {"node": public_protocol(node, runtime_status)})
    if req.path == "/api/devices" and req.method == "GET":
        rows = conn.execute("SELECT * FROM devices ORDER BY created_at DESC").fetchall()
        wireguard_status = vpnctl_json("status")
        return json_response(start_response, HTTPStatus.OK, {
            "devices": [public_device(row, connection_for(row, wireguard_status)) for row in rows],
            "checkedAt": wireguard_status.get("checkedAt"),
            "connectionError": wireguard_status.get("error"),
        })
    if req.path == "/api/devices" and req.method == "POST":
        body = req.json()
        name = safe_name(str(body.get("name", "")))
        platform = str(body.get("platform", "Android"))[:32]
        device_id = secrets.token_urlsafe(9)
        private, public = wg_keypair()
        ip = allocate_ip(conn)
        ike_user = "hk-" + secrets.token_urlsafe(6).replace("-", "a").replace("_", "b")
        ike_password = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO devices (id,name,platform,created_at,revoked_at,wg_private_key,wg_public_key,wg_ip,ike_username,ike_password,subscription_token,client_id,provision_error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (device_id, name, platform, now(), None, private, public, ip, ike_user, ike_password, token, None, None))
        conn.commit()
        error = call_vpnctl("add-device", "--id", device_id, "--wg-public-key", public, "--wg-ip", ip, "--ike-user", ike_user, "--ike-password", ike_password)
        if error:
            conn.execute("UPDATE devices SET provision_error=? WHERE id=?", (error, device_id))
            conn.commit()
        row = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        return json_response(start_response, HTTPStatus.CREATED, {"device": public_device(row)})
    if req.path.startswith("/api/devices/"):
        parts = req.path.split("/")
        if len(parts) < 4:
            raise ValueError("Invalid device path")
        device_id, action = parts[3], parts[4] if len(parts) > 4 else ""
        row = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if not row:
            return json_response(start_response, HTTPStatus.NOT_FOUND, {"error": "device not found"})
        if action == "" and req.method == "PATCH":
            name = safe_name(str(req.json().get("name", "")))
            conn.execute("UPDATE devices SET name=? WHERE id=?", (name, device_id))
            conn.commit()
            updated = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
            return json_response(start_response, HTTPStatus.OK, {"device": public_device(updated)})
        if action == "wireguard.conf" and req.method == "GET":
            # WSGI headers are Latin-1. Device names may be Chinese, so never put
            # them in Content-Disposition; the opaque ID is URL/header safe.
            return response(start_response, HTTPStatus.OK, wireguard_config(row).encode(), "text/plain; charset=utf-8", [("Content-Disposition", f'attachment; filename="hk-vpn-{row["id"]}.conf"'), ("Cache-Control", "no-store")])
        if action == "ikev2" and req.method == "GET":
            return json_response(start_response, HTTPStatus.OK, {"server": WG_ENDPOINT.rsplit(":", 1)[0], "username": row["ike_username"], "password": row["ike_password"], "type": "IKEv2 / IPsec EAP-MSCHAPv2"})
        if action == "reconcile" and req.method == "POST":
            error = call_vpnctl("reconcile")
            conn.execute("UPDATE devices SET provision_error=? WHERE id=?", (error, device_id))
            conn.commit()
            return json_response(start_response, HTTPStatus.OK, {"ok": error is None, "error": error})
        if action == "revoke" and req.method == "POST":
            error = call_vpnctl("remove-device", "--id", device_id)
            conn.execute("UPDATE devices SET revoked_at=?, provision_error=? WHERE id=?", (now(), error, device_id))
            conn.commit()
            return json_response(start_response, HTTPStatus.OK, {"ok": error is None, "error": error})
    return json_response(start_response, HTTPStatus.NOT_FOUND, {"error": "not found"})


def native_enroll(req: Request, start_response):
    body = req.json()
    if not constant_time_password(str(body.get("password", ""))):
        return json_response(start_response, HTTPStatus.UNAUTHORIZED, {"error": "密码错误"})
    client_id = str(body.get("clientId", ""))
    if len(client_id) < 8 or len(client_id) > 128 or not all(ch.isalnum() or ch in "-_" for ch in client_id):
        return json_response(start_response, HTTPStatus.BAD_REQUEST, {"error": "无效设备标识"})
    platform = safe_name(str(body.get("platform", "Client")))
    conn = database()
    row = conn.execute("SELECT * FROM devices WHERE client_id=?", (client_id,)).fetchone()
    if not row:
        device_id = secrets.token_urlsafe(9)
        private, public = wg_keypair()
        ip = allocate_ip(conn)
        ike_user = "hk-" + secrets.token_urlsafe(6).replace("-", "a").replace("_", "b")
        ike_password = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(32)
        name = f"{platform} {client_id[-6:]}"
        conn.execute("INSERT INTO devices (id,name,platform,created_at,revoked_at,wg_private_key,wg_public_key,wg_ip,ike_username,ike_password,subscription_token,client_id,provision_error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (device_id, name, platform, now(), None, private, public, ip, ike_user, ike_password, token, client_id, None))
        conn.commit()
        error = call_vpnctl("add-device", "--id", device_id, "--wg-public-key", public, "--wg-ip", ip, "--ike-user", ike_user, "--ike-password", ike_password)
        if error:
            conn.execute("UPDATE devices SET provision_error=? WHERE id=?", (error, device_id)); conn.commit()
        row = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    if row["revoked_at"]:
        return json_response(start_response, HTTPStatus.FORBIDDEN, {"error": "此设备已被撤销，请在控制台删除后重试"})
    return json_response(start_response, HTTPStatus.OK, {"wireGuardConfig": wireguard_config(row), "device": public_device(row)})


if __name__ == "__main__":
    host = os.environ.get("HKVPN_BIND", "127.0.0.1")
    port = int(os.environ.get("HKVPN_PORT", "8787"))
    print(f"HK VPN panel listening on {host}:{port}", flush=True)
    make_server(host, port, application).serve_forever()
