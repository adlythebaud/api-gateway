"""Route matching for GatewayKit. Prefix-based, longest-first."""

from gateway.config import RouteConfig


class Router:
    """Matches request paths to configured routes using prefix matching.

    Routes are sorted longest-first so that more specific routes take priority.
    e.g., /api/users/admin matches before /api/users.
    """

    def __init__(self, routes: list[RouteConfig]):
        # Sort routes by path length descending for longest-prefix-first matching
        self.routes = sorted(routes, key=lambda r: len(r.path), reverse=True)

    def match(self, path: str) -> RouteConfig | None:
        """Return the first route whose path is a prefix of the request path, or None."""
        for route in self.routes:
            if path == route.path or path.startswith(route.path.rstrip("/") + "/"):
                return route
        return None
