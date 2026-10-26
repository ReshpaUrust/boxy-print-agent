"""Device-token storage. Production Windows storage is encrypted with DPAPI."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Protocol


class SecretStoreError(RuntimeError):
    pass


class DeviceTokenStore(Protocol):
    def save(self, token: str) -> None: ...

    def load(self) -> str | None: ...

    def delete(self) -> None: ...


class DpapiDeviceTokenStore:
    """Encrypt the scoped token for the current Windows user via DPAPI."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @staticmethod
    def _win32crypt():
        if os.name != "nt":
            raise SecretStoreError("DPAPI token storage requires Windows")
        try:
            import win32crypt
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise SecretStoreError("pywin32 is required for DPAPI token storage") from exc
        return win32crypt

    def save(self, token: str) -> None:
        if not token:
            raise SecretStoreError("Refusing to store an empty device token")
        win32crypt = self._win32crypt()
        protected = win32crypt.CryptProtectData(
            token.encode("utf-8"),
            "Boxy Print Agent device token",
            None,
            None,
            None,
            0,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(base64.b64encode(protected))
        os.replace(temporary, self.path)

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        win32crypt = self._win32crypt()
        try:
            protected = base64.b64decode(self.path.read_bytes(), validate=True)
            _, clear = win32crypt.CryptUnprotectData(protected, None, None, None, 0)
            return clear.decode("utf-8")
        except Exception as exc:
            raise SecretStoreError("Unable to decrypt the stored device token") from exc

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)


class MemoryDeviceTokenStore:
    """Test/dev-only secret store that never writes a token to disk."""

    def __init__(self, token: str | None = None):
        self.token = token

    def save(self, token: str) -> None:
        self.token = token

    def load(self) -> str | None:
        return self.token

    def delete(self) -> None:
        self.token = None
