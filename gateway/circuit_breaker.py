"""Circuit breaker for GatewayKit. Protects against repeatedly hitting a failing upstream."""

import threading
import time
from enum import Enum

from gateway.config import CircuitBreakerConfig


class State(Enum):
    CLOSED = "closed"        # Normal operation — requests flow through
    OPEN = "open"            # Tripped — requests rejected immediately
    HALF_OPEN = "half_open"  # Probing — one request allowed through to test upstream


class CircuitBreaker:
    """Per-route circuit breaker with closed/open/half-open states."""

    def __init__(self, config: CircuitBreakerConfig):
        self.threshold = config.threshold
        self.window = config.window
        self.cooldown = config.cooldown
        self._lock = threading.Lock()
        self._state = State.CLOSED
        self._failures: list[float] = []  # timestamps of failures within the window
        self._tripped_at: float = 0.0

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def allow(self) -> tuple[bool, dict | None]:
        """Check if a request should be allowed through.

        Returns (allowed, error_body). If not allowed, error_body contains
        the 503 response to send.
        """
        now = time.time()
        with self._lock:
            if self._state == State.CLOSED:
                return True, None

            if self._state == State.OPEN:
                elapsed = now - self._tripped_at
                if elapsed >= self.cooldown:
                    # Cooldown expired — transition to half-open, allow one probe
                    self._state = State.HALF_OPEN
                    return True, None
                else:
                    retry_after = int(self.cooldown - elapsed) + 1
                    return False, {
                        "error": "service_unavailable",
                        "retry_after": retry_after,
                    }

            if self._state == State.HALF_OPEN:
                # Only one probe at a time — reject others while probing
                return False, {
                    "error": "service_unavailable",
                    "retry_after": 1,
                }

        return True, None

    def record_success(self):
        """Record a successful upstream response. Resets the breaker if half-open."""
        with self._lock:
            if self._state == State.HALF_OPEN:
                self._state = State.CLOSED
                self._failures.clear()

    def record_failure(self):
        """Record a failed upstream response. May trip the breaker."""
        now = time.time()
        with self._lock:
            if self._state == State.HALF_OPEN:
                # Probe failed — trip again
                self._state = State.OPEN
                self._tripped_at = now
                return

            # Prune old failures outside the window
            cutoff = now - self.window
            self._failures = [t for t in self._failures if t > cutoff]
            self._failures.append(now)

            if len(self._failures) >= self.threshold:
                self._state = State.OPEN
                self._tripped_at = now


class CircuitBreakerRegistry:
    """Holds circuit breakers for each route that has one configured."""

    def __init__(self, routes: list):
        self._breakers: dict[str, CircuitBreaker] = {}
        for route in routes:
            if route.circuit_breaker:
                self._breakers[route.path] = CircuitBreaker(route.circuit_breaker)

    def get(self, route_path: str) -> CircuitBreaker | None:
        return self._breakers.get(route_path)
