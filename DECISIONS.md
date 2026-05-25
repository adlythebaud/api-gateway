# DECISIONS.md — GatewayKit

## Language Choice: Python

Python was chosen over Go for familiarity. While Go's `net/http` and goroutines are a natural fit for a proxy server, Python's `http.server` / `asyncio` standard library and rapid development speed make it a strong choice when the priority is clean architecture and correctness under time pressure. The trade-off is raw concurrency performance, but for this scope that's acceptable.

Dependencies will be kept minimal: `pyyaml` for config parsing, standard library for HTTP, `threading` for concurrency.

## Feature Prioritization

Features were prioritized using three criteria:

1. **Core requirement status** — the 5 non-negotiable requirements (start on port 8080, health endpoint, basic proxying, method filtering, config generality) came first. These are the foundation everything else builds on.
2. **Architectural leverage** — features that force good abstractions early were prioritized over isolated features. For example, rate limiting was built early because it established the pattern of per-route middleware with a registry, which circuit breaker and auth later followed.
3. **Complexity-to-value ratio** — quick wins that demonstrate production thinking were prioritized over complex niche features. API key auth is a straightforward header check but shows the gateway handles security. Body transforms require deep JSON restructuring for a narrow use case, so they were deprioritized.

**The actual build order was:**
1. Config parsing → Health endpoint → Router → Reverse proxy → Strip prefix (core requirements)
2. Request logging (observability for everything that follows)
3. Rate limiting (fixed + sliding window — establishes the middleware registry pattern)
4. Retry with backoff (resilience, builds on the proxy layer)
5. API key auth (security, quick win)
6. Circuit breaker (resilience, follows the registry pattern from rate limiting)
7. Header transforms (request/response modification)
8. Load balancing + health checks (these are coupled — health checks remove targets from the load balancer)

**What was deprioritized:** Body transforms (request body mapping and response envelopes) were left for last because they're the most complex feature with the narrowest use case. The gateway works fully without them.

The full phased plan is documented in `docs/plan.md`.

## Key Design Decisions

### Middleware Pipeline Architecture

Almost every feature in the config (rate limiting, auth, transforms, circuit breaker) is a middleware step that runs before or after the core proxy handler. The request flows through a chain:

```
Request → Router → [Auth] → [Rate Limiter] → [Circuit Breaker] → [Request Transform] → Proxy/[Retry] → [Response Transform] → Client
```

Each step is optional and only runs if the route's config enables it. The ordering is deliberate:
- **Auth before rate limiting** — unauthenticated requests don't consume rate limit quota
- **Rate limiting before circuit breaker** — rate-limited requests don't count as upstream failures
- **Circuit breaker before proxy** — if the circuit is open, we fail fast without touching the upstream
- **Retry wraps the proxy call** — retries are transparent to the rest of the pipeline

**Extensibility:** Adding a new feature means writing a new module (e.g., `gateway/new_feature.py`), adding a registry class if it needs per-route state, and inserting a check in `_handle()` or `_proxy()`. No existing code needs to change. For example, adding IP allowlisting would be a new check after routing and before auth — one `if` block and one new module.

