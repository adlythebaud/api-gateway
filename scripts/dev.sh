#!/bin/bash
# Start mock upstreams and the gateway together for local development.
# Usage: ./scripts/dev.sh [config_file]

CONFIG="${1:-gateway.yaml}"

cleanup() {
    echo "Shutting down..."
    kill $UPSTREAM_PID $GATEWAY_PID 2>/dev/null
    wait $UPSTREAM_PID $GATEWAY_PID 2>/dev/null
}
trap cleanup EXIT

echo "Starting mock upstreams..."
uv run python scripts/mock_upstream.py &
UPSTREAM_PID=$!
sleep 1

echo "Starting gateway with $CONFIG..."
uv run gatewaykit "$CONFIG" &
GATEWAY_PID=$!

echo ""
echo "Ready! Try: curl http://localhost:8080/health"
echo "Press Ctrl+C to stop."
wait
