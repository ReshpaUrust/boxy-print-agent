"""Fixed, least-privilege HTTP contract for the future Boxy server API."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote

import httpx

from .config import validate_server_url
from .logging_setup import redact_text
from .models import DeviceHeartbeat, JobState, PairingResult, PrintJob


class ApiClientError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class BoxyClient(Protocol):
    def pair(self, pairing_code: str, *, display_name: str, hostname: str, capabilities: dict[str, Any]) -> PairingResult: ...

    def set_device_token(self, token: str) -> None: ...

    def heartbeat(self, heartbeat: DeviceHeartbeat) -> None: ...

    def claim_next(self, device_id: str) -> PrintJob | None: ...

    def download_artifact(self, job: PrintJob, *, max_bytes: int) -> bytes: ...

    def update_job_status(
        self,
        job: PrintJob,
        status: JobState,
        *,
        code: str = "",
        message: str = "",
        spooler_job_id: str | None = None,
    ) -> None: ...


class HttpBoxyClient:
    API_PREFIX = "/api/print-agent/v1"

    def __init__(
        self,
        server_url: str,
        *,
        device_token: str | None = None,
        timeout_seconds: float = 10,
        dev_mode: bool = False,
        transport: httpx.BaseTransport | None = None,
    ):
        validate_server_url(server_url, dev_mode=dev_mode)
        self._device_token = device_token
        self._client = httpx.Client(
            base_url=server_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            verify=True,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def set_device_token(self, token: str) -> None:
        if not token:
            raise ApiClientError("AUTH_MISSING", "Device token is empty")
        self._device_token = token

    def pair(
        self,
        pairing_code: str,
        *,
        display_name: str,
        hostname: str,
        capabilities: dict[str, Any],
    ) -> PairingResult:
        if not pairing_code:
            raise ApiClientError("PAIRING_CODE_MISSING", "Pairing code is required")
        payload = self._request_json(
            "POST",
            f"{self.API_PREFIX}/pair",
            authenticated=False,
            json={
                "pairing_code": pairing_code,
                "display_name": display_name,
                "hostname": hostname,
                "capabilities": capabilities,
            },
        )
        try:
            return PairingResult(
                device_id=str(payload["device_id"]),
                device_token=str(payload["device_token"]),
                display_name=str(payload.get("display_name", display_name)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiClientError("INVALID_PAIRING_RESPONSE", "Pairing response is incomplete") from exc

    def heartbeat(self, heartbeat: DeviceHeartbeat) -> None:
        self._request_json(
            "POST",
            f"{self.API_PREFIX}/devices/{quote(heartbeat.device_id, safe='')}/heartbeat",
            json=heartbeat.as_dict(),
        )

    def claim_next(self, device_id: str) -> PrintJob | None:
        response = self._request(
            "POST",
            f"{self.API_PREFIX}/devices/{quote(device_id, safe='')}/jobs/claim-next",
            json={"device_id": device_id},
        )
        if response.status_code == 204:
            return None
        payload = self._json_from_response(response)
        try:
            return PrintJob.from_dict(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise ApiClientError("INVALID_JOB_RESPONSE", "Claimed job response is invalid") from exc

    def download_artifact(self, job: PrintJob, *, max_bytes: int) -> bytes:
        headers = self._authenticated_headers({"Accept": job.artifact.content_type})
        try:
            with self._client.stream(
                "GET",
                f"{self.API_PREFIX}/jobs/{quote(job.job_id, safe='')}/artifact",
                headers=headers,
            ) as response:
                self._check_response(response)
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > max_bytes:
                            raise ApiClientError("ARTIFACT_TOO_LARGE", "Artifact exceeds the configured size limit")
                    except ValueError as exc:
                        raise ApiClientError("INVALID_ARTIFACT_RESPONSE", "Invalid Content-Length") from exc
                received_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
                if received_type != job.artifact.content_type.casefold():
                    raise ApiClientError("ARTIFACT_MIME_MISMATCH", "Artifact Content-Type does not match metadata")
                payload = bytearray()
                for chunk in response.iter_bytes():
                    payload.extend(chunk)
                    if len(payload) > max_bytes:
                        raise ApiClientError("ARTIFACT_TOO_LARGE", "Artifact exceeds the configured size limit")
                return bytes(payload)
        except httpx.TimeoutException as exc:
            raise ApiClientError("SERVER_TIMEOUT", "Boxy server request timed out", retryable=True) from exc
        except httpx.NetworkError as exc:
            raise ApiClientError("SERVER_UNAVAILABLE", "Boxy server is unavailable", retryable=True) from exc

    def update_job_status(
        self,
        job: PrintJob,
        status: JobState,
        *,
        code: str = "",
        message: str = "",
        spooler_job_id: str | None = None,
    ) -> None:
        self._request_json(
            "POST",
            f"{self.API_PREFIX}/jobs/{quote(job.job_id, safe='')}/status",
            json={
                "status": status.value,
                "print_attempt_token": job.print_attempt_token,
                "code": code,
                "message": redact_text(message)[:500],
                "spooler_job_id": spooler_job_id,
            },
            headers={"Idempotency-Key": f"{job.job_id}:{job.print_attempt_token}:{status.value}"},
        )

    def _request_json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return self._json_from_response(self._request(method, path, **kwargs))

    def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        request_headers = self._authenticated_headers(headers) if authenticated else dict(headers or {})
        try:
            response = self._client.request(method, path, headers=request_headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise ApiClientError("SERVER_TIMEOUT", "Boxy server request timed out", retryable=True) from exc
        except httpx.NetworkError as exc:
            raise ApiClientError("SERVER_UNAVAILABLE", "Boxy server is unavailable", retryable=True) from exc
        self._check_response(response)
        return response

    def _authenticated_headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
        if not self._device_token:
            raise ApiClientError("AUTH_MISSING", "Device is not paired")
        request_headers = dict(headers or {})
        request_headers["Authorization"] = f"Bearer {self._device_token}"
        return request_headers

    @staticmethod
    def _check_response(response: httpx.Response) -> None:
        if 300 <= response.status_code < 400:
            raise ApiClientError("UNEXPECTED_REDIRECT", "Boxy API redirects are not allowed")
        if response.status_code >= 400:
            retryable = response.status_code >= 500 or response.status_code == 429
            raise ApiClientError(
                f"HTTP_{response.status_code}",
                f"Boxy API request failed with HTTP {response.status_code}",
                retryable=retryable,
            )

    @staticmethod
    def _json_from_response(response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 204 or not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiClientError("INVALID_JSON", "Boxy API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ApiClientError("INVALID_JSON", "Boxy API response must be a JSON object")
        return payload
