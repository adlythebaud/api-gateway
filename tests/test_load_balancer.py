"""Tests for load balancing — round robin and weighted round robin."""

import json
import urllib.request
from collections import Counter

import pytest

from gateway.config import (
    Config, GatewayConfig, RouteConfig, UpstreamConfig, UpstreamTarget,
)
from gateway.load_balancer import LoadBalancer, LoadBalancerRegistry
from tests.helpers import make_gateway
from tests.mock_upstream import start_mock_upstream


# --- Unit tests for LoadBalancer ---


class TestRoundRobin:
    def test_cycles_through_targets(self):
        upstream = UpstreamConfig(
            targets=[
                UpstreamTarget(url="http://a:1", weight=1),
                UpstreamTarget(url="http://b:2", weight=1),
                UpstreamTarget(url="http://c:3", weight=1),
            ],
            balance="round_robin",
        )
        lb = LoadBalancer(upstream)
        results = [lb.next() for _ in range(6)]
        assert results == [
            "http://a:1", "http://b:2", "http://c:3",
            "http://a:1", "http://b:2", "http://c:3",
        ]

    def test_single_target(self):
        upstream = UpstreamConfig(
            targets=[UpstreamTarget(url="http://a:1", weight=1)],
            balance="round_robin",
        )
        lb = LoadBalancer(upstream)
        results = [lb.next() for _ in range(3)]
        assert results == ["http://a:1", "http://a:1", "http://a:1"]


class TestWeightedRoundRobin:
    def test_respects_weights(self):
        upstream = UpstreamConfig(
            targets=[
                UpstreamTarget(url="http://a:1", weight=3),
                UpstreamTarget(url="http://b:2", weight=1),
            ],
            balance="weighted_round_robin",
        )
        lb = LoadBalancer(upstream)
        # Over enough iterations, distribution should match weights
        results = [lb.next() for _ in range(100)]
        counts = Counter(results)
        # With weights 3:1 and sequence [a,a,a,b], ratio should be 3:1
        assert counts["http://a:1"] > counts["http://b:2"]
        ratio = counts["http://a:1"] / counts["http://b:2"]
        assert 2.5 <= ratio <= 3.5

    def test_all_equal_weights(self):
        upstream = UpstreamConfig(
            targets=[
                UpstreamTarget(url="http://a:1", weight=1),
                UpstreamTarget(url="http://b:2", weight=1),
            ],
            balance="weighted_round_robin",
        )
        lb = LoadBalancer(upstream)
        results = [lb.next() for _ in range(100)]
        counts = Counter(results)
        assert counts["http://a:1"] == 50
        assert counts["http://b:2"] == 50


class TestHealthTracking:
    def test_skips_unhealthy_targets(self):
        upstream = UpstreamConfig(
            targets=[
                UpstreamTarget(url="http://a:1", weight=1),
                UpstreamTarget(url="http://b:2", weight=1),
            ],
            balance="round_robin",
        )
        lb = LoadBalancer(upstream)
        lb.mark_unhealthy("http://a:1")
        results = [lb.next() for _ in range(3)]
        assert results == ["http://b:2", "http://b:2", "http://b:2"]

    def test_recovers_when_marked_healthy(self):
        upstream = UpstreamConfig(
            targets=[
                UpstreamTarget(url="http://a:1", weight=1),
                UpstreamTarget(url="http://b:2", weight=1),
            ],
            balance="round_robin",
        )
        lb = LoadBalancer(upstream)
        lb.mark_unhealthy("http://a:1")
        assert lb.next() == "http://b:2"
        lb.mark_healthy("http://a:1")
        # Both targets should be available again
        results = {lb.next() for _ in range(10)}
        assert "http://a:1" in results
        assert "http://b:2" in results

    def test_all_unhealthy_returns_none(self):
        upstream = UpstreamConfig(
            targets=[
                UpstreamTarget(url="http://a:1", weight=1),
                UpstreamTarget(url="http://b:2", weight=1),
            ],
            balance="round_robin",
        )
        lb = LoadBalancer(upstream)
        lb.mark_unhealthy("http://a:1")
        lb.mark_unhealthy("http://b:2")
        assert lb.next() is None


# --- LoadBalancerRegistry ---


