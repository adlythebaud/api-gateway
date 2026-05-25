"""Tests for rate limiting — fixed window, sliding window, per-IP, global, concurrency."""

import json
import threading
import time
import urllib.request
import urllib.error

import pytest

from gateway.config import (
    Config, GatewayConfig, RateLimitConfig, RouteConfig, UpstreamConfig,
)
from gateway.rate_limiter import (
    FixedWindowLimiter, SlidingWindowLimiter, RateLimiterRegistry, create_limiter,
)
from tests.helpers import make_gateway
from tests.mock_upstream import start_mock_upstream


# --- Unit tests for FixedWindowLimiter ---


class TestFixedWindowLimiter:
    def test_allows_up_to_limit(self):
        limiter = FixedWindowLimiter(RateLimitConfig(requests=3, window=60.0))
        assert limiter.allow("1.2.3.4") is True
        assert limiter.allow("1.2.3.4") is True
        assert limiter.allow("1.2.3.4") is True
        assert limiter.allow("1.2.3.4") is False

    def test_per_ip_isolation(self):
        limiter = FixedWindowLimiter(RateLimitConfig(requests=2, window=60.0, per="ip"))
        assert limiter.allow("1.1.1.1") is True
        assert limiter.allow("1.1.1.1") is True
        assert limiter.allow("1.1.1.1") is False
        # Different IP should have its own bucket
        assert limiter.allow("2.2.2.2") is True
        assert limiter.allow("2.2.2.2") is True
        assert limiter.allow("2.2.2.2") is False

    def test_global_bucket(self):
        limiter = FixedWindowLimiter(RateLimitConfig(requests=3, window=60.0, per="global"))
        assert limiter.allow("1.1.1.1") is True
        assert limiter.allow("2.2.2.2") is True
        assert limiter.allow("3.3.3.3") is True
        # All share one bucket
        assert limiter.allow("4.4.4.4") is False

    def test_window_resets(self):
        limiter = FixedWindowLimiter(RateLimitConfig(requests=2, window=0.1))
        assert limiter.allow("1.1.1.1") is True
        assert limiter.allow("1.1.1.1") is True
        assert limiter.allow("1.1.1.1") is False
        time.sleep(0.15)
        # Window expired — counter resets
        assert limiter.allow("1.1.1.1") is True


# --- Unit tests for SlidingWindowLimiter ---


class TestSlidingWindowLimiter:
    def test_allows_up_to_limit(self):
        limiter = SlidingWindowLimiter(RateLimitConfig(requests=3, window=60.0, strategy="sliding_window"))
        assert limiter.allow("1.2.3.4") is True
        assert limiter.allow("1.2.3.4") is True
        assert limiter.allow("1.2.3.4") is True
        assert limiter.allow("1.2.3.4") is False

    def test_per_ip_isolation(self):
        limiter = SlidingWindowLimiter(RateLimitConfig(requests=2, window=60.0, strategy="sliding_window", per="ip"))
        assert limiter.allow("1.1.1.1") is True
        assert limiter.allow("1.1.1.1") is True
        assert limiter.allow("1.1.1.1") is False
        assert limiter.allow("2.2.2.2") is True

    def test_global_bucket(self):
        limiter = SlidingWindowLimiter(RateLimitConfig(requests=2, window=60.0, strategy="sliding_window", per="global"))
        assert limiter.allow("1.1.1.1") is True
        assert limiter.allow("2.2.2.2") is True
        assert limiter.allow("3.3.3.3") is False

    def test_sliding_expiry(self):
        limiter = SlidingWindowLimiter(RateLimitConfig(requests=2, window=0.1, strategy="sliding_window"))
        assert limiter.allow("1.1.1.1") is True
        assert limiter.allow("1.1.1.1") is True
        assert limiter.allow("1.1.1.1") is False
        time.sleep(0.15)
        # Old timestamps expired — should allow again
        assert limiter.allow("1.1.1.1") is True

    def test_sliding_window_gradual_expiry(self):
        """Earlier requests expire individually, not all at once like fixed window."""
        limiter = SlidingWindowLimiter(RateLimitConfig(requests=2, window=0.2, strategy="sliding_window"))
        assert limiter.allow("1.1.1.1") is True  # t=0
        time.sleep(0.1)
        assert limiter.allow("1.1.1.1") is True  # t=0.1
        assert limiter.allow("1.1.1.1") is False  # at limit
        time.sleep(0.15)
        # First request (t=0) has now expired (0.25 > 0.2), but second (t=0.1) hasn't
        assert limiter.allow("1.1.1.1") is True  # one slot freed
        assert limiter.allow("1.1.1.1") is False  # still at limit


# --- create_limiter factory ---


class TestCreateLimiter:
    def test_fixed_window(self):
        limiter = create_limiter(RateLimitConfig(requests=10, window=60.0, strategy="fixed_window"))
        assert isinstance(limiter, FixedWindowLimiter)

    def test_sliding_window(self):
        limiter = create_limiter(RateLimitConfig(requests=10, window=60.0, strategy="sliding_window"))
        assert isinstance(limiter, SlidingWindowLimiter)


# --- RateLimiterRegistry ---


