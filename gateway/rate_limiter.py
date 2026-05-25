"""Rate limiting for GatewayKit. Supports fixed window and sliding window strategies."""

import threading
import time

from gateway.config import RateLimitConfig


class FixedWindowLimiter:
    """Fixed window rate limiter. Counter resets when the window expires."""

    def __init__(self, config: RateLimitConfig):
        self.max_requests = config.requests
        self.window = config.window
        self.per = config.per
        self._lock = threading.Lock()
        # bucket_key -> (count, window_start)
        self._buckets: dict[str, tuple[int, float]] = {}

    def allow(self, client_ip: str) -> bool:
        key = client_ip if self.per == "ip" else "__global__"
        now = time.time()

        with self._lock:
            count, window_start = self._buckets.get(key, (0, now))

            # Window expired — reset
            if now - window_start >= self.window:
                count = 0
                window_start = now

            if count >= self.max_requests:
                return False

            self._buckets[key] = (count + 1, window_start)
            return True


class SlidingWindowLimiter:
    """Sliding window rate limiter. Tracks individual request timestamps."""

    def __init__(self, config: RateLimitConfig):
        self.max_requests = config.requests
        self.window = config.window
        self.per = config.per
        self._lock = threading.Lock()
        # bucket_key -> list of timestamps
        self._buckets: dict[str, list[float]] = {}

    def allow(self, client_ip: str) -> bool:
        key = client_ip if self.per == "ip" else "__global__"
        now = time.time()
        cutoff = now - self.window

        with self._lock:
            timestamps = self._buckets.get(key, [])

            # Prune expired timestamps
            timestamps = [t for t in timestamps if t > cutoff]

            if len(timestamps) >= self.max_requests:
                self._buckets[key] = timestamps
                return False

            timestamps.append(now)
            self._buckets[key] = timestamps
            return True


def create_limiter(config: RateLimitConfig) -> FixedWindowLimiter | SlidingWindowLimiter:
    """Create the appropriate rate limiter based on the config strategy."""
    if config.strategy == "sliding_window":
        return SlidingWindowLimiter(config)
    return FixedWindowLimiter(config)


class RateLimiterRegistry:
    """Holds rate limiters for each route and the global fallback."""

    def __init__(self, global_config: RateLimitConfig | None, routes: list):
        self.global_limiter = create_limiter(global_config) if global_config else None
        # route path -> limiter
        self._route_limiters: dict[str, FixedWindowLimiter | SlidingWindowLimiter] = {}
        for route in routes:
            if route.rate_limit:
                self._route_limiters[route.path] = create_limiter(route.rate_limit)

    def check(self, route_path: str, client_ip: str) -> bool:
        """Check if the request is allowed. Route-level limits override global."""
        limiter = self._route_limiters.get(route_path, self.global_limiter)
        if limiter is None:
            return True
        return limiter.allow(client_ip)
