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
# Pass a config file as a CLI argument
uv run gatewaykit gateway.yaml

# Or via python module
uv run python -m gateway gateway.yaml
```

## Running Tests

```bash
uv run pytest
```

## Project Structure

```
gateway/          # Main gateway package
tests/            # Test suite
docs/             # Planning and design docs
gateway.yaml      # Example gateway config
DECISIONS.md      # Architectural decisions and trade-offs
```

## Implemented Features

- [ ] Config parsing (YAML → typed structures)
- [ ] Health endpoint (`GET /health`)
- [ ] Route matching with prefix support
- [ ] Method filtering (405 on mismatch)
- [ ] Reverse proxy (forward to upstream)
- [ ] Strip prefix
- [ ] Rate limiting (fixed window)
- [ ] Rate limiting (sliding window)
- [ ] Retry with backoff
- [ ] API key auth
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
