from __future__ import annotations

import logging

import pytest

from boxy_print_agent.config import AgentConfig
from boxy_print_agent.mock_api import create_mock_job


@pytest.fixture
def agent_config() -> AgentConfig:
    return AgentConfig(
        device_id="device-1",
        display_name="Test device",
        printer_name="Test printer",
        polling_interval_seconds=2,
        heartbeat_interval_seconds=20,
        dev_mode=True,
        dry_run=True,
    )


@pytest.fixture
def mock_job():
    return create_mock_job(device_id="device-1")


@pytest.fixture
def test_logger() -> logging.Logger:
    logger = logging.getLogger("boxy_print_agent.tests")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger
