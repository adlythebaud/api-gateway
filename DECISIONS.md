# DECISIONS.md — GatewayKit

## Language Choice: Python

Python was chosen over Go for familiarity. While Go's `net/http` and goroutines are a natural fit for a proxy server, Python's `http.server` / `asyncio` standard library and rapid development speed make it a strong choice when the priority is clean architecture and correctness under time pressure. The trade-off is raw concurrency performance, but for this scope that's acceptable.

Dependencies will be kept minimal: `pyyaml` for config parsing, standard library for HTTP, `threading` for concurrency.

## Feature Prioritization

Features are ordered by a combination of:
1. **Core requirement status** — the 5 non-negotiable requirements come first
2. **Architectural leverage** — features that force good abstractions early (e.g., middleware pipeline) are prioritized over isolated features
3. **Complexity-to-value ratio** — API key auth is a 15-minute feature that demonstrates production thinking; body transforms are complex and niche

The full ordering is documented in `docs/plan.md`.

## Key Design Decisions

### Middleware Pipeline Architecture

Almost every feature in the config (rate limiting, auth, transforms, circuit breaker) is a middleware that wraps the core proxy handler. The request flows through a chain:

```
Request → Router → [Auth] → [Rate Limiter] → [Request Transform] → Proxy → [Retry] → [Response Transform] → Client
```

Each middleware is optional and constructed from the route's config. Adding a new feature means writing a new middleware — no changes to existing code required. This is the single most important architectural decision because it determines how extensible the gateway is.

### Routing: Prefix Matching with Longest-First Ordering

Routes are matched by prefix (e.g., `/api/users` matches `/api/users/123`). Routes are sorted longest-first at startup to prevent ambiguous matches — `/api/users/admin` will match before `/api/users`.

### Rate Limiting: Thread-Safe In-Memory Counters

Rate limit state is stored in-memory using dictionaries protected by `threading.Lock`. Buckets are keyed by IP address (or a single global key depending on the `per` config).

- **Fixed window**: A counter and a window-start timestamp. When the window expires, both reset.
- **Sliding window**: A list of request timestamps. On each request, expired timestamps are pruned and the remaining count is checked against the limit.

Concurrency is critical here — the spec explicitly calls out 50 simultaneous requests hitting a rate-limited route. Lock granularity is per-route to avoid global contention.

### Timeouts

Duration strings like `"30s"` and `"1s"` are parsed into numeric seconds at config load time. Route-level `timeout` overrides the `global_timeout`. Timeouts are enforced on upstream HTTP requests.

### Config Generality

The gateway must work with any valid config following the schema, not just the provided example. Config parsing uses typed structures (dataclasses) with sensible defaults, and the router/middleware pipeline is constructed dynamically from whatever routes are present.

## What I'd Build Next (Given More Time)

In priority order:
1. **Body transforms** — JSON restructuring with dot-notation mapping, response envelopes
2. **Health checks** — Background thread pinging upstreams, removing unhealthy targets from rotation
3. **Graceful shutdown** — Drain in-flight requests before stopping
4. **Structured logging** — Request IDs, latency tracking, upstream status
5. **Config hot-reload** — Watch `gateway.yaml` for changes, rebuild the pipeline without restart

## AI Tool Usage

Claude Code was used to accelerate development — planning the architecture, generating boilerplate, and iterating on implementation. All generated code was reviewed and understood before inclusion.