**Trade-offs:** The pipeline is implemented as sequential `if` checks in the handler rather than a composable middleware chain (like Go's `http.Handler` wrapping). This is simpler to read and debug but means the ordering is hardcoded in `server.py`. For this scope that's the right call — a fully composable pipeline adds indirection without enough benefit for ~7 middleware steps. If the gateway grew to 20+ middleware, refactoring to a chain pattern would be warranted.

### Server: stdlib `http.server` with Class-Level State

The gateway uses Python's `http.server.HTTPServer` and `BaseHTTPRequestHandler`. Config and start time are set as class attributes on the handler, avoiding global state. Each request handler instance reads from the shared class-level config. This is simple, requires no dependencies, and is sufficient for the gateway's needs. The `ThreadingHTTPServer` variant or a thread pool can be swapped in later if needed for concurrency.

### Health Endpoint: Always Available

`GET /health` is handled before route matching — it returns `{"status": "healthy", "uptime_seconds": <int>}` regardless of what routes are configured. This is hardcoded behavior, not config-driven, per the spec requirement.

### YAML `on:` Key Quirk

YAML parses bare `on:` as the boolean `True`. The retry config parser checks for both the string key `"on"` and the boolean key `True` to handle this transparently, so config authors don't need to quote the key.

### Routing: Prefix Matching with Longest-First Ordering

Routes are matched by prefix (e.g., `/api/users` matches `/api/users/123`). Routes are sorted longest-first at startup to prevent ambiguous matches — `/api/users/admin` will match before `/api/users`.

### Rate Limiting: Thread-Safe In-Memory Counters

Rate limit state is stored in-memory using dictionaries protected by `threading.Lock`. Buckets are keyed by IP address (or a single global key depending on the `per` config).

- **Fixed window**: A counter and a window-start timestamp. When the window expires, both reset.
- **Sliding window**: A list of request timestamps. On each request, expired timestamps are pruned and the remaining count is checked against the limit.

Concurrency is critical here — the spec explicitly calls out 50 simultaneous requests hitting a rate-limited route. Lock granularity is per-route to avoid global contention.

### Data Storage: In-Memory Only

All gateway state (rate limit counters, circuit breaker status, health check results) is stored in-memory. No database is required for this scope. If the gateway needed to survive restarts without losing state or share state across multiple gateway instances, I would add a persistent data store — likely Postgres running in a Docker container — to back the rate limiter and circuit breaker. For now, in-memory is simpler, faster, and has zero infrastructure dependencies.

### Timeouts

Duration strings like `"30s"` and `"1s"` are parsed into numeric seconds at config load time. Route-level `timeout` overrides the `global_timeout`. Timeouts are enforced on upstream HTTP requests.

### Request Logging

Every request is logged with client IP, method, path, matched route, status code, and duration in milliseconds. Log levels are based on status: INFO for 2xx/3xx, WARNING for 4xx, ERROR for 5xx. Uses Python's standard `logging` module with a named `gatewaykit` logger, which makes it easy to configure log levels or add handlers externally. Logs are suppressed during tests via `conftest.py`.

### Retry with Backoff

Retry is opt-in per route. When configured, the gateway retries upstream requests on specific status codes (e.g., 502, 503, 504). Two backoff strategies are supported: `fixed` (constant delay) and `exponential` (delay doubles each attempt). Connection errors are also retried. The retry logic lives in its own module (`gateway/retry.py`) and wraps the existing `forward_request` function — no changes to the proxy layer were needed.

### API Key Auth

Auth is a simple header check — if a route has `auth` configured, the gateway checks for the specified header and validates it against the list of allowed keys. Returns 401 if missing or invalid. Auth runs before rate limiting in the pipeline so that unauthenticated requests don't consume rate limit quota. Routes without auth config are unaffected — extra headers are simply forwarded to the upstream.

### Standalone Mode

The `--standalone` flag auto-detects all upstream ports from the config and starts mock upstreams on them. This means `uv run gatewaykit gateway.yaml --standalone` works out of the box with any config file, no hardcoded ports or separate scripts needed. The mock upstream logic lives in `gateway/standalone.py` and is only imported when the flag is present, keeping the production code path clean.

### Circuit Breaker

The circuit breaker uses three states: closed (normal), open (rejecting), and half-open (probing). Failures are tracked per-route with timestamps — only failures within the configured window count toward the threshold. When tripped, the gateway returns `503 { "error": "service_unavailable", "retry_after": <seconds> }` immediately without contacting the upstream. After the cooldown, one probe request is allowed through. If it succeeds, the circuit closes; if it fails, it re-opens. Connection errors and timeouts also count as failures, not just 5xx responses.

### Request/Response Header Transforms

Transforms are applied as two steps in the proxy pipeline: request transforms run after building the headers dict but before forwarding, and response transforms run after receiving the upstream response but before sending to the client. Header removal is case-insensitive. Dynamic values (`$request_time`, `$response_time`, `$route_path`) are resolved at request time. This is stateless — no shared state or locking needed.

### Load Balancing

The load balancer builds a target sequence at startup: for round robin it's the target list as-is, for weighted round robin it expands targets by weight (e.g., weights [3,1] become [A,A,A,B]). Selection cycles through the healthy subset of this sequence. This approach is simple, deterministic, and thread-safe with a single lock. The trade-off is that weight changes require a restart, but that's acceptable since the config is loaded once at startup.

### Health Checks

Background daemon threads ping each upstream target's health endpoint on a configurable interval. After `unhealthy_threshold` consecutive failures, the target is removed from the load balancer's healthy set. When a health check succeeds after being unhealthy, the target is restored. Health checkers are only started for routes that have both `health_check` config and multiple upstream targets — single-URL routes don't need them.

### Config Generality

The gateway must work with any valid config following the schema, not just the provided example. Config parsing uses typed structures (dataclasses) with sensible defaults, and the router/middleware pipeline is constructed dynamically from whatever routes are present.

## Production Hardening

A self-code-review identified 7 critical/high-severity issues. All were fixed:

### 1. Unhandled exceptions crash the handler
**Problem:** Any unhandled exception in `_handle()` (e.g., malformed headers) would kill the request handler thread with no response to the client.
**Fix:** Wrapped the entire request flow in a try/except. Unhandled exceptions now log a full traceback and return `500 Internal Server Error` instead of crashing.

### 2. Malformed Content-Length crashes the handler
**Problem:** `int(self.headers.get("Content-Length", 0))` raises `ValueError` on non-numeric values like `"abc"`. A malicious client could crash handlers at will.
**Fix:** Wrapped in try/except, returns `400 Bad Request` for non-numeric Content-Length.

### 3. No max body size limit
**Problem:** A client sending `Content-Length: 1073741824` (1 GB) would cause the gateway to allocate 1 GB of memory. Multiple concurrent large requests exhaust memory.
**Fix:** Added a 10 MB `MAX_BODY_SIZE` limit. Requests exceeding it get `413 Request Body Too Large` before any data is read.

### 4. Rate limiter memory leak
**Problem:** The rate limiter bucket dictionaries grow unbounded — one entry per unique client IP, never cleaned up. Under sustained traffic from many IPs, memory grows linearly until OOM.
**Fix:** Added periodic cleanup in both `FixedWindowLimiter` and `SlidingWindowLimiter`. Stale buckets (not accessed in `2 * window`) are pruned during the next `allow()` call. Cleanup runs under the existing lock, so no additional contention.

### 5. Upstream response read can hang forever
**Problem:** `http.client` timeout only applies to connection establishment. If the upstream accepts the connection but sends the response body slowly (or never), `resp.read()` blocks the thread indefinitely. Under load, this exhausts all handler threads.
**Fix:** After `conn.request()`, explicitly set `conn.sock.settimeout(timeout)` to enforce the same timeout on the response read. `socket.timeout` is caught and raised as `TimeoutError`.

### 6. Health checker race condition
**Problem:** `HealthChecker._check()` and `_record_failure()` both read and write `_healthy` and `_consecutive_failures` without synchronization. Two concurrent calls could lose a failure increment or produce inconsistent state.
**Fix:** Added a `threading.Lock` to `HealthChecker`. All reads/writes to `_healthy` and `_consecutive_failures` are now protected. Also narrowed the exception catch from bare `except Exception` to `except (OSError, http.client.HTTPException, socket.timeout)` and added `try/finally` on the connection to ensure `conn.close()` always runs.

### 7. Circuit breaker probe timeout race
**Problem:** If a half-open probe request hangs for longer than the cooldown period, the breaker stays in HALF_OPEN indefinitely — no new probes are allowed, and the circuit never recovers.
**Fix:** Track `_probe_started_at` separately. If a probe has been in flight longer than the cooldown, allow a new probe through. This prevents a hanging probe from permanently blocking recovery.

## What I'd Build Next (Given More Time)

In priority order:
1. **Body transforms** — JSON restructuring with dot-notation mapping, response envelopes
2. **Graceful shutdown** — Drain in-flight requests before stopping
3. **Config hot-reload** — Watch `gateway.yaml` for changes, rebuild the pipeline without restart
4. **Request ID propagation** — Generate unique request IDs, pass through the pipeline for tracing
5. **Metrics endpoint** — Expose request counts, latencies, and error rates per route

## AI Tool Usage

Claude Code was used to accelerate development — planning the architecture, generating boilerplate, and iterating on implementation. All generated code was reviewed and understood before inclusion.
