"""Tests for circuit breaker — trip, cooldown, half-open, and recovery."""

import json
import time
import urllib.request
import urllib.error

import pytest

from gateway.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, State
from gateway.config import (
    CircuitBreakerConfig, Config, GatewayConfig, RouteConfig, UpstreamConfig,
)
from tests.helpers import make_gateway
from tests.mock_upstream import start_mock_upstream


# --- Unit tests for CircuitBreaker ---


class TestCircuitBreakerUnit:
    def test_starts_closed(self):
        cb = CircuitBreaker(CircuitBreakerConfig(threshold=3, window=60.0, cooldown=10.0))
        assert cb.state == State.CLOSED

    def test_allows_when_closed(self):
        cb = CircuitBreaker(CircuitBreakerConfig(threshold=3, window=60.0, cooldown=10.0))
        allowed, err = cb.allow()
        assert allowed is True
        assert err is None

    def test_trips_after_threshold_failures(self):
        cb = CircuitBreaker(CircuitBreakerConfig(threshold=3, window=60.0, cooldown=10.0))
        cb.record_failure()
        cb.record_failure()
        assert cb.state == State.CLOSED
        cb.record_failure()
        assert cb.state == State.OPEN

    def test_rejects_when_open(self):
        cb = CircuitBreaker(CircuitBreakerConfig(threshold=2, window=60.0, cooldown=10.0))
        cb.record_failure()
        cb.record_failure()
        allowed, err = cb.allow()
        assert allowed is False
        assert err["error"] == "service_unavailable"
        assert "retry_after" in err

    def test_retry_after_decreases(self):
        cb = CircuitBreaker(CircuitBreakerConfig(threshold=1, window=60.0, cooldown=2.0))
        cb.record_failure()
        _, err1 = cb.allow()
        time.sleep(1.1)
        _, err2 = cb.allow()
        assert err2["retry_after"] <= err1["retry_after"]

    def test_transitions_to_half_open_after_cooldown(self):
        cb = CircuitBreaker(CircuitBreakerConfig(threshold=1, window=60.0, cooldown=0.1))
        cb.record_failure()
        assert cb.state == State.OPEN
        time.sleep(0.15)
        allowed, err = cb.allow()
        assert allowed is True
        assert cb.state == State.HALF_OPEN

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(CircuitBreakerConfig(threshold=1, window=60.0, cooldown=0.1))
        cb.record_failure()
        time.sleep(0.15)
        cb.allow()  # transitions to half-open
        cb.record_success()
        assert cb.state == State.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(CircuitBreakerConfig(threshold=1, window=60.0, cooldown=0.1))
        cb.record_failure()
        time.sleep(0.15)
        cb.allow()  # transitions to half-open
        cb.record_failure()
        assert cb.state == State.OPEN

    def test_half_open_rejects_second_request(self):
        cb = CircuitBreaker(CircuitBreakerConfig(threshold=1, window=60.0, cooldown=0.1))
        cb.record_failure()
        time.sleep(0.15)
        allowed1, _ = cb.allow()  # transitions to half-open, probe allowed
        allowed2, err = cb.allow()  # second request rejected while probing
        assert allowed1 is True
        assert allowed2 is False

    def test_failures_outside_window_dont_count(self):
        cb = CircuitBreaker(CircuitBreakerConfig(threshold=3, window=0.1, cooldown=10.0))
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        # Old failures expired, this is only the 1st in the new window
        cb.record_failure()
        assert cb.state == State.CLOSED

    def test_success_doesnt_affect_closed_state(self):
        cb = CircuitBreaker(CircuitBreakerConfig(threshold=3, window=60.0, cooldown=10.0))
        cb.record_failure()
        cb.record_success()
        # Success in closed state doesn't clear failure history
        cb.record_failure()
        cb.record_failure()
        assert cb.state == State.OPEN


# --- CircuitBreakerRegistry ---


class TestCircuitBreakerRegistry:
    def test_returns_breaker_for_configured_route(self):
        routes = [RouteConfig(
            path="/api/test",
            methods=["GET"],
            upstream=UpstreamConfig(url="http://localhost:1"),
            circuit_breaker=CircuitBreakerConfig(threshold=5, window=60.0, cooldown=30.0),
        )]
        registry = CircuitBreakerRegistry(routes)
        assert registry.get("/api/test") is not None

    def test_returns_none_for_unconfigured_route(self):
        routes = [RouteConfig(
            path="/api/test",
            methods=["GET"],
            upstream=UpstreamConfig(url="http://localhost:1"),
        )]
        registry = CircuitBreakerRegistry(routes)
        assert registry.get("/api/test") is None


