#!/usr/bin/env python3
"""Authenticated loopback bridge from the Linux web UI to the AMC maintenance socket."""

import argparse
import configparser
import hmac
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
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_FIRMWARE_BYTES,
    FILE_COMMANDS,
    FIRMWARE_TARGETS,
    HEADER_MAX_BYTES,
    PROTOCOL_NAME,
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
        if self.shared_token in PLACEHOLDER_TOKENS or len(self.shared_token) < 16:
            raise ValueError("shared_token must be a non-placeholder value of at least 16 characters")
        if not self.allowed_client_ip:
            raise ValueError("allowed_client_ip must be configured")
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

    def handle_request(self, header, payload, peer_ip):
        try:
            self._validate(header, payload, peer_ip)
        except (RequestRejected, ValueError) as error:
            return {"ok": False, "message": "ERROR: {}".format(error)}

        # A firmware update can take minutes.  Answer other requests at once instead of
        # letting them wait in the queue until the web UI times out.
        if not self._operation_lock.acquire(False):
            return {"ok": False, "message": "ERROR: another maintenance operation is running"}
        try:
            return self._execute(header, payload)
        except RequestRejected as error:
            return {"ok": False, "message": "ERROR: {}".format(error)}
        except OSError as error:
            LOG.exception("controller socket failure")
            return {"ok": False, "message": "ERROR: controller socket failure: {}".format(error)}
        except Exception as error:  # Keep the TCP worker alive and report the failure.
            LOG.exception("unexpected maintenance bridge failure")
            return {"ok": False, "message": "ERROR: bridge failure: {}".format(error)}
        finally:
            self._operation_lock.release()

    def _validate(self, header, payload, peer_ip):
        if header.get("protocol") != PROTOCOL_NAME:
            raise RequestRejected("unsupported protocol")
        token = header.get("token")
        # Compare bytes: compare_digest raises TypeError for non-ASCII str.
        expected = self.config.shared_token.encode("utf-8")
        if not isinstance(token, str) or not hmac.compare_digest(token.encode("utf-8"), expected):
            raise RequestRejected("authentication failed")
        if self.config.allowed_client_ip not in ("*", peer_ip):
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

    def _execute(self, header, payload):
        command = header["command"]
        axis = header["axis"]
        path = ""
        if command in FILE_COMMANDS:
            path = self._save_uploaded_file(header["filename"], payload)
        if command == "firmware_update":
            axis = self.config.firmware_targets[header["target"]]

        amc_command, success_prefix = AMC_COMMANDS[command]
        result = self._command_sender(amc_command.format(axis=axis, path=path))
        return {"ok": result.startswith(success_prefix), "message": result}

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
        if not reply:
            raise RequestRejected("controller returned no response")
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
            response = {"ok": False, "message": "ERROR: invalid request: {}".format(error)}
        except OSError as error:
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
            "listening on %s:%d for client %s; unix socket=%s",
            config.listen_host, config.listen_port, config.allowed_client_ip, config.unix_socket,
        )
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path(__file__).with_name("servo_maintenance.ini")))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
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
