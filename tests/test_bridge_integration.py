import sys
import tempfile
import threading
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from linux.servo_maintenance_tcp_bridge import BridgeConfig, MaintenanceBridge  # noqa: E402
from linux.servo_maintenance_tcp_bridge import BridgeTCPServer  # noqa: E402
from common.maintenance_protocol import RemoteMaintenanceClient  # noqa: E402


class MaintenanceBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.unix_path = str(root / "amc.sock")
        self.spool_dir = root / "spool"
        self.commands = []
        self.payloads = []

        def command_sender(command):
            self.commands.append(command)
            pieces = command.split(maxsplit=2)
            if len(pieces) == 3 and pieces[0] in {"PARAM_READ", "PARAM_UPDATE"}:
                self.payloads.append(Path(pieces[2]).read_bytes())
            if command.startswith("STATUS"):
                return "AMC CONNECTED\n"
            if command.startswith("FW_VERSION"):
                return "FW_VERSION Axis=1 1.2.3\n"
            if command.startswith("PARAM_READ"):
                return "PARAM_READ Axis=1\nGain=3\n"
            return "OK " + pieces[0] + "\n"

        self.bridge = MaintenanceBridge(
            BridgeConfig(
                shared_token="test-token",
                allowed_client_ip="192.168.10.10",
                unix_socket=self.unix_path,
                spool_dir=str(self.spool_dir),
                max_file_bytes=1024,
                unix_timeout_seconds=2,
            ),
            command_sender=command_sender,
        )

    def tearDown(self):
        self.temp.cleanup()

    def request(self, command, axis=-1, payload=b"", filename="", target="", confirmation=""):
        return self.bridge.handle_request(
            {
                "protocol": "SMT1",
                "token": "test-token",
                "command": command,
                "axis": axis,
                "payload_length": len(payload),
                "filename": filename,
                "target": target,
                "confirmation": confirmation,
            },
            payload,
            "192.168.10.10",
        )

    def test_status_forwards_to_existing_unix_socket(self):
        response = self.request("status")
        self.assertTrue(response["ok"])
        self.assertEqual(response["message"], "AMC CONNECTED\n")
        self.assertEqual(self.commands, ["STATUS"])

    def test_parameter_read_keeps_uploaded_file_in_work_bin_with_original_name(self):
        response = self.request("parameter_read", axis=1, payload=b"Gain,,,,\n", filename="gain.txt")
        self.assertTrue(response["ok"])
        self.assertEqual(self.commands[0].split(maxsplit=2)[0:2], ["PARAM_READ", "1"])
        self.assertEqual(self.payloads, [b"Gain,,,,\n"])
        self.assertEqual((self.spool_dir / "gain.txt").read_bytes(), b"Gain,,,,\n")

    def test_different_content_cannot_overwrite_existing_uploaded_file(self):
        self.spool_dir.mkdir(parents=True)
        (self.spool_dir / "gain.txt").write_bytes(b"original")
        response = self.request("parameter_update", axis=1, payload=b"changed", filename="gain.txt")
        self.assertFalse(response["ok"])
        self.assertIn("already exists", response["message"])
        self.assertEqual((self.spool_dir / "gain.txt").read_bytes(), b"original")

    def test_rejects_wrong_token_without_calling_unix_socket(self):
        response = self.bridge.handle_request(
            {"protocol": "SMT1", "token": "wrong", "command": "status", "axis": -1, "payload_length": 0},
            b"",
            "192.168.10.10",
        )
        self.assertFalse(response["ok"])
        self.assertIn("authentication", response["message"])
        self.assertEqual(self.commands, [])

    def test_firmware_update_is_saved_then_forwarded_to_amc_ecmaster_path(self):
        response = self.bridge.handle_request(
            {
                "protocol": "SMT1",
                "token": "test-token",
                "command": "firmware_update",
                "axis": -1,
                "payload_length": 4,
                "filename": "hoist.bin",
                "target": "hoist",
                "confirmation": "FIRMWARE_UPDATE",
            },
            b"\x01\x02\x03\x04",
            "192.168.10.10",
        )
        self.assertTrue(response["ok"])
        stored = self.spool_dir / "hoist.bin"
        self.assertEqual(stored.read_bytes(), b"\x01\x02\x03\x04")
        self.assertEqual(self.commands, ["FIRMWARE_UPDATE 2 {}".format(stored)])

    def test_firmware_update_rejects_missing_confirmation_before_execution(self):
        response = self.request(
            "firmware_update", payload=b"fw", filename="drive.bin", target="driving", confirmation="WRONG"
        )
        self.assertFalse(response["ok"])
        self.assertIn("confirmation", response["message"])

    def test_rejects_unapproved_ip_and_invalid_axis(self):
        denied = self.bridge.handle_request(
            {"protocol": "SMT1", "token": "test-token", "command": "status", "axis": -1, "payload_length": 0},
            b"",
            "192.168.10.11",
        )
        invalid_axis = self.request("absolute_reset", axis=4)
        self.assertFalse(denied["ok"])
        self.assertIn("client IP", denied["message"])
        self.assertFalse(invalid_axis["ok"])
        self.assertIn("axis", invalid_axis["message"])
        self.assertEqual(self.commands, [])

    def test_tcp_server_round_trip_uses_framed_protocol(self):
        commands = []

        def command_sender(command):
            commands.append(command)
            return "AMC CONNECTED\n"

        config = BridgeConfig(
            shared_token="test-token",
            allowed_client_ip="127.0.0.1",
            unix_socket=self.unix_path,
            spool_dir=str(self.spool_dir),
            max_file_bytes=1024,
            unix_timeout_seconds=2,
        )
        server = BridgeTCPServer(("127.0.0.1", 0), MaintenanceBridge(config, command_sender=command_sender))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            host, port = server.server_address
            response = RemoteMaintenanceClient(host, port, "test-token", timeout=2).request("status")
            self.assertTrue(response["ok"])
            self.assertEqual(response["message"], "AMC CONNECTED\n")
            self.assertEqual(commands, ["STATUS"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)



class BridgeSafetyTests(unittest.TestCase):
    def make_bridge(self, command_sender):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = BridgeConfig(
            shared_token="test-token",
            allowed_client_ip="127.0.0.1",
            unix_socket="/tmp/unused.sock",
            spool_dir=self.temp.name,
        )
        return MaintenanceBridge(config, command_sender=command_sender)

    def header(self, **values):
        header = {"protocol": "SMT1", "token": "test-token", "command": "status", "axis": -1, "payload_length": 0}
        header.update(values)
        return header

    def test_second_request_is_rejected_while_one_is_running(self):
        started = threading.Event()
        release = threading.Event()

        def slow_sender(command):
            started.set()
            release.wait(2)
            return "AMC CONNECTED\n"

        bridge = self.make_bridge(slow_sender)
        worker = threading.Thread(target=bridge.handle_request, args=(self.header(), b"", "127.0.0.1"))
        worker.start()
        self.assertTrue(started.wait(2))
        busy = bridge.handle_request(self.header(), b"", "127.0.0.1")
        release.set()
        worker.join(2)
        self.assertFalse(busy["ok"])
        self.assertIn("another maintenance operation", busy["message"])

    def test_boolean_axis_and_non_ascii_token_are_rejected(self):
        commands = []
        bridge = self.make_bridge(lambda command: commands.append(command) or "OK")
        as_bool = bridge.handle_request(self.header(command="absolute_reset", axis=True), b"", "127.0.0.1")
        korean = bridge.handle_request(self.header(token="토큰"), b"", "127.0.0.1")
        self.assertIn("axis", as_bool["message"])
        self.assertIn("authentication", korean["message"])
        self.assertEqual(commands, [])

    def test_windows_full_path_is_stored_by_base_name(self):
        commands = []
        bridge = self.make_bridge(lambda command: commands.append(command) or "PARAM_READ Axis=1\n")
        response = bridge.handle_request(
            self.header(command="parameter_read", axis=1, payload_length=3, filename="C:\\work\\gain.txt"),
            b"abc",
            "127.0.0.1",
        )
        self.assertTrue(response["ok"])
        self.assertEqual(commands, ["PARAM_READ 1 {}".format(Path(self.temp.name) / "gain.txt")])
