#!/usr/bin/env python3
"""Authenticated loopback bridge from the Linux web UI to the AMC maintenance socket."""

import argparse
import configparser
import hmac
import itertools
import logging
import os
import re
import socket
import socketserver
import sys
import tempfile
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from common.maintenance_protocol import (  # noqa: E402
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_FIRMWARE_BYTES,
    FILE_COMMANDS,
    FIRMWARE_TARGETS,
    HEADER_MAX_BYTES,
    PROTOCOL_NAME,
    RESPONSE_MAX_BYTES,
    ProtocolError,
    decode_json,
    encode_json,
    recv_exact,
    recv_frame,
    send_frame,
    validate_client_request,
)


LOG = logging.getLogger("servo_maintenance_tcp_bridge")
PLACEHOLDER_TOKENS = ("", "CHANGE_ME", "CHANGE_ME_TO_A_LONG_RANDOM_TOKEN")
DEFAULT_SPOOL_DIR = "/home/semes/work/bin"  # EC-Master only accepts firmware below this path.
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# Web command -> (AMC UNIX socket command, prefix of a successful AMC reply)
AMC_COMMANDS = {
    "status": ("STATUS", "AMC CONNECTED"),
    "firmware_version": ("FW_VERSION {axis}", "FW_VERSION Axis="),
    "absolute_reset": ("ABS_RESET {axis}", "OK ABS_RESET"),
    "parameter_read": ("PARAM_READ {axis} {path}", "PARAM_READ Axis="),
    "parameter_update": ("PARAM_UPDATE {axis} {path}", "OK PARAM_UPDATE"),
    "firmware_update": ("FIRMWARE_UPDATE {axis} {path}", "OK FIRMWARE_UPDATE"),
}


class RequestRejected(Exception):
    """Expected rejection whose message is safe to return to the client."""


class BridgeConfig:
    def __init__(
        self,
        shared_token,
        allowed_client_ip,
        unix_socket,
        spool_dir,
        max_file_bytes=DEFAULT_MAX_FILE_BYTES,
        unix_timeout_seconds=120.0,
        listen_host="127.0.0.1",
        listen_port=18766,
        firmware_max_file_bytes=DEFAULT_MAX_FIRMWARE_BYTES,
        firmware_targets=None,
    ):
        self.shared_token = shared_token
        self.allowed_client_ip = allowed_client_ip
        self.unix_socket = unix_socket
        self.spool_dir = spool_dir
        self.max_file_bytes = int(max_file_bytes)
        self.unix_timeout_seconds = float(unix_timeout_seconds)
        self.listen_host = listen_host
        self.listen_port = int(listen_port)
        self.firmware_max_file_bytes = int(firmware_max_file_bytes)
        self.firmware_targets = firmware_targets or {"driving": 0, "hoist": 2, "slide": 3}

    def validate(self):
        if not self.unix_socket.startswith("/"):
            raise ValueError("unix_socket must be an absolute path")
        if not 1 <= self.max_file_bytes <= 16 * 1024 * 1024:
            raise ValueError("max_file_bytes must be between 1 and 16777216")
        if not 1 <= self.firmware_max_file_bytes <= DEFAULT_MAX_FIRMWARE_BYTES:
            raise ValueError("firmware max_file_bytes must be between 1 and 8388608")
        if not 1 <= self.listen_port <= 65535:
            raise ValueError("listen_port must be between 1 and 65535")
        if sorted(self.firmware_targets) != sorted(FIRMWARE_TARGETS):
            raise ValueError("firmware targets must be driving, hoist, and slide")
        for axis in self.firmware_targets.values():
            if type(axis) is not int or not 0 <= axis <= 3:
                raise ValueError("firmware target axes must be integers from 0 to 3")


def load_config(path):
    parser = configparser.ConfigParser()
    if not parser.read(str(path), encoding="utf-8") or not parser.has_section("server"):
        raise ValueError("configuration file or [server] section not found: {}".format(path))

    def text(option, default):
        return parser.get("server", option, fallback=default).strip()

    config = BridgeConfig(
        shared_token=text("shared_token", ""),
        allowed_client_ip=text("allowed_client_ip", ""),
        unix_socket=text("unix_socket", "/tmp/amc_rsa_maintenance.sock"),
        spool_dir=text("spool_dir", DEFAULT_SPOOL_DIR),
        max_file_bytes=parser.getint("server", "max_file_bytes", fallback=DEFAULT_MAX_FILE_BYTES),
        unix_timeout_seconds=parser.getfloat("server", "unix_timeout_seconds", fallback=120.0),
        listen_host=text("listen_host", "127.0.0.1"),
        listen_port=parser.getint("server", "listen_port", fallback=18766),
        firmware_max_file_bytes=parser.getint(
            "firmware", "max_file_bytes", fallback=DEFAULT_MAX_FIRMWARE_BYTES
        ),
        firmware_targets={
            "driving": parser.getint("firmware", "driving_axis", fallback=0),
            "hoist": parser.getint("firmware", "hoist_axis", fallback=2),
            "slide": parser.getint("firmware", "slide_axis", fallback=3),
        },
    )
    config.validate()
    return config


