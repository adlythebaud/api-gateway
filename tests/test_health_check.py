"""Tests for background health checks — marking targets unhealthy/healthy."""

import json
import threading
import time
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytest

from gateway.config import (
    Config, GatewayConfig, HealthCheckConfig, RouteConfig, UpstreamConfig, UpstreamTarget,
)
from gateway.health_check import HealthChecker, start_health_checks
from gateway.load_balancer import LoadBalancer, LoadBalancerRegistry


def _start_health_upstream(port: int = 0, healthy: bool = True):
    """Start an upstream that responds to /healthz. Returns (server, base_url, set_healthy_fn)."""
    state = {"healthy": healthy}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                if state["healthy"]:
                    self._respond(200, {"status": "ok"})
                else:
                    self._respond(503, {"error": "unhealthy"})
            else:
                self._respond(200, {"message": "ok", "upstream_port": self.server.server_address[1]})

        def _respond(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_address[1]
    base_url = f"http://127.0.0.1:{actual_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def set_healthy(h: bool):
        state["healthy"] = h

    return server, base_url, set_healthy


# --- Unit tests for HealthChecker ---


class TestHealthCheckerUnit:
    def test_marks_unhealthy_after_threshold(self):
        server, url, set_healthy = _start_health_upstream(healthy=False)
        marked = {"unhealthy": False}

        def on_unhealthy(u):
            marked["unhealthy"] = True

        try:
            checker = HealthChecker(
                target_url=url,
                config=HealthCheckConfig(path="/healthz", interval=0.05, unhealthy_threshold=2),
                on_healthy=lambda u: None,
                on_unhealthy=on_unhealthy,
            )
            checker.start()
            time.sleep(0.3)
            checker.stop()
            assert marked["unhealthy"] is True
        finally:
            server.shutdown()

    def test_marks_healthy_after_recovery(self):
        server, url, set_healthy = _start_health_upstream(healthy=False)
        events = []

        def on_unhealthy(u):
            events.append("unhealthy")

        def on_healthy(u):
            events.append("healthy")

        try:
            checker = HealthChecker(
                target_url=url,
                config=HealthCheckConfig(path="/healthz", interval=0.05, unhealthy_threshold=2),
                on_healthy=on_healthy,
                on_unhealthy=on_unhealthy,
            )
            checker.start()
            time.sleep(0.2)
            # Now make it healthy
            set_healthy(True)
            time.sleep(0.2)
            checker.stop()
            assert "unhealthy" in events
            assert "healthy" in events
        finally:
            server.shutdown()

    def test_does_not_mark_unhealthy_below_threshold(self):
        server, url, set_healthy = _start_health_upstream(healthy=False)
        marked = {"unhealthy": False}

        try:
            checker = HealthChecker(
                target_url=url,
                config=HealthCheckConfig(path="/healthz", interval=0.05, unhealthy_threshold=100),
                on_healthy=lambda u: None,
                on_unhealthy=lambda u: marked.update(unhealthy=True),
            )
            checker.start()
            time.sleep(0.2)
            checker.stop()
            # Threshold of 100 should not be reached in 0.2s with 0.05s interval
            assert marked["unhealthy"] is False
        finally:
            server.shutdown()

    def test_unreachable_upstream_counts_as_failure(self):
        marked = {"unhealthy": False}
        checker = HealthChecker(
            target_url="http://127.0.0.1:1",  # nothing listening
            config=HealthCheckConfig(path="/healthz", interval=0.05, unhealthy_threshold=2),
            on_healthy=lambda u: None,
            on_unhealthy=lambda u: marked.update(unhealthy=True),
        )
        checker.start()
        time.sleep(0.5)
        checker.stop()
        assert marked["unhealthy"] is True


# --- Integration: health checks + load balancer ---


class TestHealthCheckWithLoadBalancer:
    def test_removes_unhealthy_target_from_rotation(self):
        server1, url1, set_healthy1 = _start_health_upstream(healthy=True)
        server2, url2, set_healthy2 = _start_health_upstream(healthy=True)
        try:
            upstream = UpstreamConfig(
                targets=[
                    UpstreamTarget(url=url1, weight=1),
                    UpstreamTarget(url=url2, weight=1),
                ],
                balance="round_robin",
            )
            route = RouteConfig(
                path="/api/test",
                methods=["GET"],
                upstream=upstream,
                health_check=HealthCheckConfig(path="/healthz", interval=0.1, unhealthy_threshold=2),
            )
            lb_registry = LoadBalancerRegistry([route])
            checkers = start_health_checks([route], lb_registry)

            # Both healthy — should get both
            lb = lb_registry.get("/api/test")
            assert len(lb.get_healthy_targets()) == 2

            # Make server2 unhealthy
            set_healthy2(False)
            time.sleep(0.5)

            # Only server1 should be in rotation
            assert url1 in lb.get_healthy_targets()
            assert url2 not in lb.get_healthy_targets()

            # Recover server2
            set_healthy2(True)
            time.sleep(0.3)

            assert url2 in lb.get_healthy_targets()

            for c in checkers:
                c.stop()
        finally:
            server1.shutdown()
            server2.shutdown()
