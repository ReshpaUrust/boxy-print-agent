"""Non-secret local configuration and secure URL policy."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


class ConfigError(ValueError):
    pass


SECRET_CONFIG_KEYS = {
    "device_token",
    "token",
    "authorization",
    "pairing_code",
    "wb_api_token",
}


@dataclass(slots=True)
class AgentConfig:
    server_url: str = "http://127.0.0.1:8765"
    device_id: str = ""
    display_name: str = "Boxy Print Agent"
    printer_name: str = ""
    polling_interval_seconds: float = 3.0
    heartbeat_interval_seconds: float = 20.0
    request_timeout_seconds: float = 10.0
    max_artifact_bytes: int = 10 * 1024 * 1024
    dev_mode: bool = True
    dry_run: bool = True

    def validate(self) -> None:
        validate_server_url(self.server_url, dev_mode=self.dev_mode)
        if not 2 <= self.polling_interval_seconds <= 300:
            raise ConfigError("polling_interval_seconds must be between 2 and 300")
        if not 5 <= self.heartbeat_interval_seconds <= 3600:
            raise ConfigError("heartbeat_interval_seconds must be between 5 and 3600")
        if not 1 <= self.request_timeout_seconds <= 120:
            raise ConfigError("request_timeout_seconds must be between 1 and 120")
        if not 1024 <= self.max_artifact_bytes <= 100 * 1024 * 1024:
            raise ConfigError("max_artifact_bytes is outside the safe range")

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "AgentConfig":
        forbidden = SECRET_CONFIG_KEYS.intersection(key.casefold() for key in data)
        if forbidden:
            raise ConfigError("Secrets must not be stored in config.json")
        known = {field.name for field in cls.__dataclass_fields__.values()}
        config = cls(**{key: value for key, value in data.items() if key in known})
        config.validate()
        return config

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_server_url(server_url: str, *, dev_mode: bool) -> None:
    parsed = urlparse(server_url)
    if parsed.username or parsed.password:
        raise ConfigError("Credentials are not allowed in server_url")
    if parsed.query or parsed.fragment:
        raise ConfigError("server_url must not contain query or fragment")
    if parsed.scheme == "https" and parsed.hostname:
        return
    localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if dev_mode and parsed.scheme == "http" and localhost:
        return
    raise ConfigError("HTTPS is required; HTTP is allowed only for localhost in dev mode")


class ConfigStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> AgentConfig:
        if not self.path.exists():
            config = AgentConfig()
            config.validate()
            return config
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ConfigError("Configuration root must be a JSON object")
        return AgentConfig.from_dict(data)

    def save(self, config: AgentConfig) -> None:
        config.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(config.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "BoxyPrintAgent"
    return Path.home() / ".boxy-print-agent"
