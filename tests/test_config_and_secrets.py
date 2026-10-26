from __future__ import annotations

import json
import os

import pytest

from boxy_print_agent.config import AgentConfig, ConfigError, ConfigStore, validate_server_url
from boxy_print_agent.logging_setup import configure_logging
from boxy_print_agent.secrets import DpapiDeviceTokenStore


def test_production_rejects_plain_http() -> None:
    with pytest.raises(ConfigError, match="HTTPS"):
        validate_server_url("http://boxy.example.test", dev_mode=False)


def test_dev_allows_only_local_plain_http() -> None:
    validate_server_url("http://127.0.0.1:8765", dev_mode=True)
    with pytest.raises(ConfigError, match="HTTPS"):
        validate_server_url("http://192.168.1.20:8765", dev_mode=True)


def test_server_url_rejects_embedded_credentials() -> None:
    with pytest.raises(ConfigError, match="Credentials"):
        validate_server_url("https://user:password@boxy.example.test", dev_mode=False)


def test_config_rejects_and_never_serializes_device_token(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"device_token": "secret"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="Secrets"):
        ConfigStore(path).load()

    config = AgentConfig(device_id="device-1")
    ConfigStore(path).save(config)
    assert "token" not in path.read_text(encoding="utf-8").casefold()


def test_logging_redacts_tokens_headers_and_pairing_codes(tmp_path) -> None:
    token = "device-super-secret"
    logger = configure_logging(tmp_path, secrets=[token])
    logger.info(
        "token=%s Authorization: Bearer auth-secret pairing_code=pair-secret",
        token,
    )
    for handler in logger.handlers:
        handler.flush()
    text = (tmp_path / "agent.log").read_text(encoding="utf-8")
    assert token not in text
    assert "auth-secret" not in text
    assert "pair-secret" not in text
    assert text.count("[REDACTED]") >= 3


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is Windows-only")
def test_dpapi_round_trip_is_not_plaintext(tmp_path) -> None:
    token = "scoped-device-token-for-test"
    path = tmp_path / "device-token.dpapi"
    store = DpapiDeviceTokenStore(path)
    store.save(token)
    assert token.encode() not in path.read_bytes()
    assert store.load() == token
    store.delete()
    assert not path.exists()
