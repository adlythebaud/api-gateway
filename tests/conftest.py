"""Global test configuration — suppress gateway logging during tests."""

import logging


def pytest_configure(config):
    logging.getLogger("gatewaykit").setLevel(logging.CRITICAL)
