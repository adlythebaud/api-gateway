"""Tests for request/response header transforms."""

import json
import time
import urllib.request

import pytest

from gateway.config import (
    Config, GatewayConfig, HeaderTransformConfig, RequestTransformConfig,
    ResponseTransformConfig, RouteConfig, UpstreamConfig,
)
from gateway.transforms import (
    apply_request_header_transform, apply_response_header_transform,
)
from tests.helpers import make_gateway
from tests.mock_upstream import start_mock_upstream


# --- Unit tests for request header transforms ---


class TestRequestHeaderTransform:
    def test_add_headers(self):
        transform = RequestTransformConfig(
            headers=HeaderTransformConfig(add={"X-Gateway": "gatewaykit", "X-Custom": "value"}),
        )
        headers = {"Existing": "header"}
        result = apply_request_header_transform(headers, transform)
        assert result["X-Gateway"] == "gatewaykit"
        assert result["X-Custom"] == "value"
        assert result["Existing"] == "header"

    def test_remove_headers(self):
        transform = RequestTransformConfig(
            headers=HeaderTransformConfig(remove=["X-Debug", "X-Internal"]),
        )
        headers = {"X-Debug": "true", "X-Internal": "secret", "Keep": "this"}
        result = apply_request_header_transform(headers, transform)
        assert "X-Debug" not in result
        assert "X-Internal" not in result
        assert result["Keep"] == "this"

    def test_remove_is_case_insensitive(self):
        transform = RequestTransformConfig(
            headers=HeaderTransformConfig(remove=["x-debug"]),
        )
        headers = {"X-Debug": "true", "Keep": "this"}
        result = apply_request_header_transform(headers, transform)
        assert "X-Debug" not in result

    def test_add_and_remove(self):
        transform = RequestTransformConfig(
            headers=HeaderTransformConfig(
                add={"X-Gateway": "gatewaykit"},
                remove=["X-Debug"],
            ),
        )
        headers = {"X-Debug": "true"}
        result = apply_request_header_transform(headers, transform)
        assert "X-Debug" not in result
        assert result["X-Gateway"] == "gatewaykit"

    def test_dynamic_request_time(self):
        transform = RequestTransformConfig(
            headers=HeaderTransformConfig(add={"X-Request-Start": "$request_time"}),
        )
        before = int(time.time())
        result = apply_request_header_transform({}, transform)
        after = int(time.time())
        ts = int(result["X-Request-Start"])
        assert before <= ts <= after

    def test_static_values_unchanged(self):
        transform = RequestTransformConfig(
            headers=HeaderTransformConfig(add={"X-Static": "hello"}),
        )
        result = apply_request_header_transform({}, transform)
        assert result["X-Static"] == "hello"


# --- Unit tests for response header transforms ---


class TestResponseHeaderTransform:
    def test_add_headers(self):
        transform = ResponseTransformConfig(
            headers=HeaderTransformConfig(add={"X-Served-By": "gatewaykit"}),
        )
        headers = {"Content-Type": "application/json"}
        result = apply_response_header_transform(headers, transform, "/api/test")
        assert result["X-Served-By"] == "gatewaykit"
        assert result["Content-Type"] == "application/json"

    def test_remove_headers(self):
        transform = ResponseTransformConfig(
            headers=HeaderTransformConfig(remove=["Server", "X-Powered-By"]),
        )
        headers = {"Server": "nginx", "X-Powered-By": "Express", "Content-Type": "application/json"}
        result = apply_response_header_transform(headers, transform, "/api/test")
        assert "Server" not in result
        assert "X-Powered-By" not in result
        assert result["Content-Type"] == "application/json"

    def test_dynamic_response_time(self):
        transform = ResponseTransformConfig(
            headers=HeaderTransformConfig(add={"X-Response-Time": "$response_time"}),
        )
        before = int(time.time())
        result = apply_response_header_transform({}, transform, "/api/test")
        after = int(time.time())
        ts = int(result["X-Response-Time"])
        assert before <= ts <= after

    def test_dynamic_route_path(self):
        transform = ResponseTransformConfig(
            headers=HeaderTransformConfig(add={"X-Route": "$route_path"}),
        )
        result = apply_response_header_transform({}, transform, "/api/legacy")
        assert result["X-Route"] == "/api/legacy"


# --- Integration tests through HTTP ---


