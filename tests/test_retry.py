"""Tests for retry with backoff — fixed and exponential strategies."""

import json
import threading
import time
import urllib.request
import urllib.error

import pytest

from gateway.config import (
    Config, GatewayConfig, RetryConfig, RouteConfig, UpstreamConfig,
)
from gateway.proxy import ProxyRequest
from gateway.retry import forward_with_retry, _compute_delay
from tests.helpers import make_gateway
from tests.mock_upstream import start_mock_upstream


# --- Unit tests for _compute_delay ---


class TestComputeDelay:
    def test_fixed_backoff(self):
        config = RetryConfig(attempts=3, backoff="fixed", initial_delay=1.0)
        assert _compute_delay(config, 0) == 1.0
        assert _compute_delay(config, 1) == 1.0
        assert _compute_delay(config, 2) == 1.0

    def test_exponential_backoff(self):
        config = RetryConfig(attempts=4, backoff="exponential", initial_delay=1.0)
        assert _compute_delay(config, 0) == 1.0   # 1 * 2^0
        assert _compute_delay(config, 1) == 2.0   # 1 * 2^1
        assert _compute_delay(config, 2) == 4.0   # 1 * 2^2
        assert _compute_delay(config, 3) == 8.0   # 1 * 2^3

    def test_exponential_with_small_initial_delay(self):
        config = RetryConfig(attempts=3, backoff="exponential", initial_delay=0.1)
        assert _compute_delay(config, 0) == pytest.approx(0.1)
        assert _compute_delay(config, 1) == pytest.approx(0.2)
        assert _compute_delay(config, 2) == pytest.approx(0.4)


# --- Unit tests for forward_with_retry ---


class TestForwardWithRetry:
    @pytest.fixture
    def upstream(self):
        server, base_url = start_mock_upstream()
        yield base_url
        server.shutdown()

    def test_no_retry_on_success(self, upstream):
        config = RetryConfig(attempts=3, backoff="fixed", initial_delay=0.01, on=[502, 503])
        req = ProxyRequest(method="GET", path="/", headers={})
        resp = forward_with_retry(upstream, req, timeout=5.0, retry_config=config)
        assert resp.status == 200

    def test_retries_on_matching_status(self, upstream):
        """The /flaky endpoint returns 503, which should trigger retries."""
        config = RetryConfig(attempts=3, backoff="fixed", initial_delay=0.01, on=[503])
        req = ProxyRequest(method="GET", path="/flaky", headers={})

        start = time.time()
        resp = forward_with_retry(upstream, req, timeout=5.0, retry_config=config)
        elapsed = time.time() - start

        # Should have retried and still got 503 after all attempts
        assert resp.status == 503
        # Should have waited at least 2 * 0.01s (2 retries after first attempt)
        assert elapsed >= 0.02

    def test_no_retry_on_non_matching_status(self, upstream):
        """Status codes not in the 'on' list should not trigger retries."""
        config = RetryConfig(attempts=3, backoff="fixed", initial_delay=0.5, on=[502])
        req = ProxyRequest(method="GET", path="/flaky", headers={})  # returns 503

        start = time.time()
        resp = forward_with_retry(upstream, req, timeout=5.0, retry_config=config)
        elapsed = time.time() - start

        assert resp.status == 503
        # Should NOT have retried (503 not in on=[502]), so no delay
        assert elapsed < 0.3

    def test_connection_error_retried(self):
        """Retries on connection errors too."""
        config = RetryConfig(attempts=2, backoff="fixed", initial_delay=0.01, on=[503])
        req = ProxyRequest(method="GET", path="/", headers={})

        with pytest.raises(ConnectionError):
            forward_with_retry("http://127.0.0.1:1", req, timeout=1.0, retry_config=config)


# --- Countdown upstream: returns 503 N times, then 200 ---


class CountdownHandler:
    """An upstream that returns 503 for the first N requests, then 200."""
    remaining: int

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)