class TestRateLimiterRegistry:
    def test_route_override(self):
        routes = [
            RouteConfig(
                path="/api/strict",
                methods=["GET"],
                upstream=UpstreamConfig(url="http://localhost:1"),
                rate_limit=RateLimitConfig(requests=2, window=60.0),
            ),
        ]
        registry = RateLimiterRegistry(
            RateLimitConfig(requests=100, window=60.0),
            routes,
        )
        # Route limit of 2 should apply, not global 100
        assert registry.check("/api/strict", "1.1.1.1") is True
        assert registry.check("/api/strict", "1.1.1.1") is True
        assert registry.check("/api/strict", "1.1.1.1") is False

    def test_global_fallback(self):
        routes = [
            RouteConfig(path="/api/open", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:1")),
        ]
        registry = RateLimiterRegistry(
            RateLimitConfig(requests=2, window=60.0),
            routes,
        )
        # No route-level limit — global should apply
        assert registry.check("/api/open", "1.1.1.1") is True
        assert registry.check("/api/open", "1.1.1.1") is True
        assert registry.check("/api/open", "1.1.1.1") is False

    def test_no_limits(self):
        routes = [
            RouteConfig(path="/api/free", methods=["GET"], upstream=UpstreamConfig(url="http://localhost:1")),
        ]
        registry = RateLimiterRegistry(None, routes)
        # No limits at all — always allow
        for _ in range(100):
            assert registry.check("/api/free", "1.1.1.1") is True


# --- Concurrency tests ---


class TestConcurrency:
    def test_fixed_window_thread_safe(self):
        limiter = FixedWindowLimiter(RateLimitConfig(requests=50, window=60.0))
        results = []

        def fire():
            results.append(limiter.allow("1.1.1.1"))

        threads = [threading.Thread(target=fire) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed = sum(1 for r in results if r)
        denied = sum(1 for r in results if not r)
        assert allowed == 50
        assert denied == 50

    def test_sliding_window_thread_safe(self):
        limiter = SlidingWindowLimiter(RateLimitConfig(requests=50, window=60.0, strategy="sliding_window"))
        results = []

        def fire():
            results.append(limiter.allow("1.1.1.1"))

        threads = [threading.Thread(target=fire) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed = sum(1 for r in results if r)
        denied = sum(1 for r in results if not r)
        assert allowed == 50
        assert denied == 50


# --- Integration tests: rate limiting through HTTP ---


class TestRateLimitHTTP:
    @pytest.fixture
    def upstream(self):
        server, base_url = start_mock_upstream()
        yield base_url
        server.shutdown()

    def test_route_rate_limit_returns_429(self, upstream):
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[RouteConfig(
                path="/api/limited",
                methods=["GET"],
                upstream=UpstreamConfig(url=upstream),
                rate_limit=RateLimitConfig(requests=3, window=60.0),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            for _ in range(3):
                resp = urllib.request.urlopen(f"{base_url}/api/limited")
                assert resp.status == 200

            req = urllib.request.Request(f"{base_url}/api/limited")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 429
            body = json.loads(exc_info.value.read())
            assert body["error"] == "rate_limit_exceeded"
        finally:
            server.shutdown()

    def test_global_rate_limit_returns_429(self, upstream):
        config = Config(
            gateway=GatewayConfig(
                port=0,
                global_rate_limit=RateLimitConfig(requests=2, window=60.0),
            ),
            routes=[RouteConfig(
                path="/api/test",
                methods=["GET"],
                upstream=UpstreamConfig(url=upstream),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            for _ in range(2):
                resp = urllib.request.urlopen(f"{base_url}/api/test")
                assert resp.status == 200

            req = urllib.request.Request(f"{base_url}/api/test")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 429
        finally:
            server.shutdown()

    def test_rate_limit_per_ip_different_routes(self, upstream):
        """Rate limits on different routes are independent."""
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[
                RouteConfig(
                    path="/api/a",
                    methods=["GET"],
                    upstream=UpstreamConfig(url=upstream),
                    rate_limit=RateLimitConfig(requests=2, window=60.0),
                ),
                RouteConfig(
                    path="/api/b",
                    methods=["GET"],
                    upstream=UpstreamConfig(url=upstream),
                    rate_limit=RateLimitConfig(requests=2, window=60.0),
                ),
            ],
        )
        server, base_url = make_gateway(config)
        try:
            # Exhaust /api/a
            for _ in range(2):
                urllib.request.urlopen(f"{base_url}/api/a")
            req = urllib.request.Request(f"{base_url}/api/a")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 429

            # /api/b should still be fine
            resp = urllib.request.urlopen(f"{base_url}/api/b")
            assert resp.status == 200
        finally:
            server.shutdown()

    def test_sliding_window_via_config(self, upstream):
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[RouteConfig(
                path="/api/slide",
                methods=["GET"],
                upstream=UpstreamConfig(url=upstream),
                rate_limit=RateLimitConfig(requests=2, window=0.2, strategy="sliding_window"),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            urllib.request.urlopen(f"{base_url}/api/slide")
            urllib.request.urlopen(f"{base_url}/api/slide")

            # Should be blocked
            req = urllib.request.Request(f"{base_url}/api/slide")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code == 429

            # Wait for window to expire
            time.sleep(0.3)

            # Should be allowed again
            resp = urllib.request.urlopen(f"{base_url}/api/slide")
            assert resp.status == 200
        finally:
            server.shutdown()

    def test_health_not_rate_limited(self, upstream):
        """The /health endpoint should never be rate limited."""
        config = Config(
            gateway=GatewayConfig(
                port=0,
                global_rate_limit=RateLimitConfig(requests=1, window=60.0),
            ),
            routes=[RouteConfig(
                path="/api/test",
                methods=["GET"],
                upstream=UpstreamConfig(url=upstream),
            )],
        )
        server, base_url = make_gateway(config)
        try:
            # Health should always work regardless of rate limits
            for _ in range(10):
                resp = urllib.request.urlopen(f"{base_url}/health")
                assert resp.status == 200
        finally:
            server.shutdown()
