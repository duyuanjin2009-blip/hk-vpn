import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["HKVPN_DATA_DIR"] = tempfile.mkdtemp(prefix="hkvpn-test-")
os.environ["HKVPN_ADMIN_PASSWORD"] = "test-password"
os.environ["HKVPN_SESSION_SECRET"] = "test-session-secret"
os.environ["HKVPN_PUBLIC_URL"] = "https://panel.test"
os.environ["HKVPN_WG_ENDPOINT"] = "vpn.test:51820"
os.environ["HKVPN_WG_PUBLIC_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["HKVPN_CTL"] = "/helper-not-installed"
os.environ["HKVPN_TEST_MODE"] = "1"
_protocol_file = Path(tempfile.mkdtemp(prefix="hkvpn-protocols-")) / "protocols.json"
_protocol_file.write_text(json.dumps({"nodes": [{"id": "hysteria2", "enabled": False, "config": {"name": "HK Hysteria2", "server": "vpn.example.com", "password": "REPLACE_WITH_PASSWORD"}}]}))
os.environ["HKVPN_PROTOCOLS_FILE"] = str(_protocol_file)

from app import main  # noqa: E402
from app.main import application  # noqa: E402


def request(path, method="GET", body=None, cookie=""):
    parsed = urlsplit(path)
    raw = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else b"")
    received = {}
    def start_response(status, headers):
        # wsgiref encodes headers as Latin-1 before they reach the client.
        for header_name, header_value in headers:
            header_name.encode("latin-1"); header_value.encode("latin-1")
        received["status"] = status; received["headers"] = dict(headers)
    result = application({"REQUEST_METHOD": method, "PATH_INFO": parsed.path, "QUERY_STRING": parsed.query, "wsgi.input": io.BytesIO(raw), "CONTENT_LENGTH": str(len(raw)), "HTTP_COOKIE": cookie}, start_response)
    return received, b"".join(result)