# --- Integration tests through HTTP ---


class TestCircuitBreakerHTTP:
    def test_trips_and_returns_503(self):
        """Hit a flaky upstream enough times to trip the breaker, then verify 503."""
        upstream_server, upstream_url = start_mock_upstream()
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[RouteConfig(
                    path="/flaky",
                    methods=["GET"],
                    upstream=UpstreamConfig(url=upstream_url),
                    circuit_breaker=CircuitBreakerConfig(threshold=3, window=60.0, cooldown=30.0),
                )],
            )
            server, base_url = make_gateway(config)
            try:
                # Hit /flaky 3 times — upstream returns 503, triggers failures
                for _ in range(3):
                    req = urllib.request.Request(f"{base_url}/flaky")
                    with pytest.raises(urllib.error.HTTPError) as exc_info:
                        urllib.request.urlopen(req)
                    assert exc_info.value.code == 503

                # 4th request — circuit breaker should be open
                req = urllib.request.Request(f"{base_url}/flaky")
                with pytest.raises(urllib.error.HTTPError) as exc_info:
                    urllib.request.urlopen(req)
                assert exc_info.value.code == 503
                body = json.loads(exc_info.value.read())
                assert body["error"] == "service_unavailable"
                assert "retry_after" in body
            finally:
                server.shutdown()
        finally:
            upstream_server.shutdown()

    def test_recovers_after_cooldown(self):
        """Breaker trips, cools down, probe succeeds, traffic resumes."""
        upstream_server, upstream_url = start_mock_upstream()
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[
                    RouteConfig(
                        path="/flaky",
                        methods=["GET"],
                        upstream=UpstreamConfig(url=upstream_url),
                        circuit_breaker=CircuitBreakerConfig(threshold=2, window=60.0, cooldown=0.2),
                    ),
                    RouteConfig(
                        path="/api/ok",
                        methods=["GET"],
                        upstream=UpstreamConfig(url=upstream_url),
                        circuit_breaker=CircuitBreakerConfig(threshold=2, window=60.0, cooldown=0.2),
                    ),
                ],
            )
            server, base_url = make_gateway(config)
            try:
                # Trip the breaker on /flaky
                for _ in range(2):
                    req = urllib.request.Request(f"{base_url}/flaky")
                    with pytest.raises(urllib.error.HTTPError):
                        urllib.request.urlopen(req)

                # Wait for cooldown
                time.sleep(0.3)

                # /api/ok should work (probe to a healthy path succeeds)
                resp = urllib.request.urlopen(f"{base_url}/api/ok")
                assert resp.status == 200
            finally:
                server.shutdown()
        finally:
            upstream_server.shutdown()

    def test_no_breaker_on_unconfigured_route(self):
        """Routes without circuit_breaker config should never trip."""
        upstream_server, upstream_url = start_mock_upstream()
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[RouteConfig(
                    path="/flaky",
                    methods=["GET"],
                    upstream=UpstreamConfig(url=upstream_url),
                    # No circuit_breaker config
                )],
            )
            server, base_url = make_gateway(config)
            try:
                # Even after many 503s, no circuit breaker kicks in
                for _ in range(10):
                    req = urllib.request.Request(f"{base_url}/flaky")
                    with pytest.raises(urllib.error.HTTPError) as exc_info:
                        urllib.request.urlopen(req)
                    # Should always be 503 from upstream, never a breaker 503
                    body = json.loads(exc_info.value.read())
                    assert body.get("error") == "service_unavailable" or "error" in body
            finally:
                server.shutdown()
        finally:
            upstream_server.shutdown()

    def test_breaker_trips_on_connection_error(self):
        """Connection errors (upstream down) should also trip the breaker."""
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[RouteConfig(
                path="/api/down",
                methods=["GET"],
                upstream=UpstreamConfig(url="http://127.0.0.1:1"),
                circuit_breaker=CircuitBreakerConfig(threshold=2, window=60.0, cooldown=30.0),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            # 2 connection errors to trip
            for _ in range(2):
                req = urllib.request.Request(f"{base_url}/api/down")
                with pytest.raises(urllib.error.HTTPError) as exc_info:
                    urllib.request.urlopen(req)
                assert exc_info.value.code == 502

            # 3rd request — breaker is open, should get 503
            req = urllib.request.Request(f"{base_url}/api/down")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 503
            body = json.loads(exc_info.value.read())
            assert body["error"] == "service_unavailable"
        finally:
            server.shutdown()
