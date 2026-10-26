from __future__ import annotations

from dataclasses import replace

import pytest

from boxy_print_agent.artifact import ArtifactValidationError, validate_artifact


def test_valid_png_passes(mock_job) -> None:
    job, payload = mock_job
    validate_artifact(payload, job.artifact, max_bytes=1024 * 1024)


@pytest.mark.parametrize(
    ("metadata_change", "payload_change", "code"),
    [
        ({"content_type": "application/pdf"}, None, "UNSUPPORTED_ARTIFACT"),
        ({"sha256": "0" * 64}, None, "ARTIFACT_CHECKSUM_MISMATCH"),
        ({}, b"not-a-png", "ARTIFACT_SIZE_MISMATCH"),
    ],
)
def test_invalid_artifacts_are_rejected(mock_job, metadata_change, payload_change, code) -> None:
    job, payload = mock_job
    candidate = payload if payload_change is None else payload_change
    metadata = replace(job.artifact, **metadata_change)
    with pytest.raises(ArtifactValidationError) as error:
        validate_artifact(candidate, metadata, max_bytes=1024 * 1024)
    assert error.value.code == code


def test_corrupt_png_with_matching_metadata_is_rejected(mock_job) -> None:
    import hashlib

    job, _ = mock_job
    payload = b"not-a-png"
    metadata = replace(
        job.artifact,
        byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        width_px=None,
        height_px=None,
    )
    with pytest.raises(ArtifactValidationError) as error:
        validate_artifact(payload, metadata, max_bytes=1024 * 1024)
    assert error.value.code == "CORRUPT_PNG"


def test_oversized_artifact_is_rejected(mock_job) -> None:
    job, payload = mock_job
    with pytest.raises(ArtifactValidationError) as error:
        validate_artifact(payload, job.artifact, max_bytes=len(payload) - 1)
    assert error.value.code == "ARTIFACT_TOO_LARGE"
