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
# Quickstart: start mock upstreams + gateway together
./scripts/dev.sh

# Or with a custom config
./scripts/dev.sh tests/configs/multi_route.yaml
```

You can also run them separately:

```bash
# Pass a config file as a CLI argument
uv run gatewaykit gateway.yaml

# Or via python module
uv run python -m gateway gateway.yaml
```

### Running with Mock Upstreams

The easiest way is the dev script, which starts mock upstreams and the gateway together:

```bash
# Terminal 1: Start everything
./scripts/dev.sh

# Terminal 2: Hit the API (use -i to see status codes and headers)
curl -i http://localhost:8080/health                        # 200, healthy
curl -i http://localhost:8080/api/users                     # 200, proxied to mock upstream
curl -i http://localhost:8080/api/unknown                   # 404
curl -i -X DELETE http://localhost:8080/api/users           # 405 (only GET/POST allowed)
```

Or run them separately — the provided `gateway.yaml` routes to upstream services on ports 3001–3006:

```bash
# Terminal 1: Start mock upstreams on ports 3001–3006
uv run python scripts/mock_upstream.py

# Terminal 2: Start the gateway
uv run gatewaykit gateway.yaml

# Terminal 3: Hit the API
curl -i http://localhost:8080/health
curl -i http://localhost:8080/api/users
```

You can also start mock upstreams on specific ports only:

```bash
uv run python scripts/mock_upstream.py 3001 3002
```

The mock upstream echoes back request details (method, path, headers, body) and supports special paths:
- `/healthz` — returns 200 OK
- `/slow` — returns 200 after a 10s delay
- `/flaky` — always returns 503

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
