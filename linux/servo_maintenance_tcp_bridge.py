#!/usr/bin/env python3
"""Authenticated loopback bridge from the Linux web UI to AMC maintenance."""

import argparse
import configparser
import hmac
import json
import logging
import os
import re
import socket
import socketserver
import sys
import tempfile
import threading
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from common.maintenance_protocol import (  # noqa: E402
    DEFAULT_MAX_FIRMWARE_BYTES,
    DEFAULT_MAX_FILE_BYTES,
    FIRMWARE_CONFIRMATION,
    HEADER_MAX_BYTES,
    PROTOCOL_NAME,
    ProtocolError,
    decode_json,
    recv_exact,
    recv_frame,
    send_frame,
)


LOG = logging.getLogger("servo_maintenance_tcp_bridge")
PLACEHOLDER_TOKENS = {"", "CHANGE_ME", "CHANGE_ME_TO_A_LONG_RANDOM_TOKEN"}
ALLOWED_COMMANDS = {
    "status": "STATUS",
    "firmware_version": "FW_VERSION {axis}",
    "absolute_reset": "ABS_RESET {axis}",
}
FILE_COMMANDS = {
    "parameter_read": "PARAM_READ {axis} {path}",
    "parameter_update": "PARAM_UPDATE {axis} {path}",
}
FIRMWARE_COMMAND = "FIRMWARE_UPDATE {axis} {path}"
SUCCESS_PREFIXES = {
    "status": "AMC CONNECTED",
    "firmware_version": "FW_VERSION Axis=",
    "absolute_reset": "OK ABS_RESET",
    "parameter_read": "PARAM_READ Axis=",
    "parameter_update": "OK PARAM_UPDATE",
    "firmware_update": "OK FIRMWARE_UPDATE",
}


class RequestRejected(Exception):
    """Expected request rejection that can be returned safely to the client."""


class BridgeConfig:
    """Bridge settings kept compatible with Python 3.5 and newer."""

    def __init__(
        self,
        shared_token,
        allowed_client_ip,
        unix_socket,
        spool_dir,
        max_file_bytes=DEFAULT_MAX_FILE_BYTES,
        unix_timeout_seconds=120.0,
        listen_host="0.0.0.0",
        listen_port=18766,
        firmware_max_file_bytes=DEFAULT_MAX_FIRMWARE_BYTES,
        firmware_targets=None,
    ):
        self.shared_token = shared_token
        self.allowed_client_ip = allowed_client_ip
        self.unix_socket = unix_socket
        self.spool_dir = spool_dir
        self.max_file_bytes = max_file_bytes
        self.unix_timeout_seconds = unix_timeout_seconds
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.firmware_max_file_bytes = int(firmware_max_file_bytes)
        self.firmware_targets = firmware_targets or {"driving": 0, "hoist": 2, "slide": 3}

    def validate(self):
        if self.shared_token in PLACEHOLDER_TOKENS or len(self.shared_token) < 16:
            raise ValueError("shared_token must be set to a non-placeholder value of at least 16 characters")
        if not self.allowed_client_ip:
            raise ValueError("allowed_client_ip must be configured")
        if not self.unix_socket.startswith("/"):
            raise ValueError("unix_socket must be an absolute path")
        if self.max_file_bytes < 1 or self.max_file_bytes > 16 * 1024 * 1024:
            raise ValueError("max_file_bytes must be between 1 and 16777216")
        if self.listen_port < 1 or self.listen_port > 65535:
            raise ValueError("listen_port must be between 1 and 65535")
        if self.firmware_max_file_bytes < 1 or self.firmware_max_file_bytes > 8 * 1024 * 1024:
            raise ValueError("firmware_max_file_bytes must be between 1 and 8388608")
        if set(self.firmware_targets) != {"driving", "hoist", "slide"}:
            raise ValueError("firmware targets must be driving, hoist, and slide")
        if any(not isinstance(value, int) or value < 0 or value > 3 for value in self.firmware_targets.values()):
            raise ValueError("firmware target axes must be integers from 0 to 3")


