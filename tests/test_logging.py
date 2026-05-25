"""Tests for request logging."""

import logging
import time
import urllib.request
import urllib.error

import pytest

from gateway.config import Config, GatewayConfig, RouteConfig, UpstreamConfig
from tests.helpers import make_gateway
from tests.mock_upstream import start_mock_upstream


@pytest.fixture
def upstream():
    server, base_url = start_mock_upstream()
    yield base_url
    server.shutdown()


class TestRequestLogging:
    """Verify that requests produce log records at the correct level."""

    @pytest.fixture(autouse=True)
    def setup(self, upstream, caplog):
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[
                RouteConfig(path="/api/test", methods=["GET"], upstream=UpstreamConfig(url=upstream)),
            ],
        )
        self.server, self.base_url = make_gateway(config)
        self.logger = logging.getLogger("gatewaykit")
        self._original_level = self.logger.level
        self.logger.setLevel(logging.DEBUG)
        # Enable caplog to capture at DEBUG level for the gatewaykit logger
        caplog.set_level(logging.DEBUG, logger="gatewaykit")
        yield
        self.logger.setLevel(self._original_level)
        self.server.shutdown()

    def _wait_for_records(self, caplog, min_count=1, timeout=2.0):
        """Wait briefly for log records to arrive from the server thread."""
        deadline = time.time() + timeout
        while len(caplog.records) < min_count and time.time() < deadline:
            time.sleep(0.05)

    def test_successful_request_logs_info(self, caplog):
        urllib.request.urlopen(f"{self.base_url}/api/test")
        self._wait_for_records(caplog)
        info_records = [r for r in caplog.records if r.levelno == logging.INFO and "GET" in r.message]
        assert len(info_records) >= 1
        assert "/api/test" in info_records[0].message
        assert "200" in info_records[0].message
        assert "ms" in info_records[0].message

    def test_404_logs_warning(self, caplog):
        req = urllib.request.Request(f"{self.base_url}/nonexistent")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(req)
        self._wait_for_records(caplog)
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warn_records) >= 1
        assert "404" in warn_records[0].message

    def test_405_logs_warning(self, caplog):
        req = urllib.request.Request(f"{self.base_url}/api/test", method="DELETE")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(req)
        self._wait_for_records(caplog)
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warn_records) >= 1
        assert "405" in warn_records[0].message

    def test_502_logs_error(self, caplog):
        config = Config(
            gateway=GatewayConfig(port=0),
            routes=[
                RouteConfig(path="/api/down", methods=["GET"], upstream=UpstreamConfig(url="http://127.0.0.1:1")),
            ],
        )
        server, base_url = make_gateway(config)
        try:
            req = urllib.request.Request(f"{base_url}/api/down")
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(req)
            self._wait_for_records(caplog)
            error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
            assert len(error_records) >= 1
            assert "502" in error_records[0].message
        finally:
            server.shutdown()

    def test_health_logs_info(self, caplog):
        urllib.request.urlopen(f"{self.base_url}/health")
        self._wait_for_records(caplog)
        info_records = [r for r in caplog.records if r.levelno == logging.INFO and "/health" in r.message]
        assert len(info_records) >= 1
        assert "200" in info_records[0].message

    def test_log_includes_client_ip(self, caplog):
        urllib.request.urlopen(f"{self.base_url}/api/test")
        self._wait_for_records(caplog)
        info_records = [r for r in caplog.records if r.levelno == logging.INFO and "GET" in r.message]
        assert "127.0.0.1" in info_records[0].message

    def test_log_includes_route_path(self, caplog):
        urllib.request.urlopen(f"{self.base_url}/api/test/sub")
        self._wait_for_records(caplog)
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert "/api/test" in info_records[0].message
