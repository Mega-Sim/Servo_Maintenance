import unittest
import subprocess
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class ControllerDiffTests(unittest.TestCase):
    def test_controller_diff_adds_internal_ecmaster_foe_path(self):
        text = (PACKAGE_ROOT / "changes" / "0005-ecmaster-foe-firmware-update.diff").read_text(encoding="utf-8")
        self.assertIn("prepareFirmwareUpdate", text)
        self.assertIn('cmd == "FIRMWARE_UPDATE"', text)
        self.assertIn("disableMotion()", text)
        self.assertIn("RoboticModule::disableServo(0)", text)
        self.assertIn("emFoeFileDownload", text)
        self.assertIn("DEVICE_STATE_BOOTSTRAP", text)
        self.assertIn("0x01020304", text)
        self.assertIn("FIRMWARE_DOWNLOAD", text)

    def test_firmware_version_diff_uses_existing_sdo_object(self):
        text = (PACKAGE_ROOT / "changes" / "0005-ecmaster-foe-firmware-update.diff").read_text(encoding="utf-8")
        self.assertIn('cmd == "FW_VERSION"', text)
        self.assertIn("0x100A", text)
        self.assertIn("return axis >= 0 && axis < TOTAL_AXIS_NUM", text)


class LinuxBridgeCompatibilityTests(unittest.TestCase):
    def test_linux_bridge_does_not_require_dataclasses(self):
        text = (PACKAGE_ROOT / "linux" / "servo_maintenance_tcp_bridge.py").read_text(encoding="utf-8")
        self.assertNotIn("from dataclasses import", text)
        self.assertNotIn("@dataclass", text)


class LinuxHostedLegacyBrowserTests(unittest.TestCase):
    def test_embedded_script_parses_and_uses_legacy_compatible_requests(self):
        from linux.servo_maintenance_web import INDEX_HTML

        script = INDEX_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
        checked = subprocess.run(["node", "--check"], input=script, text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertNotIn("async function", script)
        self.assertNotIn("await ", script)
        self.assertNotIn("fetch(", script)
        self.assertIn("XMLHttpRequest", script)
        self.assertIn("/api/firmware-update", script)
        self.assertIn("FIRMWARE_UPDATE", script)


class LinuxFirmwareIsolationTests(unittest.TestCase):
    def test_linux_web_source_does_not_execute_updater_or_ethercat(self):
        text = (PACKAGE_ROOT / "linux" / "servo_maintenance_web.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", text)
        self.assertNotIn("firm_update /f", text)
        self.assertNotIn("ecatFoe", text)

    def test_linux_bridge_forwards_firmware_to_amc_without_external_soem_process(self):
        text = (PACKAGE_ROOT / "linux" / "servo_maintenance_tcp_bridge.py").read_text(encoding="utf-8")
        self.assertIn("FIRMWARE_UPDATE {axis} {path}", text)
        self.assertNotIn("subprocess", text)
        self.assertNotIn("firm_update", text)


class DeploymentContractTests(unittest.TestCase):
    def test_windows_contains_only_browser_shortcut_and_instructions(self):
        names = {path.name for path in (PACKAGE_ROOT / "windows").iterdir() if path.is_file()}
        self.assertEqual(names, {"Servo_Maintenance.url", "README_KO.txt"})
        shortcut = (PACKAGE_ROOT / "windows" / "Servo_Maintenance.url").read_text(encoding="utf-8")
        self.assertIn("URL=http://192.168.0.2:18765/", shortcut)

    def test_linux_web_is_python35_compatible_and_keeps_latest_ui(self):
        web = (PACKAGE_ROOT / "linux" / "servo_maintenance_web.py").read_text(encoding="utf-8")
        self.assertNotIn("from dataclasses import", web)
        self.assertNotIn("ThreadingHTTPServer", web)
        self.assertIn("class ThreadedHTTPServer", web)
        self.assertIn("Firmware Update (EC-Master FoE)", web)
        self.assertIn("Windows IPC → Linux motion controller → Servo", web)

    def test_linux_exposes_only_web_port_and_persists_uploads_in_work_bin(self):
        config = (PACKAGE_ROOT / "linux" / "servo_maintenance.ini").read_text(encoding="utf-8")
        self.assertIn("listen_host = 127.0.0.1", config)
        self.assertIn("spool_dir = /home/semes/work/bin", config)
        self.assertIn("motion_ip = 127.0.0.1", config)
        self.assertIn("host = 0.0.0.0", config)
        self.assertIn("port = 18765", config)

    def test_controller_patch_contains_every_file_touched_by_diff(self):
        expected = {
            "src/app/amc/amc_oht.cpp",
            "src/control/axes_module.cpp",
            "src/control/axes_module.h",
            "src/control/ec_servo_motor.cpp",
            "src/control/ec_servo_motor.h",
            "src/ecmaster/ecat_data_struct.h",
            "src/ecmaster/ecmaster_message_handler.cpp",
            "src/ecmaster/ecmaster_service.cpp",
            "src/ecmaster/ecmaster_service.h",
            "src/ecmaster/ecmaster_servo_test.cpp",
        }
        actual = {
            str(path.relative_to(PACKAGE_ROOT / "controller_patch"))
            for path in (PACKAGE_ROOT / "controller_patch").rglob("*")
            if path.is_file()
        }
        self.assertEqual(actual, expected)