def load_config(path):
    parser = configparser.ConfigParser()
    loaded = parser.read(path, encoding="utf-8")
    if not loaded or "server" not in parser:
        raise ValueError("configuration file or [server] section not found: {}".format(path))
    server = parser["server"]
    firmware = parser["firmware"] if "firmware" in parser else {}
    config = BridgeConfig(
        shared_token=server.get("shared_token", "").strip(),
        allowed_client_ip=server.get("allowed_client_ip", "").strip(),
        unix_socket=server.get("unix_socket", "/tmp/amc_rsa_maintenance.sock").strip(),
        spool_dir=server.get("spool_dir", "/tmp/servo_maintenance_uploads").strip(),
        max_file_bytes=server.getint("max_file_bytes", fallback=DEFAULT_MAX_FILE_BYTES),
        unix_timeout_seconds=server.getfloat("unix_timeout_seconds", fallback=120.0),
        listen_host=server.get("listen_host", "0.0.0.0").strip(),
        listen_port=server.getint("listen_port", fallback=18766),
        firmware_max_file_bytes=firmware.getint(
            "max_file_bytes", fallback=DEFAULT_MAX_FIRMWARE_BYTES
        ) if firmware else DEFAULT_MAX_FIRMWARE_BYTES,
        firmware_targets={
            "driving": firmware.getint("driving_axis", fallback=0) if firmware else 0,
            "hoist": firmware.getint("hoist_axis", fallback=2) if firmware else 2,
            "slide": firmware.getint("slide_axis", fallback=3) if firmware else 3,
        },
    )
    config.validate()
    return config


class MaintenanceBridge:
    """Validates a TCP request then invokes exactly one local AMC command."""

    def __init__(self, config, command_sender=None):
        self.config = config
        self._operation_lock = threading.Lock()
        self._command_sender = command_sender or self._send_unix_command

    def handle_request(self, header, payload, peer_ip):
        try:
            command, axis, target = self._validate_request(header, payload, peer_ip)
            with self._operation_lock:
                return self._execute(command, axis, target, header.get("filename", ""), payload)
        except RequestRejected as error:
            return {"ok": False, "message": "ERROR: {}".format(error)}
        except OSError as error:
            LOG.exception("controller socket failure")
            return {"ok": False, "message": "ERROR: controller socket failure: {}".format(error)}
        except Exception as error:  # Keep TCP worker alive while reporting unexpected failures.
            LOG.exception("unexpected maintenance bridge failure")
            return {"ok": False, "message": "ERROR: bridge failure: {}".format(error)}

    def _validate_request(self, header, payload, peer_ip):
        if not isinstance(header, dict):
            raise RequestRejected("request header must be a JSON object")
        if header.get("protocol") != PROTOCOL_NAME:
            raise RequestRejected("unsupported protocol")
        token = header.get("token")
        if not isinstance(token, str) or not hmac.compare_digest(token, self.config.shared_token):
            raise RequestRejected("authentication failed")
        if self.config.allowed_client_ip != "*" and peer_ip != self.config.allowed_client_ip:
            raise RequestRejected("client IP is not allowed")

        command = header.get("command")
        if command not in set(ALLOWED_COMMANDS) | set(FILE_COMMANDS) | {"firmware_update"}:
            raise RequestRejected("unsupported command")
        payload_length = header.get("payload_length")
        if not isinstance(payload_length, int) or payload_length < 0:
            raise RequestRejected("payload_length must be a non-negative integer")
        if payload_length != len(payload):
            raise RequestRejected("payload length does not match header")
        request_limit = (
            self.config.firmware_max_file_bytes if command == "firmware_update" else self.config.max_file_bytes
        )
        if payload_length > request_limit:
            raise RequestRejected("uploaded file exceeds configured size limit")

        axis = header.get("axis")
        if command == "status":
            if axis != -1 or payload:
                raise RequestRejected("status requires axis -1 and no payload")
            return command, axis, ""
        if command == "firmware_update":
            if axis != -1:
                raise RequestRejected("firmware_update requires axis -1")
            target = header.get("target")
            if target not in self.config.firmware_targets:
                raise RequestRejected("firmware target is not configured")
            if header.get("confirmation") != FIRMWARE_CONFIRMATION:
                raise RequestRejected("firmware confirmation is invalid")
            filename = header.get("filename")
            if not isinstance(filename, str) or not filename.lower().endswith(".bin"):
                raise RequestRejected("firmware filename must end with .bin")
            if not payload:
                raise RequestRejected("firmware file payload is empty")
            self._safe_filename(filename)
            return command, axis, target
        if not isinstance(axis, int) or axis < 0 or axis > 3:
            raise RequestRejected("axis must be an integer from 0 to 3")
        if command in FILE_COMMANDS:
            filename = header.get("filename")
            if not isinstance(filename, str) or not filename:
                raise RequestRejected("parameter command requires a filename")
            if not payload:
                raise RequestRejected("parameter file payload is empty")
        elif payload:
            raise RequestRejected("payload is only valid for parameter commands")
        return command, axis, ""

    def _execute(self, command, axis, target, filename, payload):
        if command == "firmware_update":
            stored_path = self._save_uploaded_file(filename, payload)
            firmware_axis = self.config.firmware_targets[target]
            result = self._command_sender(
                FIRMWARE_COMMAND.format(axis=firmware_axis, path=stored_path)
            )
        elif command in FILE_COMMANDS:
            stored_path = self._save_uploaded_file(filename, payload)
            unix_command = FILE_COMMANDS[command].format(axis=axis, path=stored_path)
            result = self._command_sender(unix_command)
        else:
            unix_command = ALLOWED_COMMANDS[command].format(axis=axis)
            result = self._command_sender(unix_command)
        ok = result.startswith(SUCCESS_PREFIXES[command])
        return {"ok": ok, "message": result}

    def _save_uploaded_file(self, filename, payload):
        safe_name = self._safe_filename(filename)
        spool = Path(self.config.spool_dir)
        spool.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = spool / safe_name
        if destination.exists():
            if destination.is_file() and destination.read_bytes() == payload:
                return str(destination)
            raise RequestRejected("uploaded file already exists with different content: {}".format(safe_name))
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=".servo_upload_", dir=str(spool), delete=False
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = handle.name
        try:
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, str(destination))
        except Exception:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
            raise
        return str(destination)

    @staticmethod
    def _safe_filename(filename):
        basename = os.path.basename(filename.replace("\\", "/"))
        if not basename or basename in {".", ".."}:
            raise RequestRejected("invalid parameter filename")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", basename):
            raise RequestRejected("parameter filename contains unsupported characters")
        return basename

    def _send_unix_command(self, command):
        if len(command.encode("utf-8")) >= 1024:
            raise RequestRejected("controller command is too long")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as unix_sock:
            unix_sock.settimeout(self.config.unix_timeout_seconds)
            unix_sock.connect(self.config.unix_socket)
            unix_sock.sendall(command.encode("utf-8"))
            unix_sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = unix_sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        if not chunks:
            raise RequestRejected("controller returned no response")
        return b"".join(chunks).decode("utf-8", errors="replace")


