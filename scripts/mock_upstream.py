#!/usr/bin/env python3
"""Start mock upstream servers for local testing with gateway.yaml.

Spins up simple HTTP servers on ports 3001–3006 that return canned JSON responses.
Each server echoes back the request method, path, and headers so you can verify
the gateway is forwarding correctly.

Usage:
    uv run python scripts/mock_upstream.py
    uv run python scripts/mock_upstream.py 3001 3002   # only specific ports
"""

import json
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

DEFAULT_PORTS = [3001, 3002, 3003, 3004, 3005, 3006]


class MockHandler(BaseHTTPRequestHandler):
    """Returns JSON echoing the request details. Includes a few special paths."""

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
        headers_dict = {key: self.headers[key] for key in self.headers}

        response = {
            "upstream_port": port,
            "method": self.command,
            "path": self.path,
            "headers": headers_dict,
        }

        if body:
            try:
                response["body"] = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                response["body_raw"] = body.decode("utf-8", errors="replace")

        self._respond(200, response)

    def _respond(self, status: int, body: dict):
        data = json.dumps(body, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        port = self.server.server_address[1]
        print(f"  [upstream:{port}] {self.command} {self.path} -> {args[1] if len(args) > 1 else '?'}")


def start_upstream(port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main():
    if len(sys.argv) > 1:
        ports = [int(p) for p in sys.argv[1:]]
    else:
        ports = DEFAULT_PORTS

    servers = []
    for port in ports:
        try:
            srv = start_upstream(port)
            servers.append(srv)
            print(f"  Mock upstream listening on http://127.0.0.1:{port}")
        except OSError as e:
            print(f"  Failed to start on port {port}: {e}")

    if not servers:
        print("No upstream servers started.")
        sys.exit(1)

    print(f"\n  {len(servers)} mock upstream(s) running. Press Ctrl+C to stop.\n")
    print("  Special paths:")
    print("    /healthz  -> 200 OK")
    print("    /slow     -> 200 after 10s delay")
    print("    /flaky    -> 503 Service Unavailable")
    print("    /*        -> 200 with request echo\n")

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nShutting down mock upstreams...")
        for srv in servers:
            srv.shutdown()


if __name__ == "__main__":
    main()
