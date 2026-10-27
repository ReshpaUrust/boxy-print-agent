from __future__ import annotations

import logging

import pytest

from boxy_print_agent.app import AgentApplication
from boxy_print_agent.config import AgentConfig, ConfigStore
from boxy_print_agent.idempotency import PrintLedger
from boxy_print_agent.mock_api import MockBoxyClient
from boxy_print_agent.printer import DryRunPrinterBackend
from boxy_print_agent.printer import PrinterError
from boxy_print_agent.secrets import MemoryDeviceTokenStore


def test_pairing_persists_device_id_but_not_token(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = AgentConfig(printer_name="Test printer")
    tokens = MemoryDeviceTokenStore()
    client = MockBoxyClient()
    application = AgentApplication(
        config=config,
        config_store=ConfigStore(config_path),
        token_store=tokens,
        client=client,
        ledger=PrintLedger(tmp_path / "ledger.sqlite3"),
        printer=DryRunPrinterBackend(["Test printer"]),
        logger=logging.getLogger("boxy_print_agent.tests.application"),
    )
    device_id = application.pair("one-use-code")
    saved = config_path.read_text(encoding="utf-8")
    assert device_id == "dev-device-1"
    assert tokens.load() == "dev-scoped-token-not-for-production"
    assert "dev-device-1" in saved
    assert tokens.load() not in saved


def test_printer_configuration_error_has_dedicated_ui_state(
    tmp_path, agent_config, mock_job, test_logger
) -> None:
    job, payload = mock_job
    application = AgentApplication(
        config=agent_config,
        config_store=ConfigStore(tmp_path / "config.json"),
        token_store=MemoryDeviceTokenStore("token"),
        client=MockBoxyClient(jobs=[job], artifacts={job.job_id: payload}),
        ledger=PrintLedger(tmp_path / "ledger.sqlite3"),
        printer=DryRunPrinterBackend(["Different printer"]),
        logger=test_logger,
    )
    result = application.run_once()
    assert result.code == "DEVICE_CONFIGURATION_ERROR"
    assert application.status.connection == "PRINTER_UNAVAILABLE"


def test_same_saved_printer_resumes_when_usb_device_reappears() -> None:
    backend = DryRunPrinterBackend(["Another printer"])
    with pytest.raises(PrinterError) as caught:
        backend.validate_printer("Xprinter XP-365B")
    assert caught.value.code == "DEVICE_CONFIGURATION_ERROR"

    backend.printers.append("Xprinter XP-365B")
    backend.validate_printer("Xprinter XP-365B")
