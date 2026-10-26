"""Validation of immutable print artifacts before any spooler interaction."""

from __future__ import annotations

import hashlib
import hmac
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from .models import ArtifactMetadata


class ArtifactValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


ALLOWED_CONTENT_TYPES = {"image/png"}
MAX_IMAGE_DIMENSION = 20_000


def validate_artifact(
    payload: bytes,
    metadata: ArtifactMetadata,
    *,
    max_bytes: int,
) -> None:
    if metadata.content_type not in ALLOWED_CONTENT_TYPES:
        raise ArtifactValidationError("UNSUPPORTED_ARTIFACT", "Only PNG artifacts are supported")
    if metadata.byte_size < 1 or metadata.byte_size > max_bytes or len(payload) > max_bytes:
        raise ArtifactValidationError("ARTIFACT_TOO_LARGE", "Artifact exceeds the configured size limit")
    if len(payload) != metadata.byte_size:
        raise ArtifactValidationError("ARTIFACT_SIZE_MISMATCH", "Artifact byte size does not match metadata")
    digest = hashlib.sha256(payload).hexdigest()
    if not _constant_time_equal(digest, metadata.sha256.casefold()):
        raise ArtifactValidationError("ARTIFACT_CHECKSUM_MISMATCH", "Artifact checksum mismatch")
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != "PNG":
                raise ArtifactValidationError("CORRUPT_PNG", "Artifact is not a PNG image")
            image.verify()
        with Image.open(BytesIO(payload)) as image:
            width, height = image.size
    except ArtifactValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ArtifactValidationError("CORRUPT_PNG", "PNG artifact is corrupt") from exc

    if width < 1 or height < 1 or width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise ArtifactValidationError("INVALID_IMAGE_DIMENSIONS", "PNG dimensions are outside the safe range")
    if metadata.width_px is not None and width != metadata.width_px:
        raise ArtifactValidationError("IMAGE_DIMENSION_MISMATCH", "PNG width does not match metadata")
    if metadata.height_px is not None and height != metadata.height_px:
        raise ArtifactValidationError("IMAGE_DIMENSION_MISMATCH", "PNG height does not match metadata")
    if metadata.orientation not in {"portrait", "landscape"}:
        raise ArtifactValidationError("INVALID_ORIENTATION", "Unsupported artifact orientation")
    if metadata.width_mm is not None and not 1 <= metadata.width_mm <= 1000:
        raise ArtifactValidationError("INVALID_PHYSICAL_SIZE", "Artifact width is outside the safe range")
    if metadata.height_mm is not None and not 1 <= metadata.height_mm <= 1000:
        raise ArtifactValidationError("INVALID_PHYSICAL_SIZE", "Artifact height is outside the safe range")


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