class BridgeRequestHandler(socketserver.BaseRequestHandler):
    def handle(self):
        response = None
        try:
            header = decode_json(recv_frame(self.request, HEADER_MAX_BYTES), "request header")
            payload_length = header.get("payload_length")
            if not isinstance(payload_length, int) or payload_length < 0:
                raise ProtocolError("payload_length must be a non-negative integer")
            max_payload = max(
                self.server.bridge.config.max_file_bytes,
                self.server.bridge.config.firmware_max_file_bytes,
            )
            if payload_length > max_payload:
                raise ProtocolError("payload too large")
            payload = recv_exact(self.request, payload_length)
            response = self.server.bridge.handle_request(header, payload, self.client_address[0])
        except (ProtocolError, json.JSONDecodeError, UnicodeDecodeError) as error:
            response = {"ok": False, "message": "ERROR: invalid request: {}".format(error)}
        except OSError as error:
            response = {"ok": False, "message": "ERROR: network failure: {}".format(error)}
        try:
            send_frame(self.request, json.dumps(response, ensure_ascii=False).encode("utf-8"))
        except OSError:
            LOG.warning("response could not be sent to %s", self.client_address[0])


class BridgeTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, bridge):
        self.bridge = bridge
        super().__init__(address, BridgeRequestHandler)


def serve(config):
    config.validate()
    with BridgeTCPServer((config.listen_host, config.listen_port), MaintenanceBridge(config)) as server:
        LOG.info(
            "listening on %s:%d for allowed client %s; unix socket=%s",
            config.listen_host,
            config.listen_port,
            config.allowed_client_ip,
            config.unix_socket,
        )
        server.serve_forever(poll_interval=0.5)


def main():
    default_config = Path(__file__).with_name("servo_maintenance.ini")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(default_config), help="path to INI configuration")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")
    try:
        serve(load_config(args.config))
    except (ValueError, OSError) as error:
        LOG.error("bridge did not start: %s", error)
        return 2
    except KeyboardInterrupt:
        LOG.info("bridge stopped")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
