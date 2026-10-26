"""In-process development server contract implementation; never a production token source."""

from __future__ import annotations

import hashlib
import threading
from io import BytesIO
from typing import Any

from PIL import Image

from .api_client import ApiClientError
from .models import ArtifactMetadata, DeviceHeartbeat, JobState, PairingResult, PrintJob


class MockBoxyClient:
    def __init__(self, *, jobs: list[PrintJob] | None = None, artifacts: dict[str, bytes] | None = None):
        self._jobs = list(jobs or [])
        self._artifacts = dict(artifacts or {})
        self._claimed: set[tuple[str, str]] = set()
        self._used_pairing_codes: set[str] = set()
        self._lock = threading.Lock()
        self.device_token: str | None = None
        self.heartbeats: list[DeviceHeartbeat] = []
        self.status_updates: list[dict[str, Any]] = []

    def pair(
        self,
        pairing_code: str,
        *,
        display_name: str,
        hostname: str,
        capabilities: dict[str, Any],
    ) -> PairingResult:
        del hostname, capabilities
        with self._lock:
            if not pairing_code or pairing_code in self._used_pairing_codes:
                raise ApiClientError("PAIRING_CODE_INVALID", "Pairing code is invalid or already used")
            self._used_pairing_codes.add(pairing_code)
        return PairingResult("dev-device-1", "dev-scoped-token-not-for-production", display_name)

    def set_device_token(self, token: str) -> None:
        self.device_token = token

    def heartbeat(self, heartbeat: DeviceHeartbeat) -> None:
        self.heartbeats.append(heartbeat)

    def claim_next(self, device_id: str) -> PrintJob | None:
        with self._lock:
            for job in self._jobs:
                key = (job.job_id, job.print_attempt_token)
                if job.device_id == device_id and key not in self._claimed:
                    self._claimed.add(key)
                    return job
        return None

    def download_artifact(self, job: PrintJob, *, max_bytes: int) -> bytes:
        payload = self._artifacts.get(job.job_id)
        if payload is None:
            raise ApiClientError("ARTIFACT_NOT_FOUND", "Mock artifact is missing")
        if len(payload) > max_bytes:
            raise ApiClientError("ARTIFACT_TOO_LARGE", "Artifact exceeds the configured size limit")
        return payload

    def update_job_status(
        self,
        job: PrintJob,
        status: JobState,
        *,
        code: str = "",
        message: str = "",
        spooler_job_id: str | None = None,
    ) -> None:
        self.status_updates.append(
            {
                "job_id": job.job_id,
                "print_attempt_token": job.print_attempt_token,
                "status": status,
                "code": code,
                "message": message,
                "spooler_job_id": spooler_job_id,
            }
        )


def create_mock_job(
    *,
    job_id: str = "job-123",
    device_id: str = "dev-device-1",
    attempt_token: str = "attempt-1",
) -> tuple[PrintJob, bytes]:
    stream = BytesIO()
    Image.new("RGB", (240, 160), "white").save(stream, format="PNG", dpi=(203, 203))
    payload = stream.getvalue()
    metadata = ArtifactMetadata(
        artifact_id=f"artifact-{job_id}",
        content_type="image/png",
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        width_px=240,
        height_px=160,
        width_mm=30.0,
        height_mm=20.0,
        dpi=203,
    )
    return (
        PrintJob(
            job_id=job_id,
            device_id=device_id,
            print_attempt_token=attempt_token,
            status=JobState.CLAIMED,
            artifact=metadata,
        ),
        payload,
    )