def safe_filename(filename):
    """Keep only the base name (IE may send a full Windows path) and allow plain characters."""
    basename = filename.replace("\\", "/").split("/")[-1]
    if basename in ("", ".", "..") or not SAFE_FILENAME.match(basename):
        raise RequestRejected("file name may only contain A-Z, a-z, 0-9, '.', '_', '-' (max 128)")
    return basename


class MaintenanceBridge:
    """Validates one request and runs exactly one AMC command at a time."""

    def __init__(self, config, command_sender=None):
        self.config = config
        self._operation_lock = threading.Lock()
        self._command_sender = command_sender or self._send_unix_command
        self._operation_ids = itertools.count(1)

    def handle_request(self, header, payload, peer_ip):
        request_id = next(self._operation_ids)
        # Log only validated operation metadata, not tokens, confirmation, file content.
        operation = header.get("command") if header.get("command") in AMC_COMMANDS else "unknown"
        started = time.monotonic()
        try:
            self._validate(header, payload, peer_ip)
        except (RequestRejected, ValueError) as error:
            LOG.warning("BRIDGE_REJECT id=%d cmd=%s peer=%s reason=%s",
                        request_id, operation, peer_ip, error)
            return {"ok": False, "message": "ERROR: {}".format(error)}

        # Reject concurrency immediately instead of letting maintenance block the UI.
        if not self._operation_lock.acquire(False):
            LOG.warning("BRIDGE_BUSY id=%d cmd=%s axis=%s peer=%s",
                        request_id, operation, header["axis"], peer_ip)
            return {"ok": False, "message": "ERROR: another maintenance operation is running"}
        LOG.info("BRIDGE_START id=%d cmd=%s axis=%s target=%s bytes=%d peer=%s",
                 request_id, operation, header["axis"], header.get("target") or "-",
                 len(payload), peer_ip)
        try:
            response = self._execute(header, payload, request_id)
            LOG.log(logging.INFO if response["ok"] else logging.WARNING,
                    "BRIDGE_END id=%d cmd=%s axis=%s ok=%s elapsed_ms=%.1f response_bytes=%d",
                    request_id, operation, header["axis"], response["ok"],
                    (time.monotonic() - started) * 1000,
                    len(response["message"].encode("utf-8", "replace")))
            return response
        except RequestRejected as error:
            LOG.warning("BRIDGE_EXEC_REJECT id=%d cmd=%s stage=execute reason=%s",
                        request_id, operation, error)
            return {"ok": False, "message": "ERROR: {}".format(error)}
        except OSError as error:
            LOG.exception("BRIDGE_SOCKET_FAIL id=%d cmd=%s axis=%s error_type=%s",
                          request_id, operation, header["axis"], type(error).__name__)
            return {"ok": False, "message": "ERROR: controller socket failure: {}".format(error)}
        except Exception as error:  # Keep the TCP worker alive and report the failure.
            LOG.exception("BRIDGE_UNEXPECTED id=%d cmd=%s axis=%s",
                          request_id, operation, header["axis"])
            return {"ok": False, "message": "ERROR: bridge failure: {}".format(error)}
        finally:
            self._operation_lock.release()

    def _validate(self, header, payload, peer_ip):
        if header.get("protocol") != PROTOCOL_NAME:
            raise RequestRejected("unsupported protocol")
        # Empty shared_token / allowed_client_ip / "*" disable the corresponding check.
        if self.config.shared_token:
            token = header.get("token")
            # Compare bytes: compare_digest raises TypeError for non-ASCII str.
            expected = self.config.shared_token.encode("utf-8")
            if not isinstance(token, str) or not hmac.compare_digest(token.encode("utf-8"), expected):
                raise RequestRejected("authentication failed")
        if self.config.allowed_client_ip not in ("", "*", peer_ip):
            raise RequestRejected("client IP is not allowed")
        if header.get("payload_length") != len(payload):
            raise RequestRejected("payload length does not match header")

        command = header.get("command")
        validate_client_request(
            command,
            header.get("axis"),
            payload,
            header.get("filename", ""),
            self.config.max_file_bytes,
            header.get("target", ""),
            header.get("confirmation", ""),
            self.config.firmware_max_file_bytes,
        )
        if command in FILE_COMMANDS:
            safe_filename(header["filename"])

    def _execute(self, header, payload, request_id=None):
        command = header["command"]
        axis = header["axis"]
        path = ""
        if command in FILE_COMMANDS:
            path = self._save_uploaded_file(header["filename"], payload)
            LOG.info("BRIDGE_FILE_READY id=%s cmd=%s axis=%s bytes=%d",
                     request_id, command, axis, len(payload))
        if command == "firmware_update":
            axis = self.config.firmware_targets[header["target"]]

        amc_command, success_prefix = AMC_COMMANDS[command]
        LOG.info("BRIDGE_AMC_CALL id=%s cmd=%s axis=%s", request_id, command, axis)
        result = self._command_sender(amc_command.format(axis=axis, path=path))
        # A successful heading is not enough when a parameter row failed.
        ok = result.startswith(success_prefix)
        if command == "parameter_read" and ("READ_FAIL" in result or "TYPE_MISMATCH" in result):
            ok = False
            LOG.warning("BRIDGE_PARAM_PARTIAL_FAILURE id=%s axis=%s", request_id, axis)
        return {"ok": ok, "message": result}

    def _save_uploaded_file(self, filename, payload):
        """Store under spool_dir with the original name.  Never overwrite different content."""
        name = safe_filename(filename)
        spool = Path(self.config.spool_dir)
        spool.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = spool / name
        if destination.exists():
            if destination.is_file() and destination.read_bytes() == payload:
                return str(destination)
            raise RequestRejected("uploaded file already exists with different content: {}".format(name))

        handle, temporary_path = tempfile.mkstemp(prefix=".servo_upload_", dir=str(spool))
        try:
            with os.fdopen(handle, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, str(destination))
        except Exception:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
            raise
        return str(destination)

    def _send_unix_command(self, command):
        data = command.encode("utf-8")
        if len(data) >= 1024:  # AMC reads one 1024-byte buffer.
            raise RequestRejected("controller command is too long")
        LOG.info("BRIDGE_UNIX_CONNECT socket=%s", self.config.unix_socket)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as unix_sock:
            unix_sock.settimeout(self.config.unix_timeout_seconds)
            unix_sock.connect(self.config.unix_socket)
            unix_sock.sendall(data)
            unix_sock.shutdown(socket.SHUT_WR)
            reply = bytearray()
            while True:
                chunk = unix_sock.recv(4096)
                if not chunk:
                    break
                reply += chunk
                if len(reply) > RESPONSE_MAX_BYTES - 1024:
                    # JSON envelope adds overhead; never collect unbounded AMC output.
                    raise RequestRejected("controller response exceeds safe size limit")
        if not reply:
            raise RequestRejected("controller returned no response")
        LOG.info("BRIDGE_UNIX_REPLY bytes=%d", len(reply))
        return reply.decode("utf-8", errors="replace")


