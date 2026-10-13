#!/usr/bin/env python3
"""Serve the maintenance UI on Linux for a browser-only Windows IPC."""

import argparse
import configparser
import json
import re
import socketserver
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from common.maintenance_protocol import (  # noqa: E402
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_FIRMWARE_BYTES,
    RemoteMaintenanceClient,
    TransportError,
)


PLACEHOLDER_TOKENS = ("", "CHANGE_ME", "CHANGE_ME_TO_A_LONG_RANDOM_TOKEN")
FORM_OVERHEAD_BYTES = 64 * 1024  # multipart headers and small text fields
UPLOAD_COMMANDS = {
    "/api/parameter-read": "parameter_read",
    "/api/parameter-update": "parameter_update",
    "/api/firmware-update": "firmware_update",
}


class WebConfig:
    def __init__(
        self,
        motion_ip,
        tcp_port,
        shared_token,
        timeout_seconds,
        web_host,
        web_port,
        allowed_browser_ip,
        max_file_bytes=DEFAULT_MAX_FILE_BYTES,
        max_firmware_bytes=DEFAULT_MAX_FIRMWARE_BYTES,
    ):
        self.motion_ip = motion_ip
        self.tcp_port = int(tcp_port)
        self.shared_token = shared_token
        self.timeout_seconds = float(timeout_seconds)
        self.web_host = web_host
        self.web_port = int(web_port)
        self.allowed_browser_ip = allowed_browser_ip
        self.max_file_bytes = int(max_file_bytes)
        self.max_firmware_bytes = int(max_firmware_bytes)

    def validate(self):
        if self.motion_ip not in ("127.0.0.1", "localhost"):
            raise ValueError("motion_ip must use the local Linux bridge")
        if not 1 <= self.tcp_port <= 65535:
            raise ValueError("tcp_port must be between 1 and 65535")
        if self.shared_token in PLACEHOLDER_TOKENS or len(self.shared_token) < 16:
            raise ValueError("shared_token must match the Linux setting and be at least 16 characters")
        if not self.web_host:
            raise ValueError("web host must be configured")
        if not 1 <= self.web_port <= 65535:
            raise ValueError("web UI port must be between 1 and 65535")
        if not self.allowed_browser_ip:
            raise ValueError("allowed_browser_ip must be configured")
        if not 1 <= self.max_file_bytes <= 16 * 1024 * 1024:
            raise ValueError("max_file_bytes must be between 1 and 16777216")
        if not 1 <= self.max_firmware_bytes <= DEFAULT_MAX_FIRMWARE_BYTES:
            raise ValueError("max_firmware_bytes must be between 1 and 8388608")


def load_web_config(path):
    parser = configparser.ConfigParser()
    if not parser.read(str(path), encoding="utf-8") or not parser.has_section("connection") \
            or not parser.has_section("web_ui"):
        raise ValueError("[connection] and [web_ui] sections are required: {}".format(path))
    connection = parser["connection"]
    web_ui = parser["web_ui"]
    config = WebConfig(
        motion_ip=connection.get("motion_ip", "").strip(),
        tcp_port=connection.getint("tcp_port", fallback=18766),
        shared_token=connection.get("shared_token", "").strip(),
        timeout_seconds=connection.getfloat("timeout_seconds", fallback=1200.0),
        web_host=web_ui.get("host", "0.0.0.0").strip(),
        web_port=web_ui.getint("port", fallback=18765),
        allowed_browser_ip=web_ui.get("allowed_browser_ip", "").strip(),
        max_file_bytes=connection.getint("max_file_bytes", fallback=DEFAULT_MAX_FILE_BYTES),
        max_firmware_bytes=connection.getint("max_firmware_bytes", fallback=DEFAULT_MAX_FIRMWARE_BYTES),
    )
    config.validate()
    return config


def parse_axis(value):
    try:
        axis = int(value)
    except (TypeError, ValueError):
        raise ValueError("axis must be 0 to 3")
    if not 0 <= axis <= 3:
        raise ValueError("axis must be 0 to 3")
    return axis


