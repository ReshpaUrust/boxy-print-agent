"""Typed contracts shared by API, polling, printing, and UI layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobState(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PRINT_INTENT = "PRINT_INTENT"
    DISPATCHED_TO_SPOOLER = "DISPATCHED_TO_SPOOLER"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ERROR = "ERROR"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    artifact_id: str
    content_type: str
    byte_size: int
    sha256: str
    width_px: int | None = None
    height_px: int | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    dpi: int | None = None
    orientation: str = "portrait"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactMetadata":
        return cls(
            artifact_id=str(data["artifact_id"]),
            content_type=str(data["content_type"]),
            byte_size=int(data["byte_size"]),
            sha256=str(data["sha256"]),
            width_px=_optional_int(data.get("width_px")),
            height_px=_optional_int(data.get("height_px")),
            width_mm=_optional_float(data.get("width_mm")),
            height_mm=_optional_float(data.get("height_mm")),
            dpi=_optional_int(data.get("dpi")),
            orientation=str(data.get("orientation", "portrait")),
        )


@dataclass(frozen=True, slots=True)
class PrintJob:
    job_id: str
    device_id: str
    print_attempt_token: str
    status: JobState
    artifact: ArtifactMetadata
    copies: int = 1
    batch_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PrintJob":
        return cls(
            job_id=str(data["job_id"]),
            device_id=str(data["device_id"]),
            print_attempt_token=str(data["print_attempt_token"]),
            status=JobState(str(data.get("status", JobState.CLAIMED))),
            artifact=ArtifactMetadata.from_dict(dict(data["artifact"])),
            copies=int(data.get("copies", 1)),
            batch_id=str(data.get("batch_id", "")),
        )


@dataclass(frozen=True, slots=True)
class ClaimedBatch:
    batch_id: str
    jobs: tuple[PrintJob, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClaimedBatch":
        jobs = tuple(PrintJob.from_dict(dict(row)) for row in data["jobs"])
        if not jobs:
            raise ValueError("Claimed batch is empty")
        return cls(batch_id=str(data["batch_id"]), jobs=jobs)


@dataclass(frozen=True, slots=True)
class PairingResult:
    device_id: str
    device_token: str
    display_name: str


@dataclass(frozen=True, slots=True)
class DeviceHeartbeat:
    device_id: str
    display_name: str
    hostname: str
    selected_printer: str
    agent_version: str
    status: str
    capabilities: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "display_name": self.display_name,
            "hostname": self.hostname,
            "selected_printer": self.selected_printer,
            "agent_version": self.agent_version,
            "status": self.status,
            "capabilities": self.capabilities,
        }


@dataclass(frozen=True, slots=True)
class PrintResult:
    spooler_job_id: str | None
    detail: str = "Sent to Windows spooler"


@dataclass(frozen=True, slots=True)
class PollResult:
    outcome: str
    job_id: str | None = None
    detail: str = ""
    code: str = ""


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)
