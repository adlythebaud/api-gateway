"""Reverse proxy logic for GatewayKit. Forwards requests to upstream services."""

import http.client
import urllib.parse
from dataclasses import dataclass, field


@dataclass
class ProxyRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes = b""


@dataclass
class ProxyResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


# Headers that should not be forwarded between client and upstream
HOP_BY_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
})


def forward_request(
    upstream_url: str,
    request: ProxyRequest,
    timeout: float,
) -> ProxyResponse:
    """Forward a request to the upstream URL and return the response.

    Raises:
        TimeoutError: If the upstream does not respond within `timeout` seconds.
        ConnectionError: If the upstream is unreachable.
    """
    parsed = urllib.parse.urlparse(upstream_url)
    upstream_path = request.path or "/"

    # Build the full URL path with any query string preserved
    if "?" in upstream_path:
        path_part, query_part = upstream_path.split("?", 1)
        full_path = f"{path_part}?{query_part}"
    else:
        full_path = upstream_path

    try:
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)

        # Filter hop-by-hop headers and set Host
        forward_headers = {}
        for key, value in request.headers.items():
            if key.lower() not in HOP_BY_HOP_HEADERS:
                forward_headers[key] = value
        forward_headers["Host"] = parsed.hostname
        if parsed.port:
            forward_headers["Host"] = f"{parsed.hostname}:{parsed.port}"

        conn.request(request.method, full_path, body=request.body, headers=forward_headers)
        resp = conn.getresponse()

        # Read response headers, filtering hop-by-hop
        resp_headers = {}
        for key, value in resp.getheaders():
            if key.lower() not in HOP_BY_HOP_HEADERS:
                resp_headers[key] = value

        body = resp.read()
        conn.close()

        return ProxyResponse(status=resp.status, headers=resp_headers, body=body)

    except TimeoutError:
        raise
    except OSError as e:
        raise ConnectionError(f"Failed to connect to upstream {upstream_url}: {e}") from e
