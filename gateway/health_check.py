"""Background health checks for upstream targets. Marks unhealthy targets in the load balancer."""

import http.client
import logging
import threading
import time
import urllib.parse

from gateway.config import HealthCheckConfig, RouteConfig
from gateway.load_balancer import LoadBalancerRegistry

logger = logging.getLogger("gatewaykit")


class HealthChecker:
    """Periodically pings an upstream target's health endpoint."""

    def __init__(
        self,
        target_url: str,
        config: HealthCheckConfig,
        on_healthy: callable,
        on_unhealthy: callable,
    ):
        self.target_url = target_url
        self.config = config
        self._on_healthy = on_healthy
        self._on_unhealthy = on_unhealthy
        self._consecutive_failures = 0
        self._healthy = True
        self._stop = threading.Event()

    def start(self):
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            self._check()
            self._stop.wait(timeout=self.config.interval)

    def _check(self):
        parsed = urllib.parse.urlparse(self.target_url)
        try:
            conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
            conn.request("GET", self.config.path)
            resp = conn.getresponse()
            resp.read()
            conn.close()

            if 200 <= resp.status < 400:
                self._consecutive_failures = 0
                if not self._healthy:
                    self._healthy = True
                    self._on_healthy(self.target_url)
                    logger.info(f"Health check: {self.target_url} is healthy again")
            else:
                self._record_failure()
        except Exception:
            self._record_failure()

    def _record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.config.unhealthy_threshold and self._healthy:
            self._healthy = False
            self._on_unhealthy(self.target_url)
            logger.warning(
                f"Health check: {self.target_url} marked unhealthy "
                f"after {self._consecutive_failures} consecutive failures"
            )


def start_health_checks(
    routes: list[RouteConfig],
    load_balancers: LoadBalancerRegistry,
) -> list[HealthChecker]:
    """Start background health checkers for all routes with health_check config."""
    checkers = []
    for route in routes:
        if not route.health_check:
            continue
        lb = load_balancers.get(route.path)
        if not lb:
            continue

        for target in route.upstream.targets:
            checker = HealthChecker(
                target_url=target.url,
                config=route.health_check,
                on_healthy=lb.mark_healthy,
                on_unhealthy=lb.mark_unhealthy,
            )
            checker.start()
            checkers.append(checker)

    return checkers