class PanelTests(unittest.TestCase):
    def setUp(self):
        _protocol_file.write_text(json.dumps({"nodes": [{"id": "hysteria2", "enabled": False, "config": {"name": "HK Hysteria2", "server": "vpn.example.com", "password": "REPLACE_WITH_PASSWORD"}}]}))

    def test_native_enrollment_and_subscription(self):
        response, body = request("/api/client/enroll", "POST", {"password": "test-password", "clientId": "client-testing-001", "platform": "Android"})
        self.assertTrue(response["status"].startswith("200"))
        payload = json.loads(body)
        self.assertIn("[Interface]", payload["wireGuardConfig"])
        self.assertIn("[Peer]", payload["wireGuardConfig"])
        sub = payload["device"]["subscriptionUrl"].replace("https://panel.test", "")
        response, body = request(sub)
        self.assertTrue(response["status"].startswith("200"))
        self.assertIn(b"type: 'wireguard'", body)
        self.assertIn(b"dns:", body)
        self.assertIn(b"enhanced-mode: 'fake-ip'", body)
        self.assertIn(b"fake-ip-range: '198.18.0.1/16'", body)

    def test_admin_can_create_device(self):
        response, _ = request("/login", "POST", b"password=test-password")
        cookie = response["headers"]["Set-Cookie"].split(";", 1)[0]
        response, body = request("/api/health", cookie=cookie)
        self.assertEqual(json.loads(body)["version"], main.APP_VERSION)
        response, body = request("/api/devices", "POST", {"name": "Windows", "platform": "Windows"}, cookie)
        self.assertTrue(response["status"].startswith("201"))
        device = json.loads(body)["device"]
        self.assertEqual(device["platform"], "Windows")
        response, body = request(f"/api/devices/{device['id']}/ikev2", cookie=cookie)
        self.assertTrue(response["status"].startswith("200"))
        self.assertEqual(json.loads(body)["type"], "IKEv2 / IPsec EAP-MSCHAPv2")

    def test_chinese_device_name_has_ascii_config_download_header(self):
        response, _ = request("/login", "POST", b"password=test-password")
        cookie = response["headers"]["Set-Cookie"].split(";", 1)[0]
        response, body = request("/api/devices", "POST", {"name": "我的安卓手机", "platform": "Android"}, cookie)
        self.assertTrue(response["status"].startswith("201"))
        device = json.loads(body)["device"]
        response, body = request(f"/api/devices/{device['id']}/wireguard.conf", cookie=cookie)
        self.assertTrue(response["status"].startswith("200"))
        self.assertEqual(response["headers"]["Content-Disposition"], f'attachment; filename="hk-vpn-{device["id"]}.conf"')
        self.assertIn(b"[Interface]", body)

    def test_template_protocol_cannot_be_enabled_and_device_name_updates_node(self):
        response, _ = request("/login", "POST", b"password=test-password")
        cookie = response["headers"]["Set-Cookie"].split(";", 1)[0]
        response, body = request("/api/protocols/hysteria2", "PATCH", {"enabled": True}, cookie)
        self.assertTrue(response["status"].startswith("409"))
        response, body = request("/api/devices", "POST", {"name": "旧名称", "platform": "FLClash"}, cookie)
        device = json.loads(body)["device"]
        response, _ = request(f"/api/devices/{device['id']}", "PATCH", {"name": "我的香港节点"}, cookie)
        self.assertTrue(response["status"].startswith("200"))
        response, body = request(device["subscriptionUrl"].replace("https://panel.test", ""))
        self.assertIn("我的香港节点 · WireGuard".encode(), body)

    def test_connection_status_uses_server_side_directional_counters(self):
        response, _ = request("/login", "POST", b"password=test-password")
        cookie = response["headers"]["Set-Cookie"].split(";", 1)[0]
        response, body = request("/api/devices", "POST", {"name": "状态测试", "platform": "FLClash"}, cookie)
        device = json.loads(body)["device"]
        row = main.database().execute("SELECT * FROM devices WHERE id=?", (device["id"],)).fetchone()
        status = {"checkedAt": main.now(), "peers": [{"publicKey": row["wg_public_key"], "latestHandshake": main.now() - 5, "receivedBytes": 1234, "sentBytes": 5678}]}
        with patch("app.main.vpnctl_json", return_value=status):
            response, body = request("/api/devices", cookie=cookie)
        listed = next(item for item in json.loads(body)["devices"] if item["id"] == device["id"])
        self.assertEqual(listed["connection"]["state"], "connected")
        self.assertEqual(listed["connection"]["clientToServerBytes"], 1234)
        self.assertEqual(listed["connection"]["serverToClientBytes"], 5678)

    def test_protocol_requires_local_listener_before_enable(self):
        _protocol_file.write_text(json.dumps({"nodes": [{"id": "hysteria2", "enabled": False, "config": {"name": "HK Hysteria2", "type": "hysteria2", "server": "vpn.test", "port": 8443, "service": "hysteria-server.service", "password": "real-password", "sni": "vpn.test"}}]}))
        response, _ = request("/login", "POST", b"password=test-password")
        cookie = response["headers"]["Set-Cookie"].split(";", 1)[0]
        no_listener = {"udpListeningPorts": [], "tcpListeningPorts": []}
        with patch("app.main.vpnctl_json", return_value=no_listener):
            response, _ = request("/api/protocols/hysteria2", "PATCH", {"enabled": True}, cookie)
        self.assertTrue(response["status"].startswith("409"))
        listener = {"udpListeningPorts": [8443], "tcpListeningPorts": [], "services": {"hysteria-server.service": True}}
        with patch("app.main.vpnctl_json", return_value=listener):
            response, _ = request("/api/protocols/hysteria2", "PATCH", {"enabled": True}, cookie)
        self.assertTrue(response["status"].startswith("200"))

    def test_wireguard_dump_parser_keeps_counter_directions(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import vpnctl
        dump = "private\tpublic\t51820\t0\npeer-key\t(none)\t198.51.100.2:4567\t10.88.0.2/32\t123\t456\t789\t25\n"
        port, peers = vpnctl.parse_wireguard_dump(dump)
        self.assertEqual(port, 51820)
        self.assertEqual(peers, [{"publicKey": "peer-key", "latestHandshake": 123, "receivedBytes": 456, "sentBytes": 789}])
        self.assertEqual(vpnctl.listening_ports("UNCONN 0 0 0.0.0.0:51820 0.0.0.0:*\n"), {51820})

    def test_traffic_history_records_deltas_and_openvpn_is_not_emitted(self):
        response, _ = request("/login", "POST", b"password=test-password")
        cookie = response["headers"]["Set-Cookie"].split(";", 1)[0]
        response, body = request("/api/devices", "POST", {"name": "流量测试", "platform": "FLClash"}, cookie)
        device = json.loads(body)["device"]
        row = main.database().execute("SELECT * FROM devices WHERE id=?", (device["id"],)).fetchone()
        stamp = main.now() - 61
        first = {"wireguardInterface": True, "peers": [{"publicKey": row["wg_public_key"], "latestHandshake": stamp, "receivedBytes": 100, "sentBytes": 200}]}
        second = {"wireguardInterface": True, "peers": [{"publicKey": row["wg_public_key"], "latestHandshake": stamp + 61, "receivedBytes": 450, "sentBytes": 900}]}
        main.record_traffic_samples(first, stamp)
        main.record_traffic_samples(second, stamp + 61)
        with patch("app.main.vpnctl_json", return_value=second):
            response, body = request(f"/api/devices/{device['id']}/traffic?range=24h", cookie=cookie)
        history = json.loads(body)
        self.assertGreaterEqual(history["clientToServerBytes"], 350)
        self.assertGreaterEqual(history["serverToClientBytes"], 700)
        self.assertEqual(main.protocol_ready({"config": {"type": "openvpn", "server": "vpn.test", "port": 1194, "service": "openvpn.service", "ca": "cert", "username": "user", "password": "pass"}})[0], False)


if __name__ == "__main__":
    unittest.main()
