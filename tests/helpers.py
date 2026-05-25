"""Shared test helpers — starts a gateway server on a random port in a background thread."""

import threading
import time
from http.server import HTTPServer

from gateway.config import Config, GatewayConfig
from gateway.server import GatewayHandler


def make_gateway(config: Config | None = None) -> tuple[HTTPServer, str]:
    """Start a gateway server on a random port. Returns (server, base_url).

    The server runs in a daemon thread and will be cleaned up when the test ends.
    """
    if config is None:
        config = Config(gateway=GatewayConfig(port=0), routes=[])

    # Port 0 lets the OS pick a free port
    config.gateway.port = 0

    handler = type("TestHandler", (GatewayHandler,), {
        "config": config,
        "start_time": time.time(),
    })

    server = HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    return server, base_url