def parse_multipart(content_type, body):
    """Split a multipart/form-data body.

    Returns ({field: text}, {field: (filename, bytes)}).  Replaces the ``cgi``
    module, which was removed in Python 3.13.  File bytes are kept unchanged.
    """
    match = re.search(r'boundary="?([^";]+)"?', content_type)
    if not content_type.lower().startswith("multipart/form-data") or not match:
        raise ValueError("multipart/form-data content type is required")
    delimiter = b"--" + match.group(1).encode("latin-1")

    fields = {}
    files = {}
    # [preamble, part, part, ..., "--" epilogue]
    for part in body.split(delimiter)[1:-1]:
        head, _, data = part.partition(b"\r\n\r\n")
        if data.endswith(b"\r\n"):
            data = data[:-2]  # line break that belongs to the next delimiter
        head = head.decode("utf-8", "replace")
        name = re.search(r'\bname="([^"]*)"', head)
        filename = re.search(r'\bfilename="([^"]*)"', head)
        if not name:
            continue
        if filename:
            files[name.group(1)] = (filename.group(1), data)
        else:
            fields[name.group(1)] = data.decode("utf-8", "replace")
    return fields, files


class MaintenanceApplication:
    def __init__(self, config):
        self.config = config

    def request(self, command, axis=-1, payload=b"", filename="", target="", confirmation=""):
        client = RemoteMaintenanceClient(
            self.config.motion_ip,
            self.config.tcp_port,
            self.config.shared_token,
            timeout=self.config.timeout_seconds,
            max_file_bytes=self.config.max_file_bytes,
            max_firmware_bytes=self.config.max_firmware_bytes,
        )
        return client.request(command, axis, payload, filename, target, confirmation)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class MaintenanceHTTPServer(ThreadedHTTPServer):
    def __init__(self, address, application):
        self.application = application
        ThreadedHTTPServer.__init__(self, address, MaintenanceRequestHandler)


