"""Polling orchestration. Server state is authoritative; local ledger prevents duplicates."""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import __version__
from .api_client import ApiClientError, BoxyClient
from .artifact import ArtifactValidationError, validate_artifact
from .config import AgentConfig
from .device_identity import capabilities, hostname
from .idempotency import DuplicatePrintAttemptError, PrintLedger
from .logging_setup import redact_text
from .models import DeviceHeartbeat, JobState, PollResult, PrintJob
from .printer import PrinterBackend, PrinterError


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    base_seconds: float = 2.0
    maximum_seconds: float = 30.0
    jitter_ratio: float = 0.2

    def delay_for(self, failure_count: int, *, random_value: float | None = None) -> float:
        exponent = max(0, failure_count - 1)
        bounded = min(self.maximum_seconds, self.base_seconds * (2**exponent))
        random_value = random.random() if random_value is None else random_value
        jitter = bounded * self.jitter_ratio * ((random_value * 2) - 1)
        return max(0.0, min(self.maximum_seconds, bounded + jitter))


class PollingService:
    def __init__(
        self,
        *,
        config: AgentConfig,
        client: BoxyClient,
        ledger: PrintLedger,
        printer: PrinterBackend,
        logger: logging.Logger,
        backoff: BackoffPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_result: Callable[[PollResult], None] | None = None,
    ):
        self.config = config
        self.client = client
        self.ledger = ledger
        self.printer = printer
        self.logger = logger
        self.backoff = backoff or BackoffPolicy()
        self.clock = clock
        self.on_result = on_result
        self._last_heartbeat = float("-inf")

    def heartbeat(self, *, status: str = "ONLINE") -> None:
        if not self.config.device_id:
            raise ApiClientError("NOT_PAIRED", "Device has not been paired")
        self.client.heartbeat(
            DeviceHeartbeat(
                device_id=self.config.device_id,
                display_name=self.config.display_name,
                hostname=hostname(),
                selected_printer=self.config.printer_name,
                agent_version=__version__,
                status=status,
                capabilities=capabilities(dry_run=self.config.dry_run),
            )
        )
        self._last_heartbeat = self.clock()

    def run_once(self) -> PollResult:
        if not self.config.device_id:
            return PollResult("NOT_PAIRED", detail="Pair the device before polling")
        if self.clock() - self._last_heartbeat >= self.config.heartbeat_interval_seconds:
            try:
                self.heartbeat()
            except ApiClientError as exc:
                self.logger.warning("heartbeat_failed code=%s", exc.code)

        job = self.client.claim_next(self.config.device_id)
        if job is None:
            return PollResult("NO_JOB")
        if job.device_id != self.config.device_id:
            self._safe_status(job, JobState.ERROR, code="DEVICE_MISMATCH", message="Job belongs to another device")
            return PollResult("ERROR", job.job_id, "Job belongs to another device")
        return self._process_claimed_job(job)

    def _process_claimed_job(self, job: PrintJob) -> PollResult:
        existing = self.ledger.get(job.job_id, job.print_attempt_token)
        if existing is not None:
            detail = f"Existing local attempt state: {existing.state.value}"
            if existing.state != JobState.ACKNOWLEDGED:
                self.ledger.mark_review_required(job.job_id, job.print_attempt_token, detail)
                self._safe_status(job, JobState.REVIEW_REQUIRED, code="AMBIGUOUS_PRIOR_ATTEMPT", message=detail)
            return PollResult("DUPLICATE_BLOCKED", job.job_id, detail)

        try:
            payload = self.client.download_artifact(job, max_bytes=self.config.max_artifact_bytes)
            validate_artifact(payload, job.artifact, max_bytes=self.config.max_artifact_bytes)
            if job.copies != 1:
                raise ArtifactValidationError("UNSUPPORTED_COPIES", "MVP accepts exactly one copy")
            self.printer.validate_printer(self.config.printer_name)
        except (ApiClientError, ArtifactValidationError, PrinterError) as exc:
            code = getattr(exc, "code", "PRE_PRINT_ERROR")
            message = redact_text(exc)
            self._safe_status(job, JobState.ERROR, code=code, message=message)
            self.logger.error("job_preflight_failed job_id=%s code=%s", job.job_id, code)
            return PollResult("ERROR", job.job_id, message, code)

        try:
            self.ledger.record_intent(
                job_id=job.job_id,
                attempt_token=job.print_attempt_token,
                artifact_sha256=job.artifact.sha256,
                printer_name=self.config.printer_name,
            )
        except DuplicatePrintAttemptError as exc:
            return PollResult("DUPLICATE_BLOCKED", job.job_id, str(exc))

        try:
            self.client.update_job_status(job, JobState.PRINT_INTENT)
            result = self.printer.print_png(
                payload,
                job.artifact,
                printer_name=self.config.printer_name,
                copies=job.copies,
            )
            self.ledger.mark_dispatched(job.job_id, job.print_attempt_token, result.spooler_job_id)
            self.client.update_job_status(
                job,
                JobState.DISPATCHED_TO_SPOOLER,
                spooler_job_id=result.spooler_job_id,
            )
            self.client.update_job_status(
                job,
                JobState.ACKNOWLEDGED,
                spooler_job_id=result.spooler_job_id,
            )
            self.ledger.mark_acknowledged(job.job_id, job.print_attempt_token)
            self.logger.info(
                "job_dispatched job_id=%s printer=%s spooler_job_id=%s",
                job.job_id,
                self.config.printer_name,
                result.spooler_job_id or "unknown",
            )
            return PollResult("DISPATCHED_TO_SPOOLER", job.job_id, result.detail)
        except Exception as exc:
            message = redact_text(exc)
            code = getattr(exc, "code", "AMBIGUOUS_PRINT_FAILURE")
            self.ledger.mark_review_required(job.job_id, job.print_attempt_token, message)
            self._safe_status(job, JobState.REVIEW_REQUIRED, code=code, message=message)
            self.logger.error("job_requires_review job_id=%s code=%s", job.job_id, code)
            return PollResult("REVIEW_REQUIRED", job.job_id, message, code)

    def _safe_status(
        self,
        job: PrintJob,
        status: JobState,
        *,
        code: str,
        message: str,
    ) -> None:
        try:
            self.client.update_job_status(job, status, code=code, message=redact_text(message))
        except ApiClientError as exc:
            self.logger.warning(
                "status_callback_failed job_id=%s target=%s code=%s",
                job.job_id,
                status.value,
                exc.code,
            )

    def run_forever(self, stop_event: threading.Event) -> None:
        failures = 0
        self.logger.info("polling_started device_id=%s", self.config.device_id or "not-paired")
        while not stop_event.is_set():
            try:
                result = self.run_once()
                if self.on_result:
                    self.on_result(result)
                failures = 0
                delay = self.config.polling_interval_seconds
            except ApiClientError as exc:
                failures += 1
                delay = self.backoff.delay_for(failures)
                self.logger.warning("polling_error code=%s retry_in=%.2f", exc.code, delay)
            except Exception as exc:  # defensive process boundary
                failures += 1
                delay = self.backoff.delay_for(failures)
                self.logger.exception("unexpected_polling_error class=%s retry_in=%.2f", type(exc).__name__, delay)
            stop_event.wait(delay)
        self.logger.info("polling_stopped")
