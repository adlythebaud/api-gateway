"""Tests for route matching and method filtering."""

import json
import os
import urllib.request
import urllib.error

import pytest

from gateway.config import (
    Config, GatewayConfig, RouteConfig, UpstreamConfig, load_config,
)
from gateway.router import Router
from tests.helpers import make_gateway

CONFIGS_DIR = os.path.join(os.path.dirname(__file__), "configs")


# --- Unit tests for the Router ---


class TestRouterMatch:
    def test_exact_match(self):
        routes = [RouteConfig(path="/api/users", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:3001"))]
        router = Router(routes)
        assert router.match("/api/users") is not None
        assert router.match("/api/users").path == "/api/users"

    def test_prefix_match(self):
        routes = [RouteConfig(path="/api/users", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:3001"))]
        router = Router(routes)
        assert router.match("/api/users/123") is not None
        assert router.match("/api/users/123/profile") is not None

    def test_no_match(self):
        routes = [RouteConfig(path="/api/users", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:3001"))]
        router = Router(routes)
        assert router.match("/api/orders") is None
        assert router.match("/other") is None

    def test_no_partial_prefix_match(self):
        """'/api/usersettings' should NOT match '/api/users' — must match at segment boundary."""
        routes = [RouteConfig(path="/api/users", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:3001"))]
        router = Router(routes)
        assert router.match("/api/usersettings") is None

    def test_longest_prefix_wins(self):
        routes = [
            RouteConfig(path="/api/users", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:3001")),
            RouteConfig(path="/api/users/admin", methods=["GET", "POST"], upstream=UpstreamConfig(url="http://localhost:3002")),
        ]
        router = Router(routes)
        match = router.match("/api/users/admin")
        assert match.path == "/api/users/admin"

        match = router.match("/api/users/admin/settings")
        assert match.path == "/api/users/admin"

        match = router.match("/api/users/123")
        assert match.path == "/api/users"

    def test_empty_routes(self):
        router = Router([])
        assert router.match("/anything") is None

    def test_root_path(self):
        routes = [RouteConfig(path="/", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:3001"))]
        router = Router(routes)
        # Root should match everything with a /
        assert router.match("/") is not None
        assert router.match("/anything") is not None

    def test_multiple_routes_no_overlap(self):
        routes = [
            RouteConfig(path="/api/users", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:3001")),
            RouteConfig(path="/api/orders", methods=["POST"], upstream=UpstreamConfig(url="http://localhost:3002")),
            RouteConfig(path="/api/products", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:3003")),
        ]
        router = Router(routes)
        assert router.match("/api/users").path == "/api/users"
        assert router.match("/api/orders").path == "/api/orders"
        assert router.match("/api/products/42").path == "/api/products"
        assert router.match("/api/other") is None


# --- Integration tests: routing through the HTTP server ---


class TestRouting404:
    @pytest.fixture(autouse=True)
    def setup(self):
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[
                RouteConfig(path="/api/users", methods=["GET", "POST"], upstream=UpstreamConfig(url="http://localhost:3001")),
                RouteConfig(path="/api/orders", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:3002")),
            ],
        )
        self.server, self.base_url = make_gateway(config)
        yield
        self.server.shutdown()

    def test_unmatched_path_returns_404(self):
        req = urllib.request.Request(f"{self.base_url}/api/unknown")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 404
        body = json.loads(exc_info.value.read())
        assert body["error"] == "not_found"

    def test_health_still_works(self):
        resp = urllib.request.urlopen(f"{self.base_url}/health")
        assert resp.status == 200


class TestRouting405:
    @pytest.fixture(autouse=True)
    def setup(self):
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[
                RouteConfig(path="/api/users", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:3001")),
                RouteConfig(path="/api/orders", methods=["GET", "POST"], upstream=UpstreamConfig(url="http://localhost:3002")),
            ],
        )
        self.server, self.base_url = make_gateway(config)
        yield
        self.server.shutdown()

    def test_wrong_method_returns_405(self):
        req = urllib.request.Request(f"{self.base_url}/api/users", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 405
        body = json.loads(exc_info.value.read())
        assert body["error"] == "method_not_allowed"

    def test_delete_on_get_only_returns_405(self):
        req = urllib.request.Request(f"{self.base_url}/api/users", method="DELETE")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 405

    def test_put_on_get_post_returns_405(self):
        req = urllib.request.Request(f"{self.base_url}/api/orders", method="PUT")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 405

    def test_allowed_method_does_not_405(self):
        """GET on a GET-only route should not 405 (it'll 502 since no upstream, but not 405)."""
        req = urllib.request.Request(f"{self.base_url}/api/users", method="GET")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        # Should be 502 (not implemented yet), NOT 405
        assert exc_info.value.code == 502

    def test_post_on_get_post_route_does_not_405(self):
        req = urllib.request.Request(f"{self.base_url}/api/orders", method="POST", data=b"")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 502


class TestRoutingWithConfigFixtures:
    """Test routing against different config fixtures to verify generality."""

    def test_multi_route_config(self):
        config = load_config(os.path.join(CONFIGS_DIR, "multi_route.yaml"))
        server, base_url = make_gateway(config)
        try:
            # /v1/items/special should match before /v1/items (longest prefix)
            router = Router(config.routes)
            match = router.match("/v1/items/special")
            assert match.path == "/v1/items/special"

            match = router.match("/v1/items/other")
            assert match.path == "/v1/items"

            # Method filtering via HTTP
            req = urllib.request.Request(f"{base_url}/v1/items", method="POST", data=b"")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 405
        finally:
            server.shutdown()

    def test_minimal_config(self):
        config = load_config(os.path.join(CONFIGS_DIR, "minimal.yaml"))
        server, base_url = make_gateway(config)
        try:
            # Only GET allowed
            req = urllib.request.Request(f"{base_url}/api/hello", method="DELETE")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 405

            # Unknown route
            req = urllib.request.Request(f"{base_url}/api/goodbye")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 404
        finally:
            server.shutdown()
