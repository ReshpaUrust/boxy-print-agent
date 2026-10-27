from __future__ import annotations

from unittest.mock import Mock, patch

from boxy_print_agent.__main__ import build_parser, create_application
from boxy_print_agent.autostart import background_command, install_autostart, remove_autostart
from boxy_print_agent.config import AgentConfig, ConfigStore


class FakeRegistry:
    def __init__(self):
        self.value = None
        self.write_count = 0
        self.delete_count = 0

    def read(self):
        return self.value

    def write(self, command):
        self.value = command
        self.write_count += 1

    def delete(self):
        self.value = None
        self.delete_count += 1


def test_frozen_autostart_command_runs_same_exe_in_background_without_powershell() -> None:
    command = background_command(executable=r"C:\Program Files\Boxy\BoxyPrintAgent.exe", frozen=True)
    assert 'BoxyPrintAgent.exe" --background' in command
    assert "powershell" not in command.casefold()
    assert "python" not in command.casefold()


def test_autostart_install_and_remove_are_idempotent() -> None:
    registry = FakeRegistry()
    command = '"C:\\Boxy\\BoxyPrintAgent.exe" --background'

    assert install_autostart(registry=registry, command=command) is True
    assert install_autostart(registry=registry, command=command) is False
    assert registry.write_count == 1
    assert remove_autostart(registry=registry) is True
    assert remove_autostart(registry=registry) is False
    assert registry.delete_count == 1


def test_background_start_preserves_saved_real_print_mode(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    ConfigStore(config_path).save(AgentConfig(
        server_url="http://127.0.0.1:8765",
        printer_name="Xprinter XP-365B",
        dev_mode=True,
        dry_run=False,
    ))
    args = build_parser().parse_args(["--config", str(config_path), "--dev", "--background"])
    printer = Mock()
    printer.list_printers.return_value = ["Xprinter XP-365B"]
    with patch("boxy_print_agent.__main__.WindowsPrinterBackend", return_value=printer) as backend:
        application, _ = create_application(args)
    try:
        assert application.config.dry_run is False
        assert application.config.printer_name == "Xprinter XP-365B"
        backend.assert_called_once_with(real_print_enabled=True)
    finally:
        application.shutdown()


def test_dry_run_flag_explicitly_updates_saved_mode(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    ConfigStore(config_path).save(AgentConfig(
        server_url="http://127.0.0.1:8765",
        printer_name="Xprinter XP-365B",
        dev_mode=True,
        dry_run=False,
    ))
    args = build_parser().parse_args(["--config", str(config_path), "--dev", "--dry-run", "--once"])
    application, _ = create_application(args)
    try:
        assert application.config.dry_run is True
        assert ConfigStore(config_path).load().dry_run is True
    finally:
        application.shutdown()
