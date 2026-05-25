"""HTTP server for GatewayKit."""

import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

from gateway.config import Config
from gateway.router import Router


class GatewayHandler(BaseHTTPRequestHandler):
    """Request handler for the gateway. Config, router, and start_time are set on the class before serving."""

    config: Config
    router: Router
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

        route = self.router.match(self.path)
        if route is None:
            self._send_json(404, {"error": "not_found"})
            return

        if self.command not in route.methods:
            self._send_json(405, {"error": "method_not_allowed"})
            return

        # TODO: proxy to upstream (Phase 1.5)
        self._send_json(502, {"error": "not_implemented"})

    def _health(self):
        uptime = int(time.time() - self.start_time)
        self._send_json(200, {"status": "healthy", "uptime_seconds": uptime})

    def _send_json(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        # Suppress default stderr logging during tests; can be overridden later
        pass


def create_server(config: Config) -> HTTPServer:
    """Create and return an HTTPServer configured with the gateway handler."""
    GatewayHandler.config = config
    GatewayHandler.router = Router(config.routes)
    GatewayHandler.start_time = time.time()
    return HTTPServer(("", config.gateway.port), GatewayHandler)
