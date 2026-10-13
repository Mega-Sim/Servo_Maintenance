"""Length-framed TCP protocol used by the servo-maintenance tool.

The transport is intentionally small and dependency-free.  A request consists
of one JSON header frame followed by an optional raw file payload.  A response
is one JSON frame.  This avoids treating a Windows file path as a Linux path.
"""

import json
import socket
import struct


PROTOCOL_NAME = "SMT1"
HEADER_MAX_BYTES = 64 * 1024
RESPONSE_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 1024 * 1024
DEFAULT_MAX_FIRMWARE_BYTES = 8 * 1024 * 1024
FIRMWARE_TARGETS = {"driving", "hoist", "slide"}
FIRMWARE_CONFIRMATION = "FIRMWARE_UPDATE"
VALID_COMMANDS = {
    "status",
    "firmware_version",
    "absolute_reset",
    "parameter_read",
    "parameter_update",
    "firmware_update",
}
AXIS_COMMANDS = {
    "firmware_version",
    "absolute_reset",
    "parameter_read",
    "parameter_update",
}
FILE_COMMANDS = {"parameter_read", "parameter_update", "firmware_update"}


class ProtocolError(Exception):
    """Raised when a peer sends an invalid framed protocol message."""


class TransportError(Exception):
    """Raised when TCP communication fails before a usable response arrives."""


def recv_exact(sock, size):
    """Read exactly *size* bytes or raise ProtocolError on early disconnect."""
    if size < 0:
        raise ProtocolError("negative read size")
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ProtocolError("connection closed before expected bytes arrived")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_frame(sock, payload):
    """Send one unsigned-32-bit-length-prefixed byte frame."""
    if not isinstance(payload, bytes):
        raise TypeError("frame payload must be bytes")
    if len(payload) > 0xFFFFFFFF:
        raise ProtocolError("frame too large")
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def recv_frame(sock, max_size):
    """Receive one framed byte payload, enforcing the supplied size limit."""
    if max_size < 0:
        raise ValueError("max_size must be non-negative")
    size = struct.unpack("!I", recv_exact(sock, 4))[0]
    if size > max_size:
        raise ProtocolError("frame too large: {} bytes (limit {})".format(size, max_size))
    return recv_exact(sock, size)


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_json(payload, message_name):
    try:
        result = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ProtocolError("invalid {} JSON: {}".format(message_name, error))
    if not isinstance(result, dict):
        raise ProtocolError("{} JSON must be an object".format(message_name))
    return result


def validate_client_request(
    command,
    axis,
    payload,
    filename,
    max_file_bytes=DEFAULT_MAX_FILE_BYTES,
    target="",
    confirmation="",
    max_firmware_bytes=DEFAULT_MAX_FIRMWARE_BYTES,
):
    """Validate the local client request before opening a TCP connection."""
    if command not in VALID_COMMANDS:
        raise ValueError("unsupported command: {}".format(command))
    if command == "status":
        if axis != -1:
            raise ValueError("status axis must be -1")
    elif command in AXIS_COMMANDS:
        if not isinstance(axis, int) or axis < 0 or axis > 3:
            raise ValueError("axis must be an integer from 0 to 3")
    elif command == "firmware_update" and axis != -1:
        raise ValueError("firmware_update axis must be -1")
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if command == "firmware_update":
        if target not in FIRMWARE_TARGETS:
            raise ValueError("firmware target must be driving, hoist, or slide")
        if confirmation != FIRMWARE_CONFIRMATION:
            raise ValueError("firmware confirmation must be FIRMWARE_UPDATE")
        if not filename or not filename.lower().endswith(".bin"):
            raise ValueError("firmware filename must end with .bin")
        if len(payload) == 0:
            raise ValueError("firmware file payload is empty")
        if len(payload) > max_firmware_bytes:
            raise ValueError("firmware file exceeds {} bytes".format(max_firmware_bytes))
    elif command in FILE_COMMANDS:
        if not filename:
            raise ValueError("filename is required for parameter commands")
        if len(payload) == 0:
            raise ValueError("parameter file payload is empty")
        if len(payload) > max_file_bytes:
            raise ValueError("parameter file exceeds {} bytes".format(max_file_bytes))
    elif payload:
        raise ValueError("payload is only valid for parameter commands")


class RemoteMaintenanceClient:
    """Synchronous client used by the Windows-local web UI."""

    def __init__(
        self,
        host,
        port,
        token,
        timeout=15,
        max_file_bytes=DEFAULT_MAX_FILE_BYTES,
        max_firmware_bytes=DEFAULT_MAX_FIRMWARE_BYTES,
    ):
        self.host = host
        self.port = int(port)
        self.token = token
        self.timeout = float(timeout)
        self.max_file_bytes = int(max_file_bytes)
        self.max_firmware_bytes = int(max_firmware_bytes)

    def request(self, command, axis=-1, payload=b"", filename="", target="", confirmation=""):
        validate_client_request(
            command,
            axis,
            payload,
            filename,
            self.max_file_bytes,
            target,
            confirmation,
            self.max_firmware_bytes,
        )
        header = {
            "protocol": PROTOCOL_NAME,
            "token": self.token,
            "command": command,
            "axis": axis,
            "payload_length": len(payload),
            "filename": filename,
            "target": target,
            "confirmation": confirmation,
        }
        try:
            with socket.create_connection((self.host, self.port), self.timeout) as sock:
                sock.settimeout(self.timeout)
                send_frame(sock, encode_json(header))
                if payload:
                    sock.sendall(payload)
                response = decode_json(recv_frame(sock, RESPONSE_MAX_BYTES), "response")
        except (OSError, ProtocolError) as error:
            raise TransportError(str(error))
        if "ok" not in response or "message" not in response:
            raise TransportError("malformed response from Linux bridge")
        return response
