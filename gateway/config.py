"""Config parsing for GatewayKit. Loads gateway.yaml into typed dataclasses."""

import os
import re
import sys
from dataclasses import dataclass, field

import yaml


def parse_duration(value: str) -> float:
    """Parse a duration string like '30s', '1m', '500ms' into seconds."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|s|m)", value.strip())
    if not match:
        raise ValueError(f"Invalid duration string: {value!r}. Expected format like '30s', '1m', '500ms'.")
    amount = float(match.group(1))
    unit = match.group(2)
    if unit == "ms":
        return amount / 1000
    if unit == "m":
        return amount * 60
    return amount


@dataclass
class RateLimitConfig:
    requests: int
    window: float  # seconds
    strategy: str = "fixed_window"
    per: str = "ip"


@dataclass
class RetryConfig:
    attempts: int = 1
    backoff: str = "fixed"
    initial_delay: float = 1.0  # seconds
    on: list[int] = field(default_factory=lambda: [502, 503, 504])


@dataclass
class UpstreamTarget:
    url: str
    weight: int = 1


@dataclass
class HealthCheckConfig:
    path: str = "/healthz"
    interval: float = 30.0  # seconds
    unhealthy_threshold: int = 3


@dataclass
class UpstreamConfig:
    url: str | None = None
    targets: list[UpstreamTarget] = field(default_factory=list)
    balance: str = "round_robin"
    timeout: float | None = None  # None means use global_timeout


@dataclass
class HeaderTransformConfig:
    add: dict[str, str] = field(default_factory=dict)
    remove: list[str] = field(default_factory=list)


@dataclass
class BodyMappingConfig:
    mapping: dict[str, str] = field(default_factory=dict)


@dataclass
class ResponseBodyConfig:
    envelope: dict | None = None


@dataclass
class RequestTransformConfig:
    headers: HeaderTransformConfig = field(default_factory=HeaderTransformConfig)
    body: BodyMappingConfig = field(default_factory=BodyMappingConfig)


@dataclass
class ResponseTransformConfig:
    headers: HeaderTransformConfig = field(default_factory=HeaderTransformConfig)
    body: ResponseBodyConfig = field(default_factory=ResponseBodyConfig)


@dataclass
class AuthConfig:
    type: str = "api_key"
    header: str = "X-API-Key"
    keys: list[str] = field(default_factory=list)


@dataclass
class CircuitBreakerConfig:
    threshold: int = 5
    window: float = 60.0  # seconds
    cooldown: float = 30.0  # seconds


@dataclass
class RouteConfig:
    path: str
    methods: list[str]
    strip_prefix: bool = False
    upstream: UpstreamConfig = field(default_factory=UpstreamConfig)
    rate_limit: RateLimitConfig | None = None
    retry: RetryConfig | None = None
    health_check: HealthCheckConfig | None = None
    request_transform: RequestTransformConfig | None = None
    response_transform: ResponseTransformConfig | None = None
    auth: AuthConfig | None = None
    circuit_breaker: CircuitBreakerConfig | None = None


@dataclass
class GatewayConfig:
    port: int = 8080
    global_timeout: float = 30.0  # seconds
    global_rate_limit: RateLimitConfig | None = None


@dataclass
class Config:
    gateway: GatewayConfig
    routes: list[RouteConfig]


def _parse_rate_limit(data: dict) -> RateLimitConfig:
    return RateLimitConfig(
        requests=data["requests"],
        window=parse_duration(data["window"]),
        strategy=data.get("strategy", "fixed_window"),
        per=data.get("per", "ip"),
    )


def _parse_upstream(data: dict) -> UpstreamConfig:
    targets = []
    for t in data.get("targets", []):
        targets.append(UpstreamTarget(url=t["url"], weight=t.get("weight", 1)))

    timeout = None
    if "timeout" in data:
        timeout = parse_duration(data["timeout"])

    return UpstreamConfig(
        url=data.get("url"),
        targets=targets,
        balance=data.get("balance", "round_robin"),
        timeout=timeout,
    )


def _parse_retry(data: dict) -> RetryConfig:
    # YAML parses bare `on:` as boolean True, so check both keys
    retry_on = data.get("on", data.get(True, [502, 503, 504]))
    return RetryConfig(
        attempts=data.get("attempts", 1),
        backoff=data.get("backoff", "fixed"),
        initial_delay=parse_duration(data["initial_delay"]) if "initial_delay" in data else 1.0,
        on=retry_on,
    )


def _parse_header_transform(data: dict) -> HeaderTransformConfig:
    return HeaderTransformConfig(
        add=data.get("add", {}),
        remove=data.get("remove", []),
    )


def _parse_request_transform(data: dict) -> RequestTransformConfig:
    headers = _parse_header_transform(data["headers"]) if "headers" in data else HeaderTransformConfig()
    body = BodyMappingConfig(mapping=data["body"]["mapping"]) if "body" in data and "mapping" in data["body"] else BodyMappingConfig()
    return RequestTransformConfig(headers=headers, body=body)


def _parse_response_transform(data: dict) -> ResponseTransformConfig:
    headers = _parse_header_transform(data["headers"]) if "headers" in data else HeaderTransformConfig()
    body = ResponseBodyConfig(envelope=data["body"]["envelope"]) if "body" in data and "envelope" in data["body"] else ResponseBodyConfig()
    return ResponseTransformConfig(headers=headers, body=body)


def _parse_auth(data: dict) -> AuthConfig:
    return AuthConfig(
        type=data.get("type", "api_key"),
        header=data.get("header", "X-API-Key"),
        keys=data.get("keys", []),
    )


def _parse_circuit_breaker(data: dict) -> CircuitBreakerConfig:
    return CircuitBreakerConfig(
        threshold=data.get("threshold", 5),
        window=parse_duration(data["window"]) if "window" in data else 60.0,
        cooldown=parse_duration(data["cooldown"]) if "cooldown" in data else 30.0,
    )


def _parse_route(data: dict) -> RouteConfig:
    if "path" not in data:
        raise ValueError("Route missing required field: 'path'")
    if "methods" not in data:
        raise ValueError(f"Route {data['path']!r} missing required field: 'methods'")
    if "upstream" not in data:
        raise ValueError(f"Route {data['path']!r} missing required field: 'upstream'")

    upstream = _parse_upstream(data["upstream"])
    if not upstream.url and not upstream.targets:
        raise ValueError(f"Route {data['path']!r} upstream must have either 'url' or 'targets'")

    route = RouteConfig(
        path=data["path"],
        methods=[m.upper() for m in data["methods"]],
        strip_prefix=data.get("strip_prefix", False),
        upstream=upstream,
    )

    if "rate_limit" in data:
        route.rate_limit = _parse_rate_limit(data["rate_limit"])
    if "retry" in data:
        route.retry = _parse_retry(data["retry"])
    if "health_check" in data:
        route.health_check = HealthCheckConfig(
            path=data["health_check"].get("path", "/healthz"),
            interval=parse_duration(data["health_check"]["interval"]) if "interval" in data["health_check"] else 30.0,
            unhealthy_threshold=data["health_check"].get("unhealthy_threshold", 3),
        )
    if "request_transform" in data:
        route.request_transform = _parse_request_transform(data["request_transform"])
    if "response_transform" in data:
        route.response_transform = _parse_response_transform(data["response_transform"])
    if "auth" in data:
        route.auth = _parse_auth(data["auth"])
    if "circuit_breaker" in data:
        route.circuit_breaker = _parse_circuit_breaker(data["circuit_breaker"])

    return route


def load_config(path: str) -> Config:
    """Load and parse a gateway config YAML file. Raises on invalid config."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError("Config file must be a YAML mapping at the top level")
    if "gateway" not in raw:
        raise ValueError("Config missing required section: 'gateway'")
    if "routes" not in raw or not raw["routes"]:
        raise ValueError("Config missing required section: 'routes' (must have at least one route)")

    gw = raw["gateway"]
    gateway = GatewayConfig(
        port=gw.get("port", 8080),
        global_timeout=parse_duration(gw["global_timeout"]) if "global_timeout" in gw else 30.0,
    )
    if "global_rate_limit" in gw:
        gateway.global_rate_limit = _parse_rate_limit(gw["global_rate_limit"])

    routes = [_parse_route(r) for r in raw["routes"]]

    return Config(gateway=gateway, routes=routes)


def get_config_path() -> str:
    """Get config file path from CLI args or GATEWAY_CONFIG env var."""
    if len(sys.argv) > 1:
        return sys.argv[1]

    env_path = os.environ.get("GATEWAY_CONFIG")
    if env_path:
        return env_path

    print("Usage: gatewaykit <config.yaml> [--standalone]", file=sys.stderr)
    print("   or: GATEWAY_CONFIG=<config.yaml> gatewaykit", file=sys.stderr)
    print("\nOptions:", file=sys.stderr)
    print("  --standalone  Start mock upstreams for all ports in the config", file=sys.stderr)
    sys.exit(1)
