"""Maintenance diagnostic logging contracts (no live servo or network required)."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from linux.servo_maintenance_tcp_bridge import BridgeConfig, MaintenanceBridge
from linux.servo_maintenance_web import MaintenanceRequestHandler


class MaintenanceLoggingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.config = BridgeConfig(
            shared_token="secret-do-not-log",
            allowed_client_ip="127.0.0.1",
            unix_socket="/tmp/test-maintenance.sock",
            spool_dir=self.temp.name,
        )

    def request(self, bridge, command="status", **extra):
        head = dict(
            protocol="SMT1", token="secret-do-not-log", command=command,
            axis=-1, filename="", payload_length=0, target="", confirmation=""
        )
        head.update(extra)
        return bridge.handle_request(head, b"", "127.0.0.1")

    def test_bridge_start_finish_and_no_secret(self):
        bridge = MaintenanceBridge(
            self.config, command_sender=lambda command: "AMC CONNECTED\n"
        )
        with self.assertLogs("servo_maintenance_tcp_bridge", level="INFO") as captured:
            result = self.request(bridge)
        self.assertTrue(result["ok"])
        logs = "\n".join(captured.output)
        self.assertIn("BRIDGE_START", logs)
        self.assertIn("BRIDGE_AMC_CALL", logs)
        self.assertIn("BRIDGE_END", logs)
        self.assertNotIn("secret-do-not-log", logs)

    def test_rejection_and_busy_are_logged(self):
        started = threading.Event()
        release = threading.Event()

        def sender(command):
            started.set()
            release.wait(2)
            return "AMC CONNECTED\n"

        bridge = MaintenanceBridge(self.config, command_sender=sender)
        head = dict(protocol="SMT1", command="status", axis=-1,
                    payload_length=0, token="secret-do-not-log")
        with self.assertLogs("servo_maintenance_tcp_bridge", level="INFO") as captured:
            rejected = bridge.handle_request(
                dict(head, token="wrong"), b"", "127.0.0.1"
            )
            t = threading.Thread(
                target=bridge.handle_request,
                args=(head, b"", "127.0.0.1"))
            t.start()
            try:
                self.assertTrue(started.wait(2))
                busy = bridge.handle_request(head, b"", "127.0.0.1")
            finally:
                release.set()
                t.join(2)
        self.assertFalse(rejected["ok"])
        self.assertFalse(busy["ok"])
        logs = "\n".join(captured.output)
        self.assertIn("BRIDGE_REJECT", logs)
        self.assertIn("BRIDGE_BUSY", logs)
        self.assertNotIn("secret-do-not-log", logs)

    def test_parameter_read_row_failure_is_not_reported_as_success(self):
        bridge = MaintenanceBridge(
            self.config,
            command_sender=lambda command: "PARAM_READ Axis=0\nitem = READ_FAIL\n",
        )
        # Direct call with a real payload to keep file-validation exercised.
        head = dict(protocol="SMT1", command="parameter_read", axis=0,
                    filename="params.txt", payload_length=4, token="secret-do-not-log")
        with self.assertLogs("servo_maintenance_tcp_bridge", level="WARNING") as captured:
            response = bridge.handle_request(head, b"data", "127.0.0.1")
        self.assertFalse(response["ok"])
        self.assertTrue(any("BRIDGE_PARAM_PARTIAL_FAILURE" in x for x in captured.output))

    def test_web_logs_outcome_without_confirmation_or_file_data(self):
        handler = object.__new__(MaintenanceRequestHandler)
        handler.client_address = ("127.0.0.1", 1234)
        handler.server = SimpleNamespace(
            application=SimpleNamespace(
                request=lambda *args: {"ok": False, "message": "ERROR FOE_4AXIS_NOT_VALIDATED\n"}
            )
        )
        responses = []
        handler._send_json = lambda code, body, plain=False: responses.append((code, body))
        with self.assertLogs("servo_maintenance_web", level="INFO") as captured:
            handler._run_command(
                "firmware_update", -1, b"PRIVATE_BIN", "servo.bin", "slide",
                "FIRMWARE_UPDATE", True
            )
        self.assertEqual(responses[0][0], 409)
        logs = "\n".join(captured.output)
        self.assertIn("WEB_START", logs)
        self.assertIn("WEB_END", logs)
        self.assertNotIn("PRIVATE_BIN", logs)
        self.assertNotIn("FIRMWARE_UPDATE", logs)


if __name__ == "__main__":
    unittest.main()
