"""Retry logic for GatewayKit. Retries upstream requests on configured status codes."""

import logging
import time

from gateway.config import RetryConfig
from gateway.proxy import ProxyRequest, ProxyResponse, forward_request

logger = logging.getLogger("gatewaykit")


def forward_with_retry(
    upstream_url: str,
    request: ProxyRequest,
    timeout: float,
    retry_config: RetryConfig,
) -> ProxyResponse:
    """Forward a request with retry logic.

    Retries on status codes listed in retry_config.on, up to retry_config.attempts total tries.
    Raises TimeoutError or ConnectionError if all attempts fail with those exceptions.
    """
    last_response = None
    total_attempts = retry_config.attempts

    for attempt in range(total_attempts):
        try:
            response = forward_request(upstream_url, request, timeout)
        except (TimeoutError, ConnectionError):
            # On connection/timeout errors, retry if we have attempts left
            if attempt < total_attempts - 1:
                delay = _compute_delay(retry_config, attempt)
                logger.info(f"Retry {attempt + 1}/{total_attempts} for {request.method} {request.path} (connection error), waiting {delay:.2f}s")
                time.sleep(delay)
                continue
            raise

        if response.status not in retry_config.on or attempt >= total_attempts - 1:
            return response

        last_response = response
        delay = _compute_delay(retry_config, attempt)
        logger.info(f"Retry {attempt + 1}/{total_attempts} for {request.method} {request.path} (status {response.status}), waiting {delay:.2f}s")
        time.sleep(delay)

    return last_response


def _compute_delay(config: RetryConfig, attempt: int) -> float:
    """Compute the delay before the next retry attempt."""
    if config.backoff == "exponential":
        return config.initial_delay * (2 ** attempt)
    return config.initial_delay