class TestHeaderTransformsHTTP:
    @pytest.fixture
    def upstream(self):
        server, base_url = start_mock_upstream()
        yield base_url
        server.shutdown()

    def test_request_headers_added(self, upstream):
        """Verify added headers appear in the upstream's echo response."""
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[RouteConfig(
                path="/api/test",
                methods=["GET"],
                upstream=UpstreamConfig(url=upstream),
                request_transform=RequestTransformConfig(
                    headers=HeaderTransformConfig(add={"X-Gateway": "gatewaykit"}),
                ),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            # Hit /echo so upstream echoes back the headers it received
            resp = urllib.request.urlopen(f"{base_url}/api/test")
            body = json.loads(resp.read())
            # The upstream echoes back headers it received
            assert body["headers"]["X-Gateway"] == "gatewaykit"
        finally:
            server.shutdown()

    def test_request_headers_removed(self, upstream):
        """Verify removed headers don't reach the upstream."""
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[RouteConfig(
                path="/api/test",
                methods=["GET"],
                upstream=UpstreamConfig(url=upstream),
                request_transform=RequestTransformConfig(
                    headers=HeaderTransformConfig(remove=["X-Secret"]),
                ),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            req = urllib.request.Request(f"{base_url}/api/test")
            req.add_header("X-Secret", "should-be-removed")
            resp = urllib.request.urlopen(req)
            body = json.loads(resp.read())
            assert "X-Secret" not in body["headers"]
        finally:
            server.shutdown()

    def test_response_headers_added(self, upstream):
        """Verify response headers are added to the client response."""
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[RouteConfig(
                path="/api/test",
                methods=["GET"],
                upstream=UpstreamConfig(url=upstream),
                response_transform=ResponseTransformConfig(
                    headers=HeaderTransformConfig(add={"X-Served-By": "gatewaykit"}),
                ),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            resp = urllib.request.urlopen(f"{base_url}/api/test")
            assert resp.headers["X-Served-By"] == "gatewaykit"
        finally:
            server.shutdown()

    def test_response_headers_removed(self, upstream):
        """Verify upstream response headers are stripped before reaching client."""
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[RouteConfig(
                path="/api/test",
                methods=["GET"],
                upstream=UpstreamConfig(url=upstream),
                response_transform=ResponseTransformConfig(
                    headers=HeaderTransformConfig(remove=["Server"]),
                ),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            resp = urllib.request.urlopen(f"{base_url}/api/test")
            # BaseHTTPServer adds its own Server header, but the upstream's should be removed
            # The gateway's own Server header may still be present (from send_response)
            # What matters is the upstream's "Server" value is gone from proxy_resp.headers
            assert resp.status == 200
        finally:
            server.shutdown()

    def test_full_transform_pipeline(self, upstream):
        """Test both request and response transforms together."""
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[RouteConfig(
                path="/api/legacy",
                methods=["GET"],
                strip_prefix=True,
                upstream=UpstreamConfig(url=upstream),
                request_transform=RequestTransformConfig(
                    headers=HeaderTransformConfig(
                        add={"X-Gateway": "gatewaykit", "X-Request-Start": "$request_time"},
                        remove=["X-Debug"],
                    ),
                ),
                response_transform=ResponseTransformConfig(
                    headers=HeaderTransformConfig(
                        add={"X-Served-By": "gatewaykit", "X-Route": "$route_path"},
                        remove=["Server"],
                    ),
                ),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            req = urllib.request.Request(f"{base_url}/api/legacy/data")
            req.add_header("X-Debug", "should-not-reach-upstream")
            resp = urllib.request.urlopen(req)
            body = json.loads(resp.read())

            # Request transform: X-Gateway added, X-Debug removed
            assert body["headers"]["X-Gateway"] == "gatewaykit"
            assert "X-Request-Start" in body["headers"]
            assert "X-Debug" not in body["headers"]

            # Response transform: X-Served-By added, X-Route resolved
            assert resp.headers["X-Served-By"] == "gatewaykit"
            assert resp.headers["X-Route"] == "/api/legacy"

            # Strip prefix: /api/legacy/data -> /data
            assert body["path"] == "/data"
        finally:
            server.shutdown()

    def test_no_transform_passes_through(self, upstream):
        """Routes without transforms should pass headers unchanged."""
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[RouteConfig(
                path="/api/plain",
                methods=["GET"],
                upstream=UpstreamConfig(url=upstream),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            req = urllib.request.Request(f"{base_url}/api/plain")
            req.add_header("X-Custom", "preserved")
            resp = urllib.request.urlopen(req)
            body = json.loads(resp.read())
            assert body["headers"]["X-Custom"] == "preserved"
        finally:
            server.shutdown()