class MaintenanceRequestHandler(BaseHTTPRequestHandler):
    server_version = "ServoMaintenanceLinuxWeb/2.1"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._browser_allowed():
            return
        url = urlparse(self.path)
        if url.path == "/":
            self._send(200, INDEX_HTML, "text/html")
        elif url.path == "/api/status":
            self._run_command("status")
        elif url.path == "/api/fw-version":
            try:
                axis = parse_axis(parse_qs(url.query).get("axis", [None])[0])
            except ValueError as error:
                self._send_json(400, {"ok": False, "message": "invalid request: {}".format(error)})
                return
            self._run_command("firmware_version", axis)
        else:
            self._send_json(404, {"ok": False, "message": "not found"})

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._browser_allowed() or not self._same_origin():
            return
        path = urlparse(self.path).path
        # Upload forms post into a hidden iframe.  IE shows text/plain there instead
        # of offering a JSON download, so those replies use text/plain.
        plain_text = path in UPLOAD_COMMANDS
        try:
            if path == "/api/absolute-reset":
                request = self._reset_request()
            elif path in UPLOAD_COMMANDS:
                request = self._upload_request(UPLOAD_COMMANDS[path])
            else:
                self._send_json(404, {"ok": False, "message": "not found"})
                return
        except ValueError as error:
            self._send_json(400, {"ok": False, "message": "invalid request: {}".format(error)}, plain_text)
            return
        self._run_command(plain_text=plain_text, **request)

    def _reset_request(self):
        # Requiring JSON blocks a plain HTML form on another page from posting here.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ValueError("application/json content type is required")
        body = json.loads(self._read_body(16 * 1024).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("JSON object is required")
        return {"command": "absolute_reset", "axis": parse_axis(body.get("axis"))}

    def _upload_request(self, command):
        config = self.server.application.config
        limit = config.max_firmware_bytes if command == "firmware_update" else config.max_file_bytes
        fields, files = parse_multipart(
            self.headers.get("Content-Type", ""), self._read_body(limit + FORM_OVERHEAD_BYTES)
        )
        filename, payload = files.get("file", ("", b""))
        if not filename:
            raise ValueError("select one file")
        if len(payload) > limit:
            raise ValueError("file exceeds {} bytes".format(limit))

        request = {"command": command, "payload": payload, "filename": filename}
        if command == "firmware_update":
            request["target"] = fields.get("target", "")
            request["confirmation"] = fields.get("confirmation", "")
        else:
            request["axis"] = parse_axis(fields.get("axis"))
        return request

    def _run_command(
        self, command, axis=-1, payload=b"", filename="", target="", confirmation="", plain_text=False
    ):
        try:
            response = self.server.application.request(
                command, axis, payload, filename, target, confirmation
            )
        except ValueError as error:
            self._send_json(400, {"ok": False, "message": "invalid request: {}".format(error)}, plain_text)
        except TransportError as error:
            message = "Linux bridge communication failed: {}".format(error)
            self._send_json(502, {"ok": False, "message": message}, plain_text)
        else:
            self._send_json(200 if response.get("ok") else 409, response, plain_text)

    def _read_body(self, limit):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 1 <= length <= limit:
            raise ValueError("request body size must be 1 to {} bytes".format(limit))
        return self.rfile.read(length)

    def _browser_allowed(self):
        allowed = self.server.application.config.allowed_browser_ip
        if allowed in ("*", self.client_address[0]):
            return True
        self._send_json(403, {"ok": False, "message": "browser IP is not allowed"})
        return False

    def _same_origin(self):
        """Reject POSTs that another web page opened in the same browser sends here."""
        origin = self.headers.get("Origin")
        if not origin or origin == "http://" + self.headers.get("Host", ""):
            return True  # IE does not send Origin for same-site requests.
        self._send_json(403, {"ok": False, "message": "cross-site request is not allowed"})
        return False

    def _send_json(self, status, body, plain_text=False):
        if plain_text:
            # IE replaces short 4xx/5xx bodies in the iframe with its own error page.
            # The JSON "ok" field already carries the result, so answer 200.
            self._send(200, json.dumps(body, ensure_ascii=False), "text/plain")
        else:
            self._send(status, json.dumps(body, ensure_ascii=False), "application/json")

    def _send(self, status, text, content_type):
        encoded = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format_string, *args):
        # The UI stays quiet; operation results are shown in the browser.
        return


