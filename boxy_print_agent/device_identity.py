"""Local identity facts safe to share in pairing and heartbeat payloads."""

from __future__ import annotations

import platform
import socket

from . import __version__


def hostname() -> str:
    return socket.gethostname()


def capabilities(*, dry_run: bool) -> dict[str, object]:
    return {
        "artifact_content_types": ["image/png"],
        "at_most_once_ledger": True,
        "dry_run": dry_run,
        "operating_system": platform.system(),
        "agent_version": __version__,
    }
