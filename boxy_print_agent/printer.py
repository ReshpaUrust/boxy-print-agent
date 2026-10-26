"""Printer abstraction with explicit dry-run and Windows implementations."""

from __future__ import annotations

import os
from io import BytesIO
from typing import Protocol

from PIL import Image, ImageWin

from .models import ArtifactMetadata, PrintResult


class PrinterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PrinterBackend(Protocol):
    def list_printers(self) -> list[str]: ...

    def validate_printer(self, name: str) -> None: ...

    def print_png(
        self,
        payload: bytes,
        metadata: ArtifactMetadata,
        *,
        printer_name: str,
        copies: int,
    ) -> PrintResult: ...


class DryRunPrinterBackend:
    def __init__(self, printers: list[str] | None = None):
        self.printers = list(printers or ["Boxy Dry-Run Printer"])
        self.calls: list[dict[str, object]] = []

    def list_printers(self) -> list[str]:
        return list(self.printers)

    def validate_printer(self, name: str) -> None:
        if name not in self.printers:
            raise PrinterError("DEVICE_CONFIGURATION_ERROR", "Configured printer is unavailable")

    def print_png(
        self,
        payload: bytes,
        metadata: ArtifactMetadata,
        *,
        printer_name: str,
        copies: int,
    ) -> PrintResult:
        self.validate_printer(printer_name)
        self.calls.append(
            {
                "byte_size": len(payload),
                "artifact_id": metadata.artifact_id,
                "printer_name": printer_name,
                "copies": copies,
            }
        )
        return PrintResult(spooler_job_id=f"dry-run-{len(self.calls)}", detail="Dry-run only")


class WindowsPrinterBackend:
    """Send a validated PNG to one explicitly configured Windows printer via GDI."""

    def __init__(self, *, real_print_enabled: bool = False):
        if not real_print_enabled:
            raise PrinterError("REAL_PRINT_NOT_ENABLED", "Real printing requires explicit opt-in")
        if os.name != "nt":
            raise PrinterError("WINDOWS_REQUIRED", "Windows printer backend requires Windows")

    @staticmethod
    def _modules():
        try:
            import win32con
            import win32print
            import win32ui
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise PrinterError("PYWIN32_MISSING", "pywin32 is required for real printing") from exc
        return win32con, win32print, win32ui

    def list_printers(self) -> list[str]:
        _, win32print, _ = self._modules()
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        names = {entry[2] for entry in win32print.EnumPrinters(flags)}
        return sorted(names, key=str.casefold)

    def validate_printer(self, name: str) -> None:
        if not name or name not in self.list_printers():
            raise PrinterError("DEVICE_CONFIGURATION_ERROR", "Configured printer is unavailable")

    def print_png(
        self,
        payload: bytes,
        metadata: ArtifactMetadata,
        *,
        printer_name: str,
        copies: int,
    ) -> PrintResult:
        self.validate_printer(printer_name)
        if metadata.content_type != "image/png":
            raise PrinterError("UNSUPPORTED_ARTIFACT", "Windows MVP supports PNG only")
        if copies != 1:
            raise PrinterError("UNSUPPORTED_COPIES", "MVP accepts exactly one copy per print attempt")
        win32con, _, win32ui = self._modules()
        dc = win32ui.CreateDC()
        try:
            dc.CreatePrinterDC(printer_name)
            dpi_x = dc.GetDeviceCaps(win32con.LOGPIXELSX)
            dpi_y = dc.GetDeviceCaps(win32con.LOGPIXELSY)
            printable_width = dc.GetDeviceCaps(win32con.HORZRES)
            printable_height = dc.GetDeviceCaps(win32con.VERTRES)
            with Image.open(BytesIO(payload)) as opened:
                image = opened.convert("RGB")
            target_width, target_height = _target_pixels(metadata, image.size, dpi_x, dpi_y)
            if target_width > printable_width or target_height > printable_height:
                raise PrinterError("ARTIFACT_DOES_NOT_FIT", "Sticker physical size exceeds printable area")
            job_id = dc.StartDoc(f"Boxy artifact {metadata.artifact_id}")
            dc.StartPage()
            ImageWin.Dib(image).draw(dc.GetHandleOutput(), (0, 0, target_width, target_height))
            dc.EndPage()
            dc.EndDoc()
            return PrintResult(spooler_job_id=str(job_id) if job_id is not None else None)
        except PrinterError:
            raise
        except Exception as exc:
            try:
                dc.AbortDoc()
            except Exception:
                pass
            raise PrinterError("SPOOLER_ERROR", "Windows spooler rejected the print job") from exc
        finally:
            dc.DeleteDC()


def _target_pixels(
    metadata: ArtifactMetadata,
    image_size: tuple[int, int],
    dpi_x: int,
    dpi_y: int,
) -> tuple[int, int]:
    if metadata.width_mm is not None and metadata.height_mm is not None:
        return (
            round(metadata.width_mm / 25.4 * dpi_x),
            round(metadata.height_mm / 25.4 * dpi_y),
        )
    if metadata.dpi:
        return (
            round(image_size[0] / metadata.dpi * dpi_x),
            round(image_size[1] / metadata.dpi * dpi_y),
        )
    raise PrinterError("PHYSICAL_SIZE_MISSING", "Artifact has no physical size or DPI metadata")
