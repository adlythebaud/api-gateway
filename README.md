# GatewayKit

A lightweight, config-driven API gateway built from scratch in Python. Routes client requests to upstream services with support for rate limiting, retries, auth, transforms, and more — all driven by a single YAML config file.

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (Python package manager)

## Setup

```bash
# Install dependencies
uv sync
```

## Running the Gateway

```bash
# Quickstart: start gateway with mock upstreams auto-detected from config
uv run gatewaykit gateway.yaml --standalone

# Without --standalone (requires real upstreams running)
uv run gatewaykit gateway.yaml
```

The `--standalone` flag reads the config, finds all upstream ports, and starts mock upstreams on them automatically. No separate scripts or terminals needed.

Then hit the API (use `-i` to see status codes and headers):

```bash
curl -i http://localhost:8080/health                        # 200, healthy
curl -i http://localhost:8080/api/users                     # 200, proxied to mock upstream
curl -i http://localhost:8080/api/unknown                   # 404
curl -i -X DELETE http://localhost:8080/api/users           # 405 (only GET/POST allowed)
```

The mock upstreams echo back request details (method, path, headers, body) and support special paths: `/healthz` (200), `/slow` (200 after 10s), `/flaky` (503).

### Running Mock Upstreams Manually

If you'd rather start mock upstreams yourself (e.g., on specific ports):

```bash
# Start mock upstreams on specific ports
uv run python scripts/mock_upstream.py 3001 3002

# Or all default ports (3001–3006)
uv run python scripts/mock_upstream.py
```

Then start the gateway without `--standalone` in a separate terminal:

```bash
uv run gatewaykit gateway.yaml
```

## Running Tests

```bash
uv run pytest
```

## Project Structure

```
gateway/          # Main gateway package
tests/            # Test suite
scripts/          # Helper scripts (mock upstream, etc.)
docs/             # Planning and design docs
gateway.yaml      # Example gateway config
DECISIONS.md      # Architectural decisions and trade-offs
```

## Implemented Features

### Core Features
- [x] Config parsing (YAML → typed structures)
- [x] Health endpoint (`GET /health`)
- [x] Route matching with prefix support
- [x] Method filtering (405 on mismatch)
- [x] Reverse proxy (forward to upstream)
- [x] Strip prefix

### High-Value Features
- [x] Rate limiting (fixed window)
- [x] Rate limiting (sliding window)
- [x] Retry with backoff
- [x] API key auth

### Stretch Features
- [ ] Circuit breaker
- [ ] Request/response header transforms
- [ ] Load balancing (round robin / weighted)
- [ ] Body transforms
- [ ] Health checks

## Contributing

1. Install dependencies: `uv sync`
2. Make your changes in the `gateway/` package
3. Add tests in `tests/`
4. Run the test suite: `uv run pytest`
5. Ensure all tests pass before committing
