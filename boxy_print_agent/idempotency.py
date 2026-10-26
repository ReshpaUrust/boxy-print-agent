"""Durable local at-most-once ledger for print attempts."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import JobState


class DuplicatePrintAttemptError(RuntimeError):
    def __init__(self, job_id: str, attempt_token: str, state: JobState):
        super().__init__(f"Print attempt already exists in state {state.value}")
        self.job_id = job_id
        self.attempt_token = attempt_token
        self.state = state


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    job_id: str
    print_attempt_token: str
    state: JobState
    artifact_sha256: str
    printer_name: str
    spooler_job_id: str | None
    created_at: str
    updated_at: str
    last_error: str


class PrintLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS print_attempts (
                    job_id TEXT NOT NULL,
                    print_attempt_token TEXT NOT NULL,
                    state TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    printer_name TEXT NOT NULL,
                    spooler_job_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (job_id, print_attempt_token)
                )
                """
            )

    def record_intent(
        self,
        *,
        job_id: str,
        attempt_token: str,
        artifact_sha256: str,
        printer_name: str,
    ) -> LedgerEntry:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM print_attempts WHERE job_id = ? AND print_attempt_token = ?",
                (job_id, attempt_token),
            ).fetchone()
            if existing:
                connection.rollback()
                raise DuplicatePrintAttemptError(job_id, attempt_token, JobState(existing["state"]))
            connection.execute(
                """
                INSERT INTO print_attempts (
                    job_id, print_attempt_token, state, artifact_sha256,
                    printer_name, spooler_job_id, created_at, updated_at, last_error
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, '')
                """,
                (
                    job_id,
                    attempt_token,
                    JobState.PRINT_INTENT.value,
                    artifact_sha256,
                    printer_name,
                    now,
                    now,
                ),
            )
            connection.commit()
        entry = self.get(job_id, attempt_token)
        assert entry is not None
        return entry

    def mark_dispatched(self, job_id: str, attempt_token: str, spooler_job_id: str | None) -> None:
        self._transition(
            job_id,
            attempt_token,
            JobState.DISPATCHED_TO_SPOOLER,
            spooler_job_id=spooler_job_id,
        )

    def mark_acknowledged(self, job_id: str, attempt_token: str) -> None:
        self._transition(job_id, attempt_token, JobState.ACKNOWLEDGED)

    def mark_review_required(self, job_id: str, attempt_token: str, error: str) -> None:
        self._transition(job_id, attempt_token, JobState.REVIEW_REQUIRED, error=error)

    def mark_error(self, job_id: str, attempt_token: str, error: str) -> None:
        self._transition(job_id, attempt_token, JobState.ERROR, error=error)

    def _transition(
        self,
        job_id: str,
        attempt_token: str,
        state: JobState,
        *,
        spooler_job_id: str | None = None,
        error: str = "",
    ) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE print_attempts
                SET state = ?, spooler_job_id = COALESCE(?, spooler_job_id),
                    updated_at = ?, last_error = ?
                WHERE job_id = ? AND print_attempt_token = ?
                """,
                (state.value, spooler_job_id, _utc_now(), error[:500], job_id, attempt_token),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown print attempt: {job_id}")

    def get(self, job_id: str, attempt_token: str) -> LedgerEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM print_attempts WHERE job_id = ? AND print_attempt_token = ?",
                (job_id, attempt_token),
            ).fetchone()
        return _entry_from_row(row) if row else None


def _entry_from_row(row: sqlite3.Row) -> LedgerEntry:
    return LedgerEntry(
        job_id=row["job_id"],
        print_attempt_token=row["print_attempt_token"],
        state=JobState(row["state"]),
        artifact_sha256=row["artifact_sha256"],
        printer_name=row["printer_name"],
        spooler_job_id=row["spooler_job_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_error=row["last_error"],
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