INDEX_HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta http-equiv="X-UA-Compatible" content="IE=edge"><title>Servo Maintenance</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;max-width:760px;margin:28px auto;padding:0 16px;color:#1f2937}
h1{margin-bottom:4px}.note{color:#4b5563;margin-top:0}.panel{border:1px solid #d1d5db;border-radius:8px;padding:16px;margin:14px 0}
button{padding:8px 12px;margin:4px 4px 4px 0;cursor:pointer}select,input{padding:7px;margin:4px 0}#result{white-space:pre-wrap;background:#111827;color:#e5e7eb;padding:12px;border-radius:6px;min-height:110px;overflow:auto}
.danger{background:#b91c1c;color:white;border:0}.warn{background:#b45309;color:white;border:0}
</style></head><body>
<h1>Servo Maintenance</h1>
<p class="note">Windows IPC → Linux motion controller → Servo 전용 유지보수 경로</p>
<div class="panel"><label>Axis <select id="axis"><option value="0">0 - Front</option><option value="1">1 - Rear</option><option value="2">2 - Hoist</option><option value="3">3 - Slide</option></select></label><br>
<button onclick="run('/api/status')">Connection Status</button><button onclick="run('/api/fw-version?axis='+axis())">Firmware Version</button><button class="danger" onclick="resetAxis()">Absolute Encoder Reset</button></div>
<div class="panel"><h3>Parameter File</h3><form id="parameter_form" method="post" enctype="multipart/form-data" target="file_response"><input type="hidden" id="file_axis" name="axis" value="0"><input type="file" id="file" name="file" accept=".txt,.csv"><br><button type="button" onclick="upload('/api/parameter-read')">Parameter Read</button><button type="button" class="warn" onclick="updateParameter()">Parameter Update</button></form><iframe name="file_response" id="file_response" style="display:none" onload="handleUploadResponse()"></iframe></div>
<div class="panel"><h3>Firmware Update (EC-Master FoE)</h3><p class="note">대상 Servo Pack과 .bin 파일을 선택하십시오. 실제 업데이트는 Linux 제어기의 EC-Master에서 실행됩니다.</p><form id="firmware_form" method="post" enctype="multipart/form-data" target="firmware_response"><label>Servo Pack <select id="firmware_target" name="target"><option value="driving">Driving (Front/Rear)</option><option value="hoist">Hoist</option><option value="slide">Slide</option></select></label><br><input type="file" id="firmware_file" name="file" accept=".bin"><br><label>확인 문구 <input type="text" id="firmware_confirmation" name="confirmation" autocomplete="off" placeholder="FIRMWARE_UPDATE"></label><br><button type="button" class="danger" onclick="updateFirmware()">Firmware Update</button></form><iframe name="firmware_response" id="firmware_response" style="display:none" onload="handleUploadResponse('firmware_response')"></iframe></div>
<div id="result">Ready.</div>
<script>
function axis(){return document.getElementById('axis').value}
function show(value){document.getElementById('result').innerText=value}
function displayResponse(body){try{var j=JSON.parse(body);show((j.ok?'OK\n':'ERROR\n')+j.message)}catch(e){show('ERROR\nInvalid response: '+body)}}
function run(url, options){show('Working...');try{var xhr=window.XMLHttpRequest?new XMLHttpRequest():new ActiveXObject('Microsoft.XMLHTTP');xhr.onreadystatechange=function(){if(xhr.readyState===4){if(xhr.responseText){displayResponse(xhr.responseText)}else{show('ERROR\nHTTP '+xhr.status)}}};xhr.open(options&&options.method?options.method:'GET',url,true);if(options&&options.headers){for(var name in options.headers){if(options.headers.hasOwnProperty(name)){xhr.setRequestHeader(name,options.headers[name])}}}xhr.send(options&&options.body?options.body:null)}catch(e){show('ERROR\n'+e)}}
function resetAxis(){if(confirm('선택한 축의 Absolute Encoder Reset을 실행하시겠습니까?'))run('/api/absolute-reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({axis:Number(axis())})})}
function upload(url){if(!document.getElementById('file').value){show('ERROR\nParameter 파일을 선택하세요.');return}document.getElementById('file_axis').value=axis();var form=document.getElementById('parameter_form');form.action=url;show('Working...');form.submit()}
function handleUploadResponse(frameId){try{var frame=document.getElementById(frameId||'file_response');var body=frame.contentWindow.document.body;if(body&&body.innerText){displayResponse(body.innerText)}}catch(e){show('ERROR\n'+e)}}
function updateParameter(){if(confirm('Parameter Update는 실제 서보 파라미터를 변경합니다. 현장 절차를 확인했습니까?'))upload('/api/parameter-update')}
function updateFirmware(){var file=document.getElementById('firmware_file');var confirmation=document.getElementById('firmware_confirmation');if(!file.value){show('ERROR\nFirmware .bin 파일을 선택하세요.');return}if(confirmation.value!=='FIRMWARE_UPDATE'){show('ERROR\n확인 문구에 FIRMWARE_UPDATE를 입력하세요.');return}if(!confirm('선택한 Servo Pack의 펌웨어를 Linux 제어기에서 업데이트합니다. 계속하시겠습니까?'))return;var form=document.getElementById('firmware_form');form.action='/api/firmware-update';show('Firmware update running on Linux...');form.submit()}
window.onload=function(){run('/api/status')};
</script></body></html>"""


def serve(config):
    server = MaintenanceHTTPServer((config.web_host, config.web_port), MaintenanceApplication(config))
    try:
        print("Servo Maintenance UI: http://{}:{}/".format(config.web_host, config.web_port))
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path(__file__).with_name("servo_maintenance.ini")))
    args = parser.parse_args()
    try:
        serve(load_web_config(args.config))
    except (ValueError, OSError) as error:
        print("Cannot start Servo Maintenance web UI: {}".format(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
