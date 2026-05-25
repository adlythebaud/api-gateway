"""Tests for the reverse proxy — forwarding requests to upstream services."""

import json
import urllib.request
import urllib.error

import pytest

from gateway.config import (
    Config, GatewayConfig, RouteConfig, UpstreamConfig,
)
from tests.helpers import make_gateway
from tests.mock_upstream import start_mock_upstream


@pytest.fixture
def upstream():
    server, base_url = start_mock_upstream()
    yield base_url
    server.shutdown()


def _make_gw(upstream_url, **route_kwargs):
    """Helper to create a gateway with a single route pointing at the given upstream."""
    defaults = dict(
        path="/api/test",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        upstream=UpstreamConfig(url=upstream_url),
    )
    defaults.update(route_kwargs)
    config = Config(
        gateway=GatewayConfig(port=0),
        routes=[RouteConfig(**defaults)],
    )
    return make_gateway(config)


# --- Basic proxying ---


class TestBasicProxy:
    def test_get_forwarded(self, upstream):
        server, base_url = _make_gw(upstream)
        try:
            resp = urllib.request.urlopen(f"{base_url}/api/test")
            body = json.loads(resp.read())
            assert resp.status == 200
            assert body["method"] == "GET"
            assert body["path"] == "/api/test"
        finally:
            server.shutdown()

    def test_post_forwarded_with_body(self, upstream):
        server, base_url = _make_gw(upstream, upstream=UpstreamConfig(url=f"{upstream}"))
        try:
            # Point at /echo to see the body echoed back
            server2, base_url2 = _make_gw(
                upstream,
                path="/echo",
                upstream=UpstreamConfig(url=upstream),
            )
            try:
                data = json.dumps({"name": "test"}).encode()
                req = urllib.request.Request(
                    f"{base_url2}/echo",
                    data=data,
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                resp = urllib.request.urlopen(req)
                body = json.loads(resp.read())
                assert body["method"] == "POST"
                assert body["path"] == "/echo"
                assert json.loads(body["body"]) == {"name": "test"}
            finally:
                server2.shutdown()
        finally:
            server.shutdown()

    def test_put_forwarded(self, upstream):
        server, base_url = _make_gw(upstream)
        try:
            req = urllib.request.Request(f"{base_url}/api/test", method="PUT", data=b"")
            resp = urllib.request.urlopen(req)
            body = json.loads(resp.read())
            assert body["method"] == "PUT"
        finally:
            server.shutdown()

    def test_upstream_status_preserved(self, upstream):
        server, base_url = _make_gw(upstream, path="/status", upstream=UpstreamConfig(url=upstream))
        try:
            req = urllib.request.Request(f"{base_url}/status/201")
            resp = urllib.request.urlopen(req)
            assert resp.status == 201
        finally:
            server.shutdown()

    def test_upstream_error_status_preserved(self, upstream):
        server, base_url = _make_gw(upstream, path="/status", upstream=UpstreamConfig(url=upstream))
        try:
            req = urllib.request.Request(f"{base_url}/status/400")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 400
        finally:
            server.shutdown()

    def test_upstream_headers_forwarded_to_client(self, upstream):
        server, base_url = _make_gw(upstream)
        try:
            resp = urllib.request.urlopen(f"{base_url}/api/test")
            assert resp.headers["Content-Type"] == "application/json"
        finally:
            server.shutdown()


# --- Upstream unreachable ---


class TestUpstreamDown:
    def test_unreachable_returns_502(self):
        # Point at a port nothing is listening on
        server, base_url = _make_gw("http://127.0.0.1:1")
        try:
            req = urllib.request.Request(f"{base_url}/api/test")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 502
            body = json.loads(exc_info.value.read())
            assert body["error"] == "upstream_unreachable"
        finally:
            server.shutdown()


# --- Timeout ---


class TestUpstreamTimeout:
    def test_slow_upstream_times_out(self, upstream):
        # Set a very short timeout so the /slow endpoint triggers it
        server, base_url = _make_gw(
            upstream,
            path="/slow",
            upstream=UpstreamConfig(url=upstream, timeout=0.5),
        )
        try:
            req = urllib.request.Request(f"{base_url}/slow")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 504
            body = json.loads(exc_info.value.read())
            assert body["error"] == "upstream_timeout"
        finally:
            server.shutdown()

    def test_global_timeout_applies(self, upstream):
        config = Config(
            gateway=GatewayConfig(port=0, global_timeout=0.5),
            routes=[RouteConfig(
                path="/slow",
                methods=["GET"],
                upstream=UpstreamConfig(url=upstream),  # no route-level timeout
            )],
        )
        server, base_url = make_gateway(config)
        try:
            req = urllib.request.Request(f"{base_url}/slow")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 504
        finally:
            server.shutdown()


# --- Strip prefix ---


class TestStripPrefix:
    def test_strip_prefix_forwards_without_prefix(self, upstream):
        server, base_url = _make_gw(
            upstream,
            path="/api/test",
            strip_prefix=True,
            upstream=UpstreamConfig(url=upstream),
        )
        try:
            resp = urllib.request.urlopen(f"{base_url}/api/test/items/42")
            body = json.loads(resp.read())
            assert body["path"] == "/items/42"
        finally:
            server.shutdown()

    def test_strip_prefix_exact_path_becomes_root(self, upstream):
        server, base_url = _make_gw(
            upstream,
            path="/api/test",
            strip_prefix=True,
            upstream=UpstreamConfig(url=upstream),
        )
        try:
            resp = urllib.request.urlopen(f"{base_url}/api/test")
            body = json.loads(resp.read())
            assert body["path"] == "/"
        finally:
            server.shutdown()

    def test_no_strip_prefix_keeps_full_path(self, upstream):
        server, base_url = _make_gw(
            upstream,
            path="/api/test",
            strip_prefix=False,
            upstream=UpstreamConfig(url=upstream),
        )
        try:
            resp = urllib.request.urlopen(f"{base_url}/api/test/items/42")
            body = json.loads(resp.read())
            assert body["path"] == "/api/test/items/42"
        finally:
            server.shutdown()


# --- Multiple routes ---


class TestMultipleRoutes:
    def test_routes_forward_to_different_upstreams(self):
        upstream1_server, upstream1_url = start_mock_upstream()
        upstream2_server, upstream2_url = start_mock_upstream()
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[
                    RouteConfig(path="/api/a", methods=["GET"], upstream=UpstreamConfig(url=upstream1_url)),
                    RouteConfig(path="/api/b", methods=["GET"], upstream=UpstreamConfig(url=upstream2_url)),
                ],
            )
            server, base_url = make_gateway(config)
            try:
                resp_a = urllib.request.urlopen(f"{base_url}/api/a")
                body_a = json.loads(resp_a.read())
                assert body_a["path"] == "/api/a"

                resp_b = urllib.request.urlopen(f"{base_url}/api/b")
                body_b = json.loads(resp_b.read())
                assert body_b["path"] == "/api/b"
            finally:
                server.shutdown()
        finally:
            upstream1_server.shutdown()
            upstream2_server.shutdown()
