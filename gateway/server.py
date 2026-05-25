"""HTTP server for GatewayKit."""

import json
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

from gateway.config import Config, RouteConfig
from gateway.proxy import ProxyRequest, forward_request
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

        self._proxy(route)

    def _proxy(self, route: RouteConfig):
        # Determine upstream URL
        upstream_url = route.upstream.url
        if not upstream_url:
            # Load balancing not implemented yet — use first target
            if route.upstream.targets:
                upstream_url = route.upstream.targets[0].url
            else:
                self._send_json(502, {"error": "no_upstream_configured"})
                return

        # Determine the path to forward
        forward_path = self.path
        if route.strip_prefix:
            forward_path = self.path[len(route.path):]
            if not forward_path:
                forward_path = "/"

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # Build headers dict from request
        headers = {}
        for key in self.headers:
            headers[key] = self.headers[key]

        # Determine timeout: route-level overrides global
        timeout = route.upstream.timeout or self.config.gateway.global_timeout

        proxy_req = ProxyRequest(
            method=self.command,
            path=forward_path,
            headers=headers,
            body=body,
        )

        try:
            proxy_resp = forward_request(upstream_url, proxy_req, timeout)
        except TimeoutError:
            self._send_json(504, {"error": "upstream_timeout"})
            return
        except ConnectionError:
            self._send_json(502, {"error": "upstream_unreachable"})
            return

        # Send upstream response back to client
        self.send_response(proxy_resp.status)
        for key, value in proxy_resp.headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(proxy_resp.body)

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
