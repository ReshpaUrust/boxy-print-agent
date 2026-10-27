from __future__ import annotations

from dataclasses import replace

from boxy_print_agent.idempotency import PrintLedger
from boxy_print_agent.api_client import ApiClientError
from boxy_print_agent.mock_api import MockBoxyClient, create_mock_job
from boxy_print_agent.models import JobState
from boxy_print_agent.polling import BackoffPolicy, PollingService
from boxy_print_agent.printer import DryRunPrinterBackend, PrinterError


def _service(*, tmp_path, config, logger, job, payload, printer=None, ledger=None):
    client = MockBoxyClient(jobs=[job], artifacts={job.job_id: payload})
    printer = printer or DryRunPrinterBackend([config.printer_name])
    ledger = ledger or PrintLedger(tmp_path / "ledger.sqlite3")
    service = PollingService(
        config=config,
        client=client,
        ledger=ledger,
        printer=printer,
        logger=logger,
    )
    return service, client, printer, ledger


def test_successful_dry_run_uses_full_lifecycle(tmp_path, agent_config, mock_job, test_logger) -> None:
    job, payload = mock_job
    service, client, printer, ledger = _service(
        tmp_path=tmp_path,
        config=agent_config,
        logger=test_logger,
        job=job,
        payload=payload,
    )
    result = service.run_once()
    assert result.outcome == "DISPATCHED_TO_SPOOLER"
    assert len(printer.calls) == 1
    assert [update["status"] for update in client.status_updates] == [
        JobState.PRINT_INTENT,
        JobState.DISPATCHED_TO_SPOOLER,
        JobState.ACKNOWLEDGED,
    ]
    assert ledger.get(job.job_id, job.print_attempt_token).state == JobState.ACKNOWLEDGED
    assert client.heartbeats[0].device_id == agent_config.device_id


def test_crash_restart_after_intent_never_prints_same_attempt(
    tmp_path, agent_config, mock_job, test_logger
) -> None:
    job, payload = mock_job
    ledger = PrintLedger(tmp_path / "ledger.sqlite3")
    ledger.record_intent(
        job_id=job.job_id,
        attempt_token=job.print_attempt_token,
        artifact_sha256=job.artifact.sha256,
        printer_name=agent_config.printer_name,
    )
    service, client, printer, _ = _service(
        tmp_path=tmp_path,
        config=agent_config,
        logger=test_logger,
        job=job,
        payload=payload,
        ledger=PrintLedger(tmp_path / "ledger.sqlite3"),
    )
    result = service.run_once()
    assert result.outcome == "DUPLICATE_BLOCKED"
    assert printer.calls == []
    assert client.status_updates[-1]["status"] == JobState.REVIEW_REQUIRED


def test_same_job_with_new_attempt_token_is_explicit_reprint(
    tmp_path, agent_config, mock_job, test_logger
) -> None:
    job, payload = mock_job
    ledger = PrintLedger(tmp_path / "ledger.sqlite3")
    printer = DryRunPrinterBackend([agent_config.printer_name])
    first, _, _, _ = _service(
        tmp_path=tmp_path,
        config=agent_config,
        logger=test_logger,
        job=job,
        payload=payload,
        printer=printer,
        ledger=ledger,
    )
    assert first.run_once().outcome == "DISPATCHED_TO_SPOOLER"

    reprint = replace(job, print_attempt_token="attempt-2")
    second, _, _, _ = _service(
        tmp_path=tmp_path,
        config=agent_config,
        logger=test_logger,
        job=reprint,
        payload=payload,
        printer=printer,
        ledger=ledger,
    )
    assert second.run_once().outcome == "DISPATCHED_TO_SPOOLER"
    assert len(printer.calls) == 2


class FailingPrinter(DryRunPrinterBackend):
    def print_png_batch(self, pages, *, printer_name, document_name):
        self.validate_printer(printer_name)
        raise PrinterError("SPOOLER_ERROR", "simulated spooler failure")


def test_failure_after_intent_requires_review(tmp_path, agent_config, mock_job, test_logger) -> None:
    job, payload = mock_job
    service, client, _, ledger = _service(
        tmp_path=tmp_path,
        config=agent_config,
        logger=test_logger,
        job=job,
        payload=payload,
        printer=FailingPrinter([agent_config.printer_name]),
    )
    result = service.run_once()
    assert result.outcome == "REVIEW_REQUIRED"
    assert result.code == "SPOOLER_ERROR"
    assert ledger.get(job.job_id, job.print_attempt_token).state == JobState.REVIEW_REQUIRED
    assert client.status_updates[-1]["status"] == JobState.REVIEW_REQUIRED


