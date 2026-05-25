"""Tests for the /health endpoint."""

import json
import time
import urllib.request

import pytest

from gateway.config import Config, GatewayConfig
from tests.helpers import make_gateway


class TestHealthEndpoint:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.server, self.base_url = make_gateway()
        yield
        self.server.shutdown()

    def test_returns_200(self):
        resp = urllib.request.urlopen(f"{self.base_url}/health")
        assert resp.status == 200

    def test_returns_json_content_type(self):
        resp = urllib.request.urlopen(f"{self.base_url}/health")
        assert "application/json" in resp.headers["Content-Type"]

    def test_returns_healthy_status(self):
        resp = urllib.request.urlopen(f"{self.base_url}/health")
        body = json.loads(resp.read())
        assert body["status"] == "healthy"

    def test_returns_uptime_seconds(self):
        resp = urllib.request.urlopen(f"{self.base_url}/health")
        body = json.loads(resp.read())
        assert "uptime_seconds" in body
        assert isinstance(body["uptime_seconds"], int)
        assert body["uptime_seconds"] >= 0

    def test_uptime_increments(self):
        resp1 = urllib.request.urlopen(f"{self.base_url}/health")
        body1 = json.loads(resp1.read())
        time.sleep(1.1)
        resp2 = urllib.request.urlopen(f"{self.base_url}/health")
        body2 = json.loads(resp2.read())
        assert body2["uptime_seconds"] >= body1["uptime_seconds"] + 1

    def test_non_get_method_still_works(self):
        # /health should respond to GET — POST should also reach _handle
        # but the spec says GET /health, so let's verify POST returns something
        req = urllib.request.Request(f"{self.base_url}/health", method="POST")
        resp = urllib.request.urlopen(req)
        body = json.loads(resp.read())
        assert body["status"] == "healthy"


class TestHealthWithDifferentConfigs:
    """Health endpoint works regardless of config — test with various configs."""

    def test_with_empty_routes(self):
        server, base_url = make_gateway(Config(gateway=GatewayConfig(port=0), routes=[]))
        try:
            resp = urllib.request.urlopen(f"{base_url}/health")
            body = json.loads(resp.read())
            assert body["status"] == "healthy"
        finally:
            server.shutdown()

    def test_with_custom_port_config(self):
        server, base_url = make_gateway(Config(gateway=GatewayConfig(port=9999), routes=[]))
        try:
            resp = urllib.request.urlopen(f"{base_url}/health")
            body = json.loads(resp.read())
            assert body["status"] == "healthy"
        finally:
            server.shutdown()


class TestUnmatchedRoutes:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.server, self.base_url = make_gateway()
        yield
        self.server.shutdown()

    def test_unknown_path_returns_404(self):
        req = urllib.request.Request(f"{self.base_url}/nonexistent")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 404

    def test_404_returns_json(self):
        req = urllib.request.Request(f"{self.base_url}/nonexistent")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        body = json.loads(exc_info.value.read())
        assert body["error"] == "not_found"
