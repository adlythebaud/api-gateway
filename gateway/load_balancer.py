"""Load balancing for GatewayKit. Round robin and weighted round robin."""

import threading

from gateway.config import RouteConfig, UpstreamConfig


class LoadBalancer:
    """Selects upstream targets using round robin or weighted round robin."""

    def __init__(self, upstream: UpstreamConfig):
        self._lock = threading.Lock()
        self.targets = upstream.targets
        self.balance = upstream.balance

        if self.balance == "weighted_round_robin":
            # Build a weighted sequence: [A, A, A, B] for weights [3, 1]
            self._sequence = []
            for target in self.targets:
                self._sequence.extend([target.url] * target.weight)
        else:
            # Plain round robin
            self._sequence = [t.url for t in self.targets]

        self._index = 0
        # Track which targets are healthy (all start healthy)
        self._healthy: set[str] = {t.url for t in self.targets}

    def next(self) -> str | None:
        """Return the next upstream URL, skipping unhealthy targets.

        Returns None if all targets are unhealthy.
        """
        with self._lock:
            healthy_urls = [url for url in self._sequence if url in self._healthy]
            if not healthy_urls:
                return None

            url = healthy_urls[self._index % len(healthy_urls)]
            self._index += 1
            return url

    def mark_unhealthy(self, url: str):
        with self._lock:
            self._healthy.discard(url)

    def mark_healthy(self, url: str):
        with self._lock:
            self._healthy.add(url)

    def get_healthy_targets(self) -> set[str]:
        with self._lock:
            return set(self._healthy)


class LoadBalancerRegistry:
    """Holds load balancers for routes that have multiple upstream targets."""

    def __init__(self, routes: list[RouteConfig]):
        self._balancers: dict[str, LoadBalancer] = {}
        for route in routes:
            if route.upstream.targets:
                self._balancers[route.path] = LoadBalancer(route.upstream)

    def get(self, route_path: str) -> LoadBalancer | None:
        return self._balancers.get(route_path)
