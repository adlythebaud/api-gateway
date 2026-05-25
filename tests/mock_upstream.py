"""Mock upstream server for testing. Provides canned responses, slow endpoints, and flaky endpoints."""

import json
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler


class MockUpstreamHandler(BaseHTTPRequestHandler):
    """A simple upstream that returns canned JSON responses."""

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def do_DELETE(self):
        self._handle()

    def do_PATCH(self):
        self._handle()

    def _handle(self):
        # Read request body if present
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if self.path == "/slow":
            time.sleep(5)
            self._respond(200, {"message": "slow_response"})
        elif self.path == "/flaky":
            self._respond(503, {"error": "service_unavailable"})
        elif self.path == "/echo":
            # Echo back method, path, headers, and body
            headers_dict = {}
            for key in self.headers:
                headers_dict[key] = self.headers[key]
            self._respond(200, {
                "method": self.command,
                "path": self.path,
                "headers": headers_dict,
                "body": body.decode("utf-8", errors="replace"),
            })
        elif self.path.startswith("/status/"):
            # Return whatever status code is in the path
            code = int(self.path.split("/status/")[1])
            self._respond(code, {"status": code})
        else:
            headers_dict = {}
            for key in self.headers:
                headers_dict[key] = self.headers[key]
            port = self.server.server_address[1]
            self._respond(200, {
                "message": "ok",
                "upstream_port": port,
                "method": self.command,
                "path": self.path,
                "headers": headers_dict,
            })

    def _respond(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


def start_mock_upstream() -> tuple[HTTPServer, str]:
    """Start a mock upstream on a random port. Returns (server, base_url)."""
    server = HTTPServer(("127.0.0.1", 0), MockUpstreamHandler)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, base_url
