"""Application composition; GUI and CLI remain thin wrappers over this core."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from .api_client import BoxyClient
from .config import AgentConfig, ConfigStore
from .device_identity import capabilities, hostname
from .idempotency import PrintLedger
from .models import PollResult
from .polling import PollingService
from .printer import PrinterBackend
from .secrets import DeviceTokenStore


@dataclass(slots=True)
class ApplicationStatus:
    connection: str = "NOT_PAIRED"
    last_job_id: str = ""
    last_job_status: str = ""
    detail: str = ""


class AgentApplication:
    def __init__(
        self,
        *,
        config: AgentConfig,
        config_store: ConfigStore,
        token_store: DeviceTokenStore,
        client: BoxyClient,
        ledger: PrintLedger,
        printer: PrinterBackend,
        logger: logging.Logger,
    ):
        self.config = config
        self.config_store = config_store
        self.token_store = token_store
        self.client = client
        self.ledger = ledger
        self.printer = printer
        self.logger = logger
        self.status = ApplicationStatus(connection="OFFLINE" if config.device_id else "NOT_PAIRED")
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.polling = PollingService(
            config=config,
            client=client,
            ledger=ledger,
            printer=printer,
            logger=logger,
            on_result=self._record_result,
        )

    def pair(self, pairing_code: str) -> str:
        result = self.client.pair(
            pairing_code,
            display_name=self.config.display_name,
            hostname=hostname(),
            capabilities=capabilities(dry_run=self.config.dry_run),
        )
        self.token_store.save(result.device_token)
        self.client.set_device_token(result.device_token)
        self.config.device_id = result.device_id
        self.config.display_name = result.display_name
        self.config_store.save(self.config)
        self.status.connection = "ONLINE"
        self.logger.info("device_paired device_id=%s", result.device_id)
        return result.device_id

    def change_printer(self, printer_name: str) -> None:
        self.printer.validate_printer(printer_name)
        self.config.printer_name = printer_name
        self.config_store.save(self.config)
        self.logger.info("printer_selected printer=%s", printer_name)

    def check_connection(self) -> bool:
        try:
            self.polling.heartbeat()
        except Exception as exc:
            self.status.connection = "SERVER_UNAVAILABLE"
            self.status.detail = type(exc).__name__
            return False
        self.status.connection = "ONLINE"
        self.status.detail = ""
        return True

    def run_once(self) -> PollResult:
        result = self.polling.run_once()
        self._record_result(result)
        return result

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.stop_event.clear()
        self._thread = threading.Thread(
            target=self.polling.run_forever,
            args=(self.stop_event,),
            name="boxy-polling",
            daemon=True,
        )
        self._thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        self.stop_event.set()
        if self._thread:
            self._thread.join(timeout)
        close = getattr(self.client, "close", None)
        if close:
            close()
        self.logger.info("agent_shutdown")

    def _record_result(self, result: PollResult) -> None:
        if result.job_id:
            self.status.last_job_id = result.job_id
        self.status.last_job_status = result.outcome
        self.status.detail = result.detail
        if result.outcome == "REVIEW_REQUIRED":
            self.status.connection = "JOB_REQUIRES_REVIEW"
        elif result.code == "DEVICE_CONFIGURATION_ERROR":
            self.status.connection = "PRINTER_UNAVAILABLE"
        elif result.outcome not in {"NOT_PAIRED", "ERROR"}:
            self.status.connection = "ONLINE"


def runtime_paths(config_path: Path) -> dict[str, Path]:
    data_dir = config_path.parent
    return {
        "data_dir": data_dir,
        "token": data_dir / "device-token.dpapi",
        "ledger": data_dir / "print-ledger.sqlite3",
        "logs": data_dir / "logs",
    }
