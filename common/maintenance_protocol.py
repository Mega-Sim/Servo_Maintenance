"""Length-framed TCP protocol between the Linux web UI and the local bridge.

Request : [4-byte big-endian length][JSON header][optional raw file bytes]
Response: [4-byte big-endian length][JSON body]

Only the file name and bytes travel over the wire, so a Windows path is never
used as a Linux path.  Works on Python 3.5 and newer.
"""

import json
import socket
import struct


PROTOCOL_NAME = "SMT1"
HEADER_MAX_BYTES = 64 * 1024
RESPONSE_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 1024 * 1024
DEFAULT_MAX_FIRMWARE_BYTES = 8 * 1024 * 1024
FIRMWARE_TARGETS = ("driving", "hoist", "slide")
FIRMWARE_CONFIRMATION = "FIRMWARE_UPDATE"
AXIS_COMMANDS = ("firmware_version", "absolute_reset", "parameter_read", "parameter_update")
PARAMETER_COMMANDS = ("parameter_read", "parameter_update")
FILE_COMMANDS = PARAMETER_COMMANDS + ("firmware_update",)
VALID_COMMANDS = ("status",) + AXIS_COMMANDS + ("firmware_update",)


class ProtocolError(Exception):
    """The peer sent a broken frame."""


class TransportError(Exception):
    """TCP communication failed before a usable response arrived."""


def is_axis(value):
    """True for 0..3.  bool is a subclass of int, so the exact type is checked."""
    return type(value) is int and 0 <= value <= 3


def recv_exact(sock, size):
    """Read exactly *size* bytes."""
    if size < 0:
        raise ProtocolError("negative read size")
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ProtocolError("connection closed before expected bytes arrived")
        data += chunk
    return bytes(data)


def send_frame(sock, payload):
    sock.sendall(struct.pack("!I", len(payload)) + payload)


def recv_frame(sock, max_size):
    size = struct.unpack("!I", recv_exact(sock, 4))[0]
    if size > max_size:
        raise ProtocolError("frame too large: {} bytes (limit {})".format(size, max_size))
    return recv_exact(sock, size)


def encode_json(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def decode_json(payload, name):
    try:
        result = json.loads(payload.decode("utf-8"))
    except ValueError as error:  # UnicodeDecodeError and JSONDecodeError are ValueErrors.
        raise ProtocolError("invalid {} JSON: {}".format(name, error))
    if not isinstance(result, dict):
        raise ProtocolError("{} JSON must be an object".format(name))
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
    """Raise ValueError when a request is not allowed.  Used by the web UI and the bridge."""
    if command not in VALID_COMMANDS:
        raise ValueError("unsupported command: {}".format(command))
    if not isinstance(payload, bytes):
        raise ValueError("payload must be bytes")

    if command in AXIS_COMMANDS:
        if not is_axis(axis):
            raise ValueError("axis must be an integer from 0 to 3")
    elif axis != -1:
        raise ValueError("{} axis must be -1".format(command))

    if command == "firmware_update":
        if target not in FIRMWARE_TARGETS:
            raise ValueError("firmware target must be driving, hoist, or slide")
        if confirmation != FIRMWARE_CONFIRMATION:
            raise ValueError("firmware confirmation must be FIRMWARE_UPDATE")
        if not isinstance(filename, str) or not filename.lower().endswith(".bin"):
            raise ValueError("firmware filename must end with .bin")
        limit = max_firmware_bytes
    elif command in PARAMETER_COMMANDS:
        if not isinstance(filename, str) or not filename:
            raise ValueError("filename is required for parameter commands")
        limit = max_file_bytes
    else:
        if payload:
            raise ValueError("payload is only valid for file commands")
        return

    if not payload:
        raise ValueError("uploaded file is empty")
    if len(payload) > limit:
        raise ValueError("uploaded file exceeds {} bytes".format(limit))


class RemoteMaintenanceClient:
    """Synchronous client used by the Linux web UI to call the local bridge."""

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
            command, axis, payload, filename,
            self.max_file_bytes, target, confirmation, self.max_firmware_bytes,
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
                sock.sendall(payload)
                response = decode_json(recv_frame(sock, RESPONSE_MAX_BYTES), "response")
        except (OSError, ProtocolError) as error:
            raise TransportError(str(error))
        if "ok" not in response or "message" not in response:
            raise TransportError("malformed response from Linux bridge")
        return response
