"""Tests for config parsing — covers duration parsing, valid configs, malformed configs, and multiple fixtures."""

import os
import tempfile

import pytest

from gateway.config import Config, load_config, parse_duration

CONFIGS_DIR = os.path.join(os.path.dirname(__file__), "configs")


# --- Duration parsing ---


class TestParseDuration:
    def test_seconds(self):
        assert parse_duration("30s") == 30.0

    def test_seconds_single(self):
        assert parse_duration("1s") == 1.0

    def test_milliseconds(self):
        assert parse_duration("500ms") == 0.5

    def test_minutes(self):
        assert parse_duration("2m") == 120.0

    def test_whitespace(self):
        assert parse_duration("  10s  ") == 10.0

    def test_invalid_unit(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_duration("10h")

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_duration("fast")

    def test_empty_string(self):
        with pytest.raises(ValueError, match="Invalid duration"):
            parse_duration("")


# --- Config loading: valid configs ---


class TestLoadConfigValid:
    def test_provided_config(self):
        config = load_config(os.path.join(os.path.dirname(__file__), "..", "gateway.yaml"))
        assert config.gateway.port == 8080
        assert config.gateway.global_timeout == 30.0
        assert config.gateway.global_rate_limit is not None
        assert config.gateway.global_rate_limit.requests == 100
        assert config.gateway.global_rate_limit.strategy == "fixed_window"
        assert len(config.routes) == 5

    def test_minimal_config(self):
        config = load_config(os.path.join(CONFIGS_DIR, "minimal.yaml"))
        assert config.gateway.port == 9090
        assert config.gateway.global_timeout == 30.0  # default
        assert config.gateway.global_rate_limit is None
        assert len(config.routes) == 1
        assert config.routes[0].path == "/api/hello"
        assert config.routes[0].methods == ["GET"]
        assert config.routes[0].upstream.url == "http://localhost:4001"
        assert config.routes[0].strip_prefix is False

    def test_multi_route_config(self):
        config = load_config(os.path.join(CONFIGS_DIR, "multi_route.yaml"))
        assert config.gateway.global_timeout == 15.0
        assert config.gateway.global_rate_limit.requests == 50
        assert config.gateway.global_rate_limit.per == "global"
        assert len(config.routes) == 3

        # Route with override timeout
        users = config.routes[0]
        assert users.path == "/v1/users"
        assert users.upstream.timeout == 3.0
        assert users.rate_limit.requests == 20
        assert users.rate_limit.strategy == "fixed_window"

        # Route with strip_prefix
        items = config.routes[1]
        assert items.strip_prefix is True
        assert items.upstream.timeout is None  # uses global

    def test_auth_and_retry_config(self):
        config = load_config(os.path.join(CONFIGS_DIR, "with_auth_and_retry.yaml"))

        secure = config.routes[0]
        assert secure.auth is not None
        assert secure.auth.header == "Authorization"
        assert len(secure.auth.keys) == 3

        flaky = config.routes[1]
        assert flaky.retry is not None
        assert flaky.retry.attempts == 5
        assert flaky.retry.backoff == "fixed"
        assert flaky.retry.initial_delay == 0.5
        assert flaky.retry.on == [500, 502, 503]

    def test_load_balanced_config(self):
        config = load_config(os.path.join(CONFIGS_DIR, "load_balanced.yaml"))
        route = config.routes[0]
        assert route.upstream.url is None
        assert len(route.upstream.targets) == 3
        assert route.upstream.targets[0].weight == 5
        assert route.upstream.balance == "weighted_round_robin"
        assert route.health_check is not None
        assert route.health_check.path == "/health"
        assert route.health_check.interval == 10.0
        assert route.health_check.unhealthy_threshold == 2


# --- Config loading: provided config feature parsing ---


class TestProvidedConfigDetails:
    @pytest.fixture
    def config(self):
        return load_config(os.path.join(os.path.dirname(__file__), "..", "gateway.yaml"))

    def test_users_route(self, config: Config):
        route = config.routes[0]
        assert route.path == "/api/users"
        assert route.rate_limit.strategy == "sliding_window"
        assert route.rate_limit.requests == 30

    def test_orders_route_retry(self, config: Config):
        route = config.routes[1]
        assert route.path == "/api/orders"
        assert route.upstream.timeout == 5.0
        assert route.retry is not None
        assert route.retry.attempts == 3
        assert route.retry.backoff == "exponential"
        assert route.retry.initial_delay == 1.0
        assert route.retry.on == [502, 503, 504]

    def test_products_load_balanced(self, config: Config):
        route = config.routes[2]
        assert route.path == "/api/products"
        assert route.strip_prefix is True
        assert route.upstream.url is None
        assert len(route.upstream.targets) == 2
        assert route.upstream.targets[0].url == "http://localhost:3003"
        assert route.upstream.targets[0].weight == 3

    def test_legacy_transforms(self, config: Config):
        route = config.routes[3]
        assert route.request_transform is not None
        assert route.request_transform.headers.add["X-Gateway"] == "gatewaykit"
        assert "X-Debug" in route.request_transform.headers.remove
        assert route.request_transform.body.mapping["user.id"] == "userId"
        assert route.response_transform is not None
        assert route.response_transform.headers.add["X-Served-By"] == "gatewaykit"
        assert route.response_transform.body.envelope is not None

    def test_internal_auth_and_circuit_breaker(self, config: Config):
        route = config.routes[4]
        assert route.auth is not None
        assert route.auth.keys == ["sk_live_abc123", "sk_live_def456"]
        assert route.circuit_breaker is not None
        assert route.circuit_breaker.threshold == 5
        assert route.circuit_breaker.window == 60.0
        assert route.circuit_breaker.cooldown == 30.0

    def test_methods_uppercased(self, config: Config):
        for route in config.routes:
            for method in route.methods:
                assert method == method.upper()


# --- Config loading: invalid configs ---


class TestLoadConfigInvalid:
    def _write_temp(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path.yaml")

    def test_empty_file(self):
        path = self._write_temp("")
        with pytest.raises(ValueError, match="YAML mapping"):
            load_config(path)
        os.unlink(path)

    def test_missing_gateway_section(self):
        path = self._write_temp("routes:\n  - path: /foo\n    methods: [GET]\n    upstream:\n      url: http://localhost:1234\n")
        with pytest.raises(ValueError, match="gateway"):
            load_config(path)
        os.unlink(path)

    def test_missing_routes_section(self):
        path = self._write_temp("gateway:\n  port: 8080\n")
        with pytest.raises(ValueError, match="routes"):
            load_config(path)
        os.unlink(path)

    def test_route_missing_path(self):
        path = self._write_temp("gateway:\n  port: 8080\nroutes:\n  - methods: [GET]\n    upstream:\n      url: http://localhost:1234\n")
        with pytest.raises(ValueError, match="path"):
            load_config(path)
        os.unlink(path)

    def test_route_missing_methods(self):
        path = self._write_temp("gateway:\n  port: 8080\nroutes:\n  - path: /foo\n    upstream:\n      url: http://localhost:1234\n")
        with pytest.raises(ValueError, match="methods"):
            load_config(path)
        os.unlink(path)

    def test_route_missing_upstream(self):
        path = self._write_temp("gateway:\n  port: 8080\nroutes:\n  - path: /foo\n    methods: [GET]\n")
        with pytest.raises(ValueError, match="upstream"):
            load_config(path)
        os.unlink(path)

    def test_upstream_no_url_or_targets(self):
        path = self._write_temp("gateway:\n  port: 8080\nroutes:\n  - path: /foo\n    methods: [GET]\n    upstream:\n      balance: round_robin\n")
        with pytest.raises(ValueError, match="url.*targets"):
            load_config(path)
        os.unlink(path)

    def test_invalid_duration_in_config(self):
        path = self._write_temp("gateway:\n  port: 8080\n  global_timeout: 'forever'\nroutes:\n  - path: /foo\n    methods: [GET]\n    upstream:\n      url: http://localhost:1234\n")
        with pytest.raises(ValueError, match="Invalid duration"):
            load_config(path)
        os.unlink(path)