class BridgeRequestHandler(socketserver.BaseRequestHandler):
    def handle(self):
        config = self.server.bridge.config
        try:
            header = decode_json(recv_frame(self.request, HEADER_MAX_BYTES), "request header")
            length = header.get("payload_length")
            if type(length) is not int or not 0 <= length <= max(config.max_file_bytes, config.firmware_max_file_bytes):
                raise ProtocolError("payload_length is invalid or too large")
            payload = recv_exact(self.request, length)
            response = self.server.bridge.handle_request(header, payload, self.client_address[0])
        except ProtocolError as error:
            LOG.warning("BRIDGE_PROTOCOL_REJECT peer=%s error_type=%s",
                        self.client_address[0], type(error).__name__)
            response = {"ok": False, "message": "ERROR: invalid request: {}".format(error)}
        except OSError as error:
            LOG.warning("BRIDGE_NETWORK_FAIL peer=%s error_type=%s",
                        self.client_address[0], type(error).__name__)
            response = {"ok": False, "message": "ERROR: network failure: {}".format(error)}
        try:
            send_frame(self.request, encode_json(response))
        except OSError:
            LOG.warning("response could not be sent to %s", self.client_address[0])


class BridgeTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, bridge):
        self.bridge = bridge
        socketserver.TCPServer.__init__(self, address, BridgeRequestHandler)


def serve(config):
    config.validate()
    server = BridgeTCPServer((config.listen_host, config.listen_port), MaintenanceBridge(config))
    try:
        LOG.info(
            "BRIDGE_LISTEN host=%s port=%d allowed_ip=%s unix_socket=%s",
            config.listen_host, config.listen_port, config.allowed_client_ip, config.unix_socket,
        )
        if not config.shared_token or config.allowed_client_ip in ("", "*"):
            LOG.warning("BRIDGE_ACCESS_UNRESTRICTED: verify token and IP restrictions before deployment")
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def setup_file_logging():
    """Keep diagnostic logs across restarts, with bounded disk use."""
    try:
        logs = PACKAGE_ROOT / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            str(logs / "servo_maintenance_bridge.log"), maxBytes=2 * 1024 * 1024,
            backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        LOG.addHandler(handler)
    except OSError as error:
        LOG.warning("BRIDGE_LOG_FILE_UNAVAILABLE error_type=%s", type(error).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path(__file__).with_name("servo_maintenance.ini")))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
    setup_file_logging()
    try:
        serve(load_config(args.config))
    except (ValueError, OSError) as error:
        LOG.error("bridge did not start: %s", error)
        return 2
    except KeyboardInterrupt:
        LOG.info("bridge stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
