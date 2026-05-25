"""Standalone mode: starts mock upstream servers for all ports found in the config."""

import json
import threading
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

from gateway.config import Config


def extract_upstream_ports(config: Config) -> list[int]:
    """Extract all unique upstream ports from the config."""
    ports = set()
    for route in config.routes:
        if route.upstream.url:
            parsed = urllib.parse.urlparse(route.upstream.url)
            if parsed.port:
                ports.add(parsed.port)
        for target in route.upstream.targets:
            parsed = urllib.parse.urlparse(target.url)
            if parsed.port:
                ports.add(parsed.port)
    return sorted(ports)


class _MockHandler(BaseHTTPRequestHandler):
    def do_GET(self): self._handle()
    def do_POST(self): self._handle()
    def do_PUT(self): self._handle()
    def do_DELETE(self): self._handle()
    def do_PATCH(self): self._handle()

    def _handle(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if self.path == "/healthz":
            self._respond(200, {"status": "ok"})
            return
        if self.path == "/slow":
            time.sleep(10)
            self._respond(200, {"message": "slow_response"})
            return
        if self.path == "/flaky":
            self._respond(503, {"error": "service_unavailable"})
            return

        port = self.server.server_address[1]
        response = {
            "upstream_port": port,
            "method": self.command,
            "path": self.path,
        }
        if body:
            try:
                response["body"] = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                response["body_raw"] = body.decode("utf-8", errors="replace")

        self._respond(200, response)

    def _respond(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


def start_mock_upstreams(config: Config) -> list[HTTPServer]:
    """Start mock upstream servers on all ports referenced in the config."""
    ports = extract_upstream_ports(config)
    if not ports:
        return []

    servers = []
    for port in ports:
        try:
            srv = HTTPServer(("127.0.0.1", port), _MockHandler)
            thread = threading.Thread(target=srv.serve_forever, daemon=True)
            thread.start()
            servers.append(srv)
            print(f"  Mock upstream on http://127.0.0.1:{port}")
        except OSError as e:
            print(f"  Warning: could not start mock upstream on port {port}: {e}")

    return servers
