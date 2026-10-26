from __future__ import annotations

import json

import httpx
import pytest

from boxy_print_agent.api_client import ApiClientError, HttpBoxyClient
from boxy_print_agent.models import JobState


def _job_payload(job) -> dict[str, object]:
    artifact = job.artifact
    return {
        "job_id": job.job_id,
        "device_id": job.device_id,
        "print_attempt_token": job.print_attempt_token,
        "status": job.status.value,
        "copies": job.copies,
        "artifact": {
            "artifact_id": artifact.artifact_id,
            "content_type": artifact.content_type,
            "byte_size": artifact.byte_size,
            "sha256": artifact.sha256,
            "width_px": artifact.width_px,
            "height_px": artifact.height_px,
            "width_mm": artifact.width_mm,
            "height_mm": artifact.height_mm,
            "dpi": artifact.dpi,
            "orientation": artifact.orientation,
        },
    }


def test_pairing_and_atomic_claim_contract(mock_job) -> None:
    job, _ = mock_job
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/pair"):
            assert "Authorization" not in request.headers
            body = json.loads(request.content)
            assert body["pairing_code"] == "one-time-code"
            return httpx.Response(
                200,
                json={"device_id": "device-1", "device_token": "scoped-token", "display_name": "Desk"},
            )
        assert request.url.path.endswith("/devices/device-1/jobs/claim-next")
        assert request.headers["Authorization"] == "Bearer scoped-token"
        return httpx.Response(200, json=_job_payload(job))

    client = HttpBoxyClient(
        "https://boxy.example.test",
        transport=httpx.MockTransport(handler),
    )
    pairing = client.pair("one-time-code", display_name="Desk", hostname="pc", capabilities={})
    client.set_device_token(pairing.device_token)
    claimed = client.claim_next(pairing.device_id)
    client.close()
    assert claimed == job
    assert [request.method for request in requests] == ["POST", "POST"]


def test_status_callback_has_attempt_scoped_idempotency_key(mock_job) -> None:
    job, _ = mock_job
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={})

    client = HttpBoxyClient(
        "https://boxy.example.test",
        device_token="scoped-token",
        transport=httpx.MockTransport(handler),
    )
    client.update_job_status(job, JobState.PRINT_INTENT)
    client.close()
    assert captured[0].headers["Idempotency-Key"] == "job-123:attempt-1:PRINT_INTENT"


def test_server_timeout_is_safe_and_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = HttpBoxyClient(
        "https://boxy.example.test",
        device_token="scoped-token",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApiClientError) as error:
        client.claim_next("device-1")
    client.close()
    assert error.value.code == "SERVER_TIMEOUT"
    assert error.value.retryable is True


def test_artifact_download_is_bounded_without_trusting_content_length(mock_job) -> None:
    job, _ = mock_job

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"x" * 32)

    client = HttpBoxyClient(
        "https://boxy.example.test",
        device_token="scoped-token",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApiClientError) as error:
        client.download_artifact(job, max_bytes=16)
    client.close()
    assert error.value.code == "ARTIFACT_TOO_LARGE"


def test_artifact_mime_must_match_claimed_metadata(mock_job) -> None:
    job, payload = mock_job

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=payload)

    client = HttpBoxyClient(
        "https://boxy.example.test",
        device_token="scoped-token",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApiClientError) as error:
        client.download_artifact(job, max_bytes=1024 * 1024)
    client.close()
    assert error.value.code == "ARTIFACT_MIME_MISMATCH"
