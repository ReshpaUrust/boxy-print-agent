from __future__ import annotations

import pytest

from boxy_print_agent.idempotency import DuplicatePrintAttemptError, PrintLedger
from boxy_print_agent.models import JobState


def _record(ledger: PrintLedger, token: str = "attempt-1") -> None:
    ledger.record_intent(
        job_id="job-1",
        attempt_token=token,
        artifact_sha256="a" * 64,
        printer_name="Test printer",
    )


def test_duplicate_attempt_token_is_blocked(tmp_path) -> None:
    ledger = PrintLedger(tmp_path / "ledger.sqlite3")
    _record(ledger)
    with pytest.raises(DuplicatePrintAttemptError):
        _record(ledger)


def test_new_attempt_token_allows_explicit_reprint(tmp_path) -> None:
    ledger = PrintLedger(tmp_path / "ledger.sqlite3")
    _record(ledger, "attempt-1")
    _record(ledger, "attempt-2")
    assert ledger.get("job-1", "attempt-1") is not None
    assert ledger.get("job-1", "attempt-2") is not None


def test_restart_preserves_idempotency_state(tmp_path) -> None:
    path = tmp_path / "ledger.sqlite3"
    first = PrintLedger(path)
    _record(first)
    first.mark_dispatched("job-1", "attempt-1", "spool-7")

    restarted = PrintLedger(path)
    entry = restarted.get("job-1", "attempt-1")
    assert entry is not None
    assert entry.state == JobState.DISPATCHED_TO_SPOOLER
    assert entry.spooler_job_id == "spool-7"
    with pytest.raises(DuplicatePrintAttemptError):
        _record(restarted)


def test_batch_intents_and_transitions_are_atomic(tmp_path) -> None:
    ledger = PrintLedger(tmp_path / "ledger.sqlite3")
    attempts = [
        ("job-1", "attempt-1", "a" * 64),
        ("job-2", "attempt-2", "b" * 64),
    ]

    ledger.record_batch_intent(attempts, printer_name="Test printer")
    keys = [(job_id, token) for job_id, token, _ in attempts]
    ledger.mark_batch_dispatched(keys, "spool-9")
    ledger.mark_batch_acknowledged(keys)

    assert ledger.get("job-1", "attempt-1").state == JobState.ACKNOWLEDGED
    assert ledger.get("job-2", "attempt-2").state == JobState.ACKNOWLEDGED
    assert ledger.get("job-1", "attempt-1").spooler_job_id == "spool-9"
    assert ledger.get("job-2", "attempt-2").spooler_job_id == "spool-9"
