"""Header transforms for GatewayKit. Adds/removes headers on requests and responses."""

import time

from gateway.config import RequestTransformConfig, ResponseTransformConfig


def _resolve_dynamic_value(value: str) -> str:
    """Replace dynamic placeholders with actual values."""
    if value == "$request_time":
        return str(int(time.time()))
    if value == "$response_time":
        return str(int(time.time()))
    return value


def apply_request_header_transform(
    headers: dict[str, str],
    transform: RequestTransformConfig,
) -> dict[str, str]:
    """Apply request header transforms: add and remove headers."""
    # Remove headers (case-insensitive)
    remove_lower = {h.lower() for h in transform.headers.remove}
    headers = {k: v for k, v in headers.items() if k.lower() not in remove_lower}

    # Add headers (with dynamic value resolution)
    for key, value in transform.headers.add.items():
        headers[key] = _resolve_dynamic_value(value)

    return headers


def apply_response_header_transform(
    headers: dict[str, str],
    transform: ResponseTransformConfig,
    route_path: str,
) -> dict[str, str]:
    """Apply response header transforms: add and remove headers."""
    # Remove headers (case-insensitive)
    remove_lower = {h.lower() for h in transform.headers.remove}
    headers = {k: v for k, v in headers.items() if k.lower() not in remove_lower}

    # Add headers (with dynamic value resolution)
    for key, value in transform.headers.add.items():
        resolved = value
        if resolved == "$response_time":
            resolved = str(int(time.time()))
        elif resolved == "$route_path":
            resolved = route_path
        else:
            resolved = _resolve_dynamic_value(resolved)
        headers[key] = resolved

    return headers
