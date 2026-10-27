"""Per-user Windows autostart without administrator rights or PowerShell."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "BoxyPrintAgent"


class AutostartError(RuntimeError):
    pass


@dataclass(slots=True)
class WindowsRunRegistry:
    def read(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, VALUE_NAME)
                return str(value)
        except FileNotFoundError:
            return None

    def write(self, command: str) -> None:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)

    def delete(self) -> None:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                RUN_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            return


def background_command(*, executable: str | None = None, frozen: bool | None = None) -> str:
    executable = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    arguments = [executable]
    if not frozen:
        arguments.extend(["-m", "boxy_print_agent"])
    arguments.append("--background")
    return subprocess.list2cmdline(arguments)


def install_autostart(*, registry=None, command: str | None = None) -> bool:
    if os.name != "nt" and registry is None:
        raise AutostartError("Windows autostart is available only on Windows")
    registry = registry or WindowsRunRegistry()
    command = command or background_command()
    if registry.read() == command:
        return False
    registry.write(command)
    return True


def remove_autostart(*, registry=None) -> bool:
    if os.name != "nt" and registry is None:
        raise AutostartError("Windows autostart is available only on Windows")
    registry = registry or WindowsRunRegistry()
    if registry.read() is None:
        return False
    registry.delete()
    return True