class TestLoadBalancerRegistry:
    def test_returns_balancer_for_multi_target_route(self):
        routes = [RouteConfig(
            path="/api/test",
            methods=["GET"],
            upstream=UpstreamConfig(targets=[
                UpstreamTarget(url="http://a:1"),
                UpstreamTarget(url="http://b:2"),
            ]),
        )]
        registry = LoadBalancerRegistry(routes)
        assert registry.get("/api/test") is not None

    def test_returns_none_for_single_url_route(self):
        routes = [RouteConfig(
            path="/api/test",
            methods=["GET"],
            upstream=UpstreamConfig(url="http://localhost:3001"),
        )]
        registry = LoadBalancerRegistry(routes)
        assert registry.get("/api/test") is None


# --- Integration tests through HTTP ---


class TestLoadBalancerHTTP:
    def test_round_robin_distributes(self):
        upstream1_server, upstream1_url = start_mock_upstream()
        upstream2_server, upstream2_url = start_mock_upstream()
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[RouteConfig(
                    path="/api/balanced",
                    methods=["GET"],
                    upstream=UpstreamConfig(
                        targets=[
                            UpstreamTarget(url=upstream1_url, weight=1),
                            UpstreamTarget(url=upstream2_url, weight=1),
                        ],
                        balance="round_robin",
                    ),
                )],
            )
            server, base_url = make_gateway(config)
            try:
                ports = []
                for _ in range(6):
                    resp = urllib.request.urlopen(f"{base_url}/api/balanced")
                    body = json.loads(resp.read())
                    ports.append(body["upstream_port"])

                counts = Counter(ports)
                assert len(counts) == 2
                assert counts[ports[0]] == 3
                assert counts[ports[1]] == 3
            finally:
                server.shutdown()
        finally:
            upstream1_server.shutdown()
            upstream2_server.shutdown()

    def test_weighted_round_robin_distributes(self):
        upstream1_server, upstream1_url = start_mock_upstream()
        upstream2_server, upstream2_url = start_mock_upstream()
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[RouteConfig(
                    path="/api/weighted",
                    methods=["GET"],
                    upstream=UpstreamConfig(
                        targets=[
                            UpstreamTarget(url=upstream1_url, weight=3),
                            UpstreamTarget(url=upstream2_url, weight=1),
                        ],
                        balance="weighted_round_robin",
                    ),
                )],
            )
            server, base_url = make_gateway(config)
            try:
                ports = []
                for _ in range(40):
                    resp = urllib.request.urlopen(f"{base_url}/api/weighted")
                    body = json.loads(resp.read())
                    ports.append(body["upstream_port"])

                counts = Counter(ports)
                # Extract the ports from the upstream URLs
                port1 = int(upstream1_url.split(":")[-1])
                port2 = int(upstream2_url.split(":")[-1])
                # Weight 3:1 ratio
                assert counts[port1] > counts[port2]
                ratio = counts[port1] / counts[port2]
                assert 2.5 <= ratio <= 3.5
            finally:
                server.shutdown()
        finally:
            upstream1_server.shutdown()
            upstream2_server.shutdown()

    def test_all_unhealthy_returns_503(self):
        upstream_server, upstream_url = start_mock_upstream()
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[RouteConfig(
                    path="/api/balanced",
                    methods=["GET"],
                    upstream=UpstreamConfig(
                        targets=[UpstreamTarget(url=upstream_url, weight=1)],
                        balance="round_robin",
                    ),
                )],
            )
            server, base_url = make_gateway(config)
            try:
                # Manually mark the only target as unhealthy
                handler_cls = server.RequestHandlerClass
                lb = handler_cls.load_balancers.get("/api/balanced")
                lb.mark_unhealthy(upstream_url)

                with pytest.raises(urllib.error.HTTPError) as exc_info:
                    urllib.request.urlopen(f"{base_url}/api/balanced")
                assert exc_info.value.code == 503
                body = json.loads(exc_info.value.read())
                assert body["error"] == "no_healthy_upstreams"
            finally:
                server.shutdown()
        finally:
            upstream_server.shutdown()

    def test_strip_prefix_with_load_balancing(self):
        upstream_server, upstream_url = start_mock_upstream()
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[RouteConfig(
                    path="/api/products",
                    methods=["GET"],
                    strip_prefix=True,
                    upstream=UpstreamConfig(
                        targets=[UpstreamTarget(url=upstream_url, weight=1)],
                        balance="round_robin",
                    ),
                )],
            )
            server, base_url = make_gateway(config)
            try:
                resp = urllib.request.urlopen(f"{base_url}/api/products/123")
                body = json.loads(resp.read())
                assert body["path"] == "/123"
            finally:
                server.shutdown()
        finally:
            upstream_server.shutdown()
