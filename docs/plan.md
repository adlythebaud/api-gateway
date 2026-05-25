# GatewayKit — Implementation Plan

## Standing Instructions

These apply throughout all phases — not just at the end:

- **DECISIONS.md**: Update `DECISIONS.md` (repo root) as we build. Every architectural decision, trade-off, or design choice should be documented when it's made, not retroactively. If we change our approach mid-build, document why.
- **README.md**: Keep `README.md` (repo root) up to date as features land. It should always reflect the current state: how to set up and run the project, how to run tests, what features are implemented, prerequisites/dependencies, and how to contribute changes.

---

## Phase 1: Foundation (Core Requirements)

The non-negotiable baseline. Everything else depends on this being solid.

### 1.1 — Project Setup
- Initialize project with `uv init` and `pyproject.toml`
- Add dependencies via `uv add`: `pyyaml`, `pytest` (dev)
- Create the entry point as a `gateway/` package
- Project install/run via `uv sync` and `uv run`

### 1.2 — Config Parsing
- Parse `gateway.yaml` into dataclasses/typed structures
- Accept config path via CLI argument or environment variable
- Duration string parsing (`"30s"`, `"1s"` → seconds)
- Validate config on startup — fail fast on malformed input

### 1.3 — Health Endpoint
- `GET /health` → `200 OK` with `{ "status": "healthy", "uptime_seconds": <int> }`
- Track server start time, compute uptime on each request
- Always available regardless of config

### 1.4 — Router
- Match incoming request path against configured routes (prefix matching)
- Longest-prefix-first ordering to avoid ambiguous matches
- Return `404` for unmatched routes
- Return `405 Method Not Allowed` for method mismatches

### 1.5 — Basic Reverse Proxy
- Forward matched requests to the upstream URL
- Copy request headers, method, and body to upstream
- Return upstream response (status, headers, body) to the client
- Apply `global_timeout` to upstream requests

### 1.6 — Strip Prefix
- When `strip_prefix: true`, remove the route's path prefix before forwarding
- e.g., `/api/products/123` → `/123`

### 1.7 — Mock Upstream Server
- Simple HTTP server with canned responses for testing
- Endpoints: basic JSON responses, a slow endpoint, a flaky 503 endpoint

### 1.8 — Tests (continuous from here on)
Tests are written alongside each feature, not batched at the end. Every step should include tests. Use multiple YAML config fixtures to verify the gateway works with different valid configs — not just the provided example.

- **Config parsing**: valid configs, malformed configs, missing fields with defaults, different route/value combinations
- **Health endpoint**: returns correct JSON, uptime increments
- **Routing**: 404 on unknown paths, 405 on wrong methods, prefix matching correctness
- **Proxying**: request/response forwarded correctly, timeout behavior, strip_prefix
- **Config fixtures**: maintain a `tests/configs/` directory with multiple YAML files exercising different schemas (minimal config, many routes, different rate limit strategies, etc.)

---

## Phase 2: High-Value Features

Features that demonstrate production thinking and are relatively quick to implement correctly.

### 2.1 — Rate Limiting (Fixed Window)
- In-memory counters keyed by IP (or global)
- Fixed window: counter resets after window expires
- Return `429 Too Many Requests` when limit exceeded
- Route-level rate limits override global rate limit
- Thread-safe — must handle concurrent requests correctly

### 2.2 — Rate Limiting (Sliding Window)
- Sliding window using timestamp log approach
- Count requests within a rolling window from the current time
- Same override/key semantics as fixed window

### 2.3 — Retry with Backoff
- Retry upstream requests on configured status codes (e.g., 502, 503, 504)
- Support `fixed` and `exponential` backoff strategies
- Respect `attempts` count and `initial_delay`
- Per-route configuration

### 2.4 — Auth (API Key)
- Check for required header (e.g., `X-API-Key`)
- Validate against list of allowed keys
- Return `401 Unauthorized` if missing or invalid
- Quick win — straightforward header check

### 2.5 — Tests for Phase 2
Each feature above includes its own tests as it's built. Key scenarios:
- Rate limiting under concurrent load (threading), verify 429 responses, test both strategies across different config values
- Retry behavior with flaky mock upstream, verify attempt counts and backoff timing
- Auth accept/reject with valid/invalid/missing keys
- Config fixtures with different rate limit windows, retry counts, auth headers

---

## Phase 3: Stretch Features

Implement as many as time allows, in this priority order.

### 3.1 — Circuit Breaker
- Track failure count per route within a time window
- Trip the breaker after `threshold` failures
- When tripped: return `503` with `{ "error": "service_unavailable", "retry_after": <seconds> }`
- After `cooldown`, allow a probe request through (half-open state)

### 3.2 — Request/Response Header Transforms
- Add headers to outgoing requests (`request_transform.headers.add`)
- Remove headers from outgoing requests (`request_transform.headers.remove`)
- Add/remove headers on responses (`response_transform.headers`)
- Support dynamic values: `$request_time`, `$response_time`, `$route_path`

### 3.3 — Load Balancing
- Round robin across multiple upstream targets
- Weighted round robin (route more traffic to higher-weight targets)
- Thread-safe target selection

### 3.4 — Body Transforms
- Request body mapping: restructure JSON using dot-notation paths
- `$literal:` prefix for injecting static values
- `$request_time` for dynamic timestamp injection
- Response body envelope: wrap upstream response in a metadata wrapper

### 3.5 — Health Checks
- Background thread pinging upstream `/healthz` (or configured path) on an interval
- Track consecutive failures against `unhealthy_threshold`
- Remove unhealthy targets from load balancer rotation

### 3.6 — Tests for Phase 3
Each feature above includes its own tests as it's built. Key scenarios:
- Circuit breaker trip/cooldown/half-open cycle
- Header transforms verified end-to-end (added headers present, removed headers absent)
- Load balancer distribution matches configured weights
- Body transforms: dot-notation mapping, `$literal:` values, response envelopes
- Health check marks upstream unhealthy after threshold, removes from rotation

---

## Deliverables Checklist

- [ ] `gateway.yaml` — provided config
- [ ] `DECISIONS.md` — architectural decisions and trade-offs (repo root)
- [ ] `README.md` — setup, run, test instructions, feature checklist
- [ ] Working test suite (`pytest` or `unittest`), runnable with a single command
- [ ] Mock upstream server included in repo
- [ ] Clean commit history showing progression