def test_missing_selected_printer_fails_before_intent(
    tmp_path, agent_config, mock_job, test_logger
) -> None:
    job, payload = mock_job
    service, client, printer, ledger = _service(
        tmp_path=tmp_path,
        config=agent_config,
        logger=test_logger,
        job=job,
        payload=payload,
        printer=DryRunPrinterBackend(["Other printer"]),
    )
    result = service.run_once()
    assert result.outcome == "ERROR"
    assert result.code == "DEVICE_CONFIGURATION_ERROR"
    assert ledger.get(job.job_id, job.print_attempt_token) is None
    assert printer.calls == []
    assert client.status_updates[-1]["status"] == JobState.ERROR


def test_checksum_failure_never_reaches_printer(tmp_path, agent_config, mock_job, test_logger) -> None:
    job, payload = mock_job
    damaged_job = replace(job, artifact=replace(job.artifact, sha256="0" * 64))
    service, _, printer, ledger = _service(
        tmp_path=tmp_path,
        config=agent_config,
        logger=test_logger,
        job=damaged_job,
        payload=payload,
    )
    result = service.run_once()
    assert result.code == "ARTIFACT_CHECKSUM_MISMATCH"
    assert printer.calls == []
    assert ledger.get(job.job_id, job.print_attempt_token) is None


def test_backoff_is_exponential_and_bounded() -> None:
    policy = BackoffPolicy(base_seconds=2, maximum_seconds=10, jitter_ratio=0)
    assert [policy.delay_for(n, random_value=0.5) for n in range(1, 7)] == [2, 4, 8, 10, 10, 10]


def test_heartbeat_failure_does_not_block_claim(tmp_path, agent_config, mock_job, test_logger) -> None:
    job, payload = mock_job

    class HeartbeatFailingClient(MockBoxyClient):
        def heartbeat(self, heartbeat):
            raise ApiClientError("SERVER_TIMEOUT", "heartbeat timed out", retryable=True)

    client = HeartbeatFailingClient(jobs=[job], artifacts={job.job_id: payload})
    printer = DryRunPrinterBackend([agent_config.printer_name])
    service = PollingService(
        config=agent_config,
        client=client,
        ledger=PrintLedger(tmp_path / "ledger.sqlite3"),
        printer=printer,
        logger=test_logger,
    )
    assert service.run_once().outcome == "DISPATCHED_TO_SPOOLER"
    assert len(printer.calls) == 1


def test_dry_run_never_calls_windows_backend(
    tmp_path, agent_config, mock_job, test_logger, monkeypatch
) -> None:
    from boxy_print_agent import printer as printer_module

    def forbidden(*args, **kwargs):
        raise AssertionError("Windows spooler was called")

    monkeypatch.setattr(printer_module.WindowsPrinterBackend, "print_png", forbidden)
    job, payload = mock_job
    service, _, printer, _ = _service(
        tmp_path=tmp_path,
        config=agent_config,
        logger=test_logger,
        job=job,
        payload=payload,
        printer=DryRunPrinterBackend([agent_config.printer_name]),
    )
    assert service.run_once().outcome == "DISPATCHED_TO_SPOOLER"
    assert len(printer.calls) == 1


def test_one_server_batch_becomes_one_spooler_document(
    tmp_path, agent_config, test_logger
) -> None:
    first, first_payload = create_mock_job(
        job_id="job-1",
        device_id=agent_config.device_id,
        attempt_token="attempt-1",
        batch_id="batch-1",
    )
    second, second_payload = create_mock_job(
        job_id="job-2",
        device_id=agent_config.device_id,
        attempt_token="attempt-2",
        batch_id="batch-1",
    )
    client = MockBoxyClient(
        jobs=[first, second],
        artifacts={first.job_id: first_payload, second.job_id: second_payload},
    )
    printer = DryRunPrinterBackend([agent_config.printer_name])
    ledger = PrintLedger(tmp_path / "ledger.sqlite3")
    service = PollingService(
        config=agent_config,
        client=client,
        ledger=ledger,
        printer=printer,
        logger=test_logger,
    )

    result = service.run_once()

    assert result.outcome == "DISPATCHED_TO_SPOOLER"
    assert len(printer.calls) == 1
    assert printer.calls[0]["page_count"] == 2
    assert printer.calls[0]["artifact_ids"] == [
        first.artifact.artifact_id,
        second.artifact.artifact_id,
    ]
    assert [update["status"] for update in client.status_updates] == [
        JobState.PRINT_INTENT,
        JobState.PRINT_INTENT,
        JobState.DISPATCHED_TO_SPOOLER,
        JobState.DISPATCHED_TO_SPOOLER,
        JobState.ACKNOWLEDGED,
        JobState.ACKNOWLEDGED,
    ]
    assert len({update["spooler_job_id"] for update in client.status_updates[2:]}) == 1
    assert ledger.get(first.job_id, first.print_attempt_token).state == JobState.ACKNOWLEDGED
    assert ledger.get(second.job_id, second.print_attempt_token).state == JobState.ACKNOWLEDGED
