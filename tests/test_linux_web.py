import json
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from linux.servo_maintenance_web import MaintenanceHTTPServer  # noqa: E402


class FakeApplication:
    def __init__(self):
        self.config = SimpleNamespace(
            max_file_bytes=1024,
            max_firmware_bytes=8 * 1024 * 1024,
            allowed_browser_ip="127.0.0.1",
        )
        self.calls = []

    def request(self, command, axis=-1, payload=b"", filename="", target="", confirmation=""):
        self.calls.append((command, axis, payload, filename, target, confirmation))
        return {"ok": True, "message": "OK {}\n".format(command)}


class WebServerTestCase(unittest.TestCase):
    def setUp(self):
        self.application = FakeApplication()
        self.server = MaintenanceHTTPServer(("127.0.0.1", 0), self.application)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        host, port = self.server.server_address
        self.base_url = "http://{}:{}".format(host, port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)


class LinuxWebUiTests(WebServerTestCase):
    def test_status_endpoint_calls_remote_application(self):
        with urlopen(self.base_url + "/api/status", timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
        self.assertTrue(body["ok"])
        self.assertEqual(body["message"], "OK status\n")
        self.assertEqual(self.application.calls, [("status", -1, b"", "", "", "")])

    def test_absolute_reset_endpoint_parses_axis(self):
        request = Request(
            self.base_url + "/api/absolute-reset",
            data=json.dumps({"axis": 3}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            body = json.loads(response.read().decode("utf-8"))
        self.assertTrue(body["ok"])
        self.assertEqual(self.application.calls, [("absolute_reset", 3, b"", "", "", "")])

    def test_parameter_read_endpoint_forwards_uploaded_file_bytes(self):
        boundary = "----servo-maintenance-test-boundary"
        payload = b"Gain,,,,\n"
        body = (
            ("--{}\r\n".format(boundary)).encode("ascii")
            + b'Content-Disposition: form-data; name="axis"\r\n\r\n'
            + b"1\r\n"
            + ("--{}\r\n".format(boundary)).encode("ascii")
            + b'Content-Disposition: form-data; name="file"; filename="gain.txt"\r\n'
            + b"Content-Type: text/plain\r\n\r\n"
            + payload
            + b"\r\n"
            + ("--{}--\r\n".format(boundary)).encode("ascii")
        )
        request = Request(
            self.base_url + "/api/parameter-read",
            data=body,
            headers={"Content-Type": "multipart/form-data; boundary={}".format(boundary)},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.headers.get_content_type(), "text/plain")
            body_json = json.loads(response.read().decode("utf-8"))
        self.assertTrue(body_json["ok"])
        self.assertEqual(self.application.calls, [("parameter_read", 1, payload, "gain.txt", "", "")])

    def test_firmware_update_endpoint_forwards_file_target_and_confirmation(self):
        boundary = "----servo-firmware-test-boundary"
        payload = b"firmware-binary"
        fields = [
            ("target", "slide"),
            ("confirmation", "FIRMWARE_UPDATE"),
        ]
        parts = []
        for name, value in fields:
            parts.append(("--{}\r\n".format(boundary)).encode("ascii"))
            parts.append(('Content-Disposition: form-data; name="{}"\r\n\r\n'.format(name)).encode("ascii"))
            parts.append(value.encode("ascii") + b"\r\n")
        parts.append(("--{}\r\n".format(boundary)).encode("ascii"))
        parts.append(b'Content-Disposition: form-data; name="file"; filename="slide.bin"\r\n')
        parts.append(b"Content-Type: application/octet-stream\r\n\r\n")
        parts.append(payload + b"\r\n")
        parts.append(("--{}--\r\n".format(boundary)).encode("ascii"))
        request = Request(
            self.base_url + "/api/firmware-update",
            data=b"".join(parts),
            headers={"Content-Type": "multipart/form-data; boundary={}".format(boundary)},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            body_json = json.loads(response.read().decode("utf-8"))
        self.assertTrue(body_json["ok"])
        self.assertEqual(
            self.application.calls,
            [("firmware_update", -1, payload, "slide.bin", "slide", "FIRMWARE_UPDATE")],
        )


class LinuxWebSafetyTests(WebServerTestCase):
    def post(self, path, data, content_type, extra_headers=None):
        headers = {"Content-Type": content_type}
        headers.update(extra_headers or {})
        request = Request(self.base_url + path, data=data, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_reset_from_another_site_or_plain_form_is_rejected(self):
        body = json.dumps({"axis": 1}).encode("utf-8")
        cross_site = self.post("/api/absolute-reset", body, "application/json", {"Origin": "http://evil.example"})
        plain_form = self.post("/api/absolute-reset", body, "text/plain")
        self.assertEqual(cross_site[0], 403)
        self.assertEqual(plain_form[0], 400)
        self.assertEqual(self.application.calls, [])

    def test_firmware_binary_bytes_survive_multipart_parsing(self):
        boundary = "----boundary"
        payload = bytes(range(256)) * 4 + b"\r\n--\r\n\n\r"
        body = (
            b"--" + boundary.encode() + b"\r\n"
            b'Content-Disposition: form-data; name="target"\r\n\r\nhoist\r\n'
            b"--" + boundary.encode() + b"\r\n"
            b'Content-Disposition: form-data; name="confirmation"\r\n\r\nFIRMWARE_UPDATE\r\n'
            b"--" + boundary.encode() + b"\r\n"
            b'Content-Disposition: form-data; name="file"; filename="C:\\fw\\hoist.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n" + payload + b"\r\n"
            b"--" + boundary.encode() + b"--\r\n"
        )
        status, reply = self.post("/api/firmware-update", body, "multipart/form-data; boundary=" + boundary)
        self.assertEqual(status, 200)
        self.assertTrue(reply["ok"])
        self.assertEqual(
            self.application.calls,
            [("firmware_update", -1, payload, "C:\\fw\\hoist.bin", "hoist", "FIRMWARE_UPDATE")],
        )

    def test_invalid_firmware_version_axis_returns_400(self):
        try:
            urlopen(self.base_url + "/api/fw-version?axis=9", timeout=2)
            self.fail("expected HTTP 400")
        except HTTPError as error:
            self.assertEqual(error.code, 400)
        self.assertEqual(self.application.calls, [])
