import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["HKVPN_DATA_DIR"] = tempfile.mkdtemp(prefix="hkvpn-test-")
os.environ["HKVPN_ADMIN_PASSWORD"] = "test-password"
os.environ["HKVPN_SESSION_SECRET"] = "test-session-secret"
os.environ["HKVPN_PUBLIC_URL"] = "https://panel.test"
os.environ["HKVPN_WG_ENDPOINT"] = "vpn.test:51820"
os.environ["HKVPN_WG_PUBLIC_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
os.environ["HKVPN_CTL"] = "/helper-not-installed"

from app.main import application  # noqa: E402


def request(path, method="GET", body=None, cookie=""):
    raw = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else b"")
    received = {}
    def start_response(status, headers):
        received["status"] = status; received["headers"] = dict(headers)
    result = application({"REQUEST_METHOD": method, "PATH_INFO": path, "wsgi.input": io.BytesIO(raw), "CONTENT_LENGTH": str(len(raw)), "HTTP_COOKIE": cookie}, start_response)
    return received, b"".join(result)


class PanelTests(unittest.TestCase):
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

    def test_admin_can_create_device(self):
        response, _ = request("/login", "POST", b"password=test-password")
        cookie = response["headers"]["Set-Cookie"].split(";", 1)[0]
        response, body = request("/api/devices", "POST", {"name": "Windows", "platform": "Windows"}, cookie)
        self.assertTrue(response["status"].startswith("201"))
        device = json.loads(body)["device"]
        self.assertEqual(device["platform"], "Windows")
        response, body = request(f"/api/devices/{device['id']}/ikev2", cookie=cookie)
        self.assertTrue(response["status"].startswith("200"))
        self.assertEqual(json.loads(body)["type"], "IKEv2 / IPsec EAP-MSCHAPv2")


if __name__ == "__main__":
    unittest.main()
