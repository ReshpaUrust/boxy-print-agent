from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from boxy_print_agent.models import ArtifactMetadata
from boxy_print_agent.printer import WindowsPrinterBackend


def _png(color: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (58, 40), color).save(output, format="PNG")
    return output.getvalue()


def test_windows_batch_uses_one_document_and_one_page_per_png(monkeypatch) -> None:
    calls: list[object] = []

    class FakeDC:
        def CreatePrinterDC(self, name):
            calls.append(("CreatePrinterDC", name))

        def GetDeviceCaps(self, capability):
            return {1: 203, 2: 203, 3: 1000, 4: 1000}[capability]

        def StartDoc(self, name):
            calls.append(("StartDoc", name))
            return 42

        def StartPage(self):
            calls.append("StartPage")

        def GetHandleOutput(self):
            return 99

        def EndPage(self):
            calls.append("EndPage")

        def EndDoc(self):
            calls.append("EndDoc")

        def AbortDoc(self):
            calls.append("AbortDoc")

        def DeleteDC(self):
            calls.append("DeleteDC")

    class FakeDib:
        def __init__(self, image):
            self.image = image

        def draw(self, handle, bounds):
            calls.append(("draw", handle, bounds))

    modules = (
        SimpleNamespace(LOGPIXELSX=1, LOGPIXELSY=2, HORZRES=3, VERTRES=4),
        SimpleNamespace(),
        SimpleNamespace(CreateDC=FakeDC),
    )
    monkeypatch.setattr(WindowsPrinterBackend, "_modules", staticmethod(lambda: modules))
    monkeypatch.setattr("boxy_print_agent.printer.ImageWin.Dib", FakeDib)
    monkeypatch.setattr(WindowsPrinterBackend, "validate_printer", lambda self, name: None)
    backend = WindowsPrinterBackend(real_print_enabled=True)
    metadata = ArtifactMetadata(
        artifact_id="artifact",
        content_type="image/png",
        byte_size=1,
        sha256="a" * 64,
        width_mm=58.0,
        height_mm=40.0,
    )

    result = backend.print_png_batch(
        [(_png("white"), metadata, 1), (_png("blue"), metadata, 1)],
        printer_name="Xprinter",
        document_name="Boxy batch test",
    )

    assert result.spooler_job_id == "42"
    assert sum(1 for call in calls if isinstance(call, tuple) and call[0] == "StartDoc") == 1
    assert calls.count("StartPage") == 2
    assert calls.count("EndPage") == 2
    assert calls.count("EndDoc") == 1
    assert "AbortDoc" not in calls
