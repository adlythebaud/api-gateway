"""Tests for API key authentication."""

import json
import urllib.request
import urllib.error

import pytest

from gateway.config import (
    AuthConfig, Config, GatewayConfig, RouteConfig, UpstreamConfig,
)
from tests.helpers import make_gateway
from tests.mock_upstream import start_mock_upstream


@pytest.fixture
def upstream():
    server, base_url = start_mock_upstream()
    yield base_url
    server.shutdown()


def _make_auth_gateway(upstream_url, **auth_kwargs):
    defaults = dict(type="api_key", header="X-API-Key", keys=["key-1", "key-2"])
    defaults.update(auth_kwargs)
    config = Config(
        gateway=GatewayConfig(port=0),
        routes=[RouteConfig(
            path="/api/secure",
            methods=["GET", "POST"],
            upstream=UpstreamConfig(url=upstream_url),
            auth=AuthConfig(**defaults),
        )],
    )
    return make_gateway(config)


class TestAuthReject:
    def test_missing_key_returns_401(self, upstream):
        server, base_url = _make_auth_gateway(upstream)
        try:
            req = urllib.request.Request(f"{base_url}/api/secure")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401
            body = json.loads(exc_info.value.read())
            assert body["error"] == "unauthorized"
        finally:
            server.shutdown()

    def test_invalid_key_returns_401(self, upstream):
        server, base_url = _make_auth_gateway(upstream)
        try:
            req = urllib.request.Request(f"{base_url}/api/secure")
            req.add_header("X-API-Key", "bad-key")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401
        finally:
            server.shutdown()

    def test_empty_key_returns_401(self, upstream):
        server, base_url = _make_auth_gateway(upstream)
        try:
            req = urllib.request.Request(f"{base_url}/api/secure")
            req.add_header("X-API-Key", "")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401
        finally:
            server.shutdown()


class TestAuthAccept:
    def test_valid_key_passes(self, upstream):
        server, base_url = _make_auth_gateway(upstream)
        try:
            req = urllib.request.Request(f"{base_url}/api/secure")
            req.add_header("X-API-Key", "key-1")
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            server.shutdown()

    def test_second_valid_key_passes(self, upstream):
        server, base_url = _make_auth_gateway(upstream)
        try:
            req = urllib.request.Request(f"{base_url}/api/secure")
            req.add_header("X-API-Key", "key-2")
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            server.shutdown()


class TestAuthCustomHeader:
    def test_custom_header_name(self, upstream):
        server, base_url = _make_auth_gateway(upstream, header="Authorization", keys=["Bearer token123"])
        try:
            # Wrong header name should fail
            req = urllib.request.Request(f"{base_url}/api/secure")
            req.add_header("X-API-Key", "Bearer token123")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401

            # Correct header name should pass
            req = urllib.request.Request(f"{base_url}/api/secure")
            req.add_header("Authorization", "Bearer token123")
            resp = urllib.request.urlopen(req)
            assert resp.status == 200
        finally:
            server.shutdown()


class TestAuthOnlyOnConfiguredRoutes:
    def test_unauthenticated_route_passes(self, upstream):
        """Routes without auth config should not require authentication."""
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[
                RouteConfig(
                    path="/api/secure",
                    methods=["GET"],
                    upstream=UpstreamConfig(url=upstream),
                    auth=AuthConfig(keys=["secret"]),
                ),
                RouteConfig(
                    path="/api/public",
                    methods=["GET"],
                    upstream=UpstreamConfig(url=upstream),
                ),
            ],
        )
        server, base_url = make_gateway(config)
        try:
            # Public route should work without any key
            resp = urllib.request.urlopen(f"{base_url}/api/public")
            assert resp.status == 200

            # Secure route without key should fail
            req = urllib.request.Request(f"{base_url}/api/secure")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401
        finally:
            server.shutdown()


class TestAuthWithConfigFixture:
    def test_provided_config_keys(self, upstream):
        """Test with the keys from the provided gateway.yaml."""
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[RouteConfig(
                path="/api/internal",
                methods=["GET", "POST"],
                upstream=UpstreamConfig(url=upstream),
                auth=AuthConfig(
                    type="api_key",
                    header="X-API-Key",
                    keys=["sk_live_abc123", "sk_live_def456"],
                ),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            # Valid key
            req = urllib.request.Request(f"{base_url}/api/internal")
            req.add_header("X-API-Key", "sk_live_abc123")
            resp = urllib.request.urlopen(req)
            assert resp.status == 200

            # Other valid key
            req = urllib.request.Request(f"{base_url}/api/internal")
            req.add_header("X-API-Key", "sk_live_def456")
            resp = urllib.request.urlopen(req)
            assert resp.status == 200

            # Invalid key
            req = urllib.request.Request(f"{base_url}/api/internal")
            req.add_header("X-API-Key", "sk_live_wrong")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 401
        finally:
            server.shutdown()