def start_countdown_upstream(fail_count: int):
    """Start an upstream that returns 503 for `fail_count` requests, then 200."""
    from http.server import HTTPServer, BaseHTTPRequestHandler

    lock = threading.Lock()
    state = {"remaining": fail_count}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                if state["remaining"] > 0:
                    state["remaining"] -= 1
                    self._respond(503, {"error": "not_yet"})
                else:
                    self._respond(200, {"message": "ok"})

        def _respond(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, base_url


class TestRetryWithCountdown:
    def test_succeeds_after_retries(self):
        """Upstream fails twice, then succeeds. With 3 attempts, should succeed."""
        server, upstream_url = start_countdown_upstream(fail_count=2)
        try:
            config = RetryConfig(attempts=3, backoff="fixed", initial_delay=0.01, on=[503])
            req = ProxyRequest(method="GET", path="/", headers={})
            resp = forward_with_retry(upstream_url, req, timeout=5.0, retry_config=config)
            assert resp.status == 200
        finally:
            server.shutdown()

    def test_fails_when_not_enough_attempts(self):
        """Upstream fails 3 times. With only 2 attempts, should return 503."""
        server, upstream_url = start_countdown_upstream(fail_count=3)
        try:
            config = RetryConfig(attempts=2, backoff="fixed", initial_delay=0.01, on=[503])
            req = ProxyRequest(method="GET", path="/", headers={})
            resp = forward_with_retry(upstream_url, req, timeout=5.0, retry_config=config)
            assert resp.status == 503
        finally:
            server.shutdown()


# --- Integration tests: retry through the HTTP gateway ---


class TestRetryHTTP:
    def test_gateway_retries_and_succeeds(self):
        upstream_server, upstream_url = start_countdown_upstream(fail_count=2)
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[RouteConfig(
                    path="/api/retry",
                    methods=["GET"],
                    upstream=UpstreamConfig(url=upstream_url),
                    retry=RetryConfig(attempts=3, backoff="fixed", initial_delay=0.01, on=[503]),
                )],
            )
            server, base_url = make_gateway(config)
            try:
                resp = urllib.request.urlopen(f"{base_url}/api/retry")
                assert resp.status == 200
                body = json.loads(resp.read())
                assert body["message"] == "ok"
            finally:
                server.shutdown()
        finally:
            upstream_server.shutdown()

    def test_gateway_retries_exhausted(self):
        upstream_server, upstream_url = start_countdown_upstream(fail_count=10)
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[RouteConfig(
                    path="/api/retry",
                    methods=["GET"],
                    upstream=UpstreamConfig(url=upstream_url),
                    retry=RetryConfig(attempts=2, backoff="fixed", initial_delay=0.01, on=[503]),
                )],
            )
            server, base_url = make_gateway(config)
            try:
                with pytest.raises(urllib.error.HTTPError) as exc_info:
                    urllib.request.urlopen(f"{base_url}/api/retry")
                assert exc_info.value.code == 503
            finally:
                server.shutdown()
        finally:
            upstream_server.shutdown()

    def test_no_retry_without_config(self):
        """Routes without retry config should not retry."""
        upstream_server, upstream_url = start_mock_upstream()
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[RouteConfig(
                    path="/flaky",
                    methods=["GET"],
                    upstream=UpstreamConfig(url=upstream_url),
                    # No retry config
                )],
            )
            server, base_url = make_gateway(config)
            try:
                with pytest.raises(urllib.error.HTTPError) as exc_info:
                    urllib.request.urlopen(f"{base_url}/flaky")
                assert exc_info.value.code == 503
            finally:
                server.shutdown()
        finally:
            upstream_server.shutdown()

    def test_exponential_backoff_timing(self):
        """Verify exponential backoff takes longer than fixed."""
        upstream_server, upstream_url = start_countdown_upstream(fail_count=10)
        try:
            config = Config(
                gateway=GatewayConfig(port=0),
                routes=[RouteConfig(
                    path="/api/retry",
                    methods=["GET"],
                    upstream=UpstreamConfig(url=upstream_url),
                    retry=RetryConfig(attempts=3, backoff="exponential", initial_delay=0.05, on=[503]),
                )],
            )
            server, base_url = make_gateway(config)
            try:
                start = time.time()
                with pytest.raises(urllib.error.HTTPError):
                    urllib.request.urlopen(f"{base_url}/api/retry")
                elapsed = time.time() - start
                # Exponential: 0.05 + 0.1 = 0.15s minimum delay
                assert elapsed >= 0.15
            finally:
                server.shutdown()
        finally:
            upstream_server.shutdown()
