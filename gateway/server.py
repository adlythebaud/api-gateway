"""HTTP server for GatewayKit."""

import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

from gateway.config import Config


class GatewayHandler(BaseHTTPRequestHandler):
    """Request handler for the gateway. Config and start_time are set on the class before serving."""

    config: Config
    start_time: float

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
        if self.path == "/health":
            self._health()
            return

        self._not_found()

    def _health(self):
        uptime = int(time.time() - self.start_time)
        body = json.dumps({"status": "healthy", "uptime_seconds": uptime})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def _not_found(self):
        body = json.dumps({"error": "not_found"})
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format, *args):
        # Suppress default stderr logging during tests; can be overridden later
        pass


def create_server(config: Config) -> HTTPServer:
    """Create and return an HTTPServer configured with the gateway handler."""
    GatewayHandler.config = config
    GatewayHandler.start_time = time.time()
    return HTTPServer(("", config.gateway.port), GatewayHandler)
