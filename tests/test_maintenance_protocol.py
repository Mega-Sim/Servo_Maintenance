import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from common.maintenance_protocol import (  # noqa: E402
    DEFAULT_MAX_FIRMWARE_BYTES,
    ProtocolError,
    RemoteMaintenanceClient,
    recv_exact,
    recv_frame,
    send_frame,
    validate_client_request,
)

from linux.servo_maintenance_web import load_web_config  # noqa: E402


class FrameTests(unittest.TestCase):
    def test_frame_round_trip_over_socketpair(self):
        left, right = socket.socketpair()
        try:
            send_frame(left, b"abc")
            self.assertEqual(recv_frame(right, 16), b"abc")
        finally:
            left.close()
            right.close()

    def test_oversized_frame_is_rejected_before_payload_read(self):
        left, right = socket.socketpair()
        try:
            left.sendall((17).to_bytes(4, "big"))
            with self.assertRaisesRegex(ProtocolError, "frame too large"):
                recv_frame(right, 16)
        finally:
            left.close()
            right.close()


class RemoteClientTests(unittest.TestCase):
    def test_client_sends_header_and_raw_parameter_payload(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        received = {}

        def serve_once():
            conn, _ = listener.accept()
            with conn:
                received["header"] = json.loads(recv_frame(conn, 65536).decode("utf-8"))
                received["payload"] = recv_exact(conn, received["header"]["payload_length"])
                response = {"ok": True, "message": "PARAM_READ Axis=2\nValue=11\n"}
                send_frame(conn, json.dumps(response).encode("utf-8"))

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        try:
            host, port = listener.getsockname()
            client = RemoteMaintenanceClient(host, port, "test-token", timeout=2)
            response = client.request(
                "parameter_read", axis=2, filename="parameter.txt", payload=b"line1\n"
            )
            self.assertTrue(response["ok"])
            self.assertEqual(received["header"]["protocol"], "SMT1")
            self.assertEqual(received["header"]["token"], "test-token")
            self.assertEqual(received["header"]["command"], "parameter_read")
            self.assertEqual(received["header"]["filename"], "parameter.txt")
            self.assertEqual(received["payload"], b"line1\n")
        finally:
            listener.close()
            thread.join(timeout=1)

    def test_client_rejects_invalid_axis_before_connecting(self):
        client = RemoteMaintenanceClient("127.0.0.1", 18766, "test-token", timeout=1)
        with self.assertRaisesRegex(ValueError, "axis"):
            client.request("absolute_reset", axis=4)

    def test_client_sends_firmware_target_confirmation_and_payload(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        received = {}

        def serve_once():
            conn, _ = listener.accept()
            with conn:
                received["header"] = json.loads(recv_frame(conn, 65536).decode("utf-8"))
                received["payload"] = recv_exact(conn, received["header"]["payload_length"])
                send_frame(conn, json.dumps({"ok": True, "message": "OK FIRMWARE_UPDATE"}).encode("utf-8"))

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()
        try:
            host, port = listener.getsockname()
            client = RemoteMaintenanceClient(host, port, "test-token", timeout=2)
            response = client.request(
                "firmware_update",
                payload=b"firmware-bytes",
                filename="drive.bin",
                target="driving",
                confirmation="FIRMWARE_UPDATE",
            )
            self.assertTrue(response["ok"])
            self.assertEqual(received["header"]["axis"], -1)
            self.assertEqual(received["header"]["target"], "driving")
            self.assertEqual(received["header"]["confirmation"], "FIRMWARE_UPDATE")
            self.assertEqual(received["payload"], b"firmware-bytes")
        finally:
            listener.close()
            thread.join(timeout=1)

    def test_firmware_request_rejects_wrong_extension_confirmation_and_size(self):
        with self.assertRaisesRegex(ValueError, "bin"):
            validate_client_request(
                "firmware_update", -1, b"x", "drive.txt", target="driving", confirmation="FIRMWARE_UPDATE"
            )
        with self.assertRaisesRegex(ValueError, "confirmation"):
            validate_client_request(
                "firmware_update", -1, b"x", "drive.bin", target="driving", confirmation="UPDATE"
            )
        with self.assertRaisesRegex(ValueError, "exceeds"):
            validate_client_request(
                "firmware_update",
                -1,
                b"x" * (DEFAULT_MAX_FIRMWARE_BYTES + 1),
                "drive.bin",
                target="driving",
                confirmation="FIRMWARE_UPDATE",
            )


class LinuxWebConfigTests(unittest.TestCase):
    def test_loads_loopback_bridge_and_remote_web_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "servo_maintenance.ini"
            config_path.write_text(
                "[connection]\n"
                "motion_ip = 127.0.0.1\n"
                "tcp_port = 18766\n"
                "shared_token = test-token-123456\n"
                "timeout_seconds = 33\n"
                "\n[web_ui]\n"
                "host = 0.0.0.0\n"
                "port = 18765\n"
                "allowed_browser_ip = 192.168.0.3\n",
                encoding="utf-8",
            )
            config = load_web_config(config_path)
        self.assertEqual(config.motion_ip, "127.0.0.1")
        self.assertEqual(config.tcp_port, 18766)
        self.assertEqual(config.shared_token, "test-token-123456")
        self.assertEqual(config.timeout_seconds, 33.0)
        self.assertEqual(config.web_host, "0.0.0.0")
        self.assertEqual(config.web_port, 18765)
        self.assertEqual(config.allowed_browser_ip, "192.168.0.3")
