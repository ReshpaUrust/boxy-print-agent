"""Minimal tkinter shell; it delegates all operations to AgentApplication."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .app import AgentApplication


class AgentWindow:
    def __init__(self, application: AgentApplication, *, log_dir: Path):
        self.application = application
        self.log_dir = Path(log_dir)
        self.root = tk.Tk()
        self.root.title("Boxy Print Agent")
        self.root.geometry("520x330")
        self.root.minsize(460, 300)
        self.status_var = tk.StringVar()
        self.device_var = tk.StringVar()
        self.printer_var = tk.StringVar(value=application.config.printer_name)
        self.last_job_var = tk.StringVar(value="—")
        self.pairing_var = tk.StringVar()
        self._build()
        self._refresh()
        self.root.protocol("WM_DELETE_WINDOW", self._exit)

    def _build(self) -> None:
        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Boxy Print Agent", font=("Segoe UI", 18, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 18)
        )
        ttk.Label(frame, text="Статус Boxy:").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Label(frame, textvariable=self.status_var).grid(row=1, column=1, sticky="w", pady=5)
        ttk.Label(frame, text="Устройство:").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Label(frame, textvariable=self.device_var).grid(row=2, column=1, sticky="w", pady=5)
        ttk.Label(frame, text="Принтер:").grid(row=3, column=0, sticky="w", pady=5)
        printers = self.application.printer.list_printers()
        self.printer_combo = ttk.Combobox(
            frame,
            textvariable=self.printer_var,
            values=printers,
            state="readonly",
            width=38,
        )
        self.printer_combo.grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="Последнее задание:").grid(row=4, column=0, sticky="w", pady=5)
        ttk.Label(frame, textvariable=self.last_job_var).grid(row=4, column=1, sticky="w", pady=5)

        if not self.application.config.device_id:
            ttk.Label(frame, text="Pairing code:").grid(row=5, column=0, sticky="w", pady=5)
            ttk.Entry(frame, textvariable=self.pairing_var, show="•").grid(row=5, column=1, sticky="ew", pady=5)
            ttk.Button(frame, text="Подключить устройство", command=self._pair).grid(
                row=6, column=1, sticky="w", pady=(8, 0)
            )

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        ttk.Button(buttons, text="Сменить принтер", command=self._save_printer).pack(side="left")
        ttk.Button(buttons, text="Проверить подключение", command=self._check).pack(side="left", padx=8)
        ttk.Button(buttons, text="Открыть логи", command=self._open_logs).pack(side="left")
        ttk.Button(buttons, text="Выход", command=self._exit).pack(side="right")
        frame.columnconfigure(1, weight=1)

    def run(self) -> None:
        if self.application.config.device_id:
            self.application.start()
        self.root.mainloop()

    def _pair(self) -> None:
        code = self.pairing_var.get().strip()
        if not code:
            messagebox.showerror("Boxy", "Введите одноразовый pairing code.")
            return
        self._background(
            lambda: (self.application.pair(code), self.application.start()),
            success="Устройство подключено.",
        )
        self.pairing_var.set("")

    def _save_printer(self) -> None:
        self._background(
            lambda: self.application.change_printer(self.printer_var.get()),
            success="Принтер сохранён.",
        )

    def _check(self) -> None:
        self._background(self.application.check_connection)

    def _background(self, action, *, success: str = "") -> None:
        def worker() -> None:
            try:
                action()
            except Exception as exc:
                self.root.after(0, lambda: messagebox.showerror("Boxy", type(exc).__name__))
            else:
                if success:
                    self.root.after(0, lambda: messagebox.showinfo("Boxy", success))
            self.root.after(0, self._refresh)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh(self) -> None:
        status = self.application.status
        labels = {
            "PRINTER_UNAVAILABLE": "Принтер недоступен",
            "NOT_PAIRED": "○ Не подключено",
            "ONLINE": "● Подключено",
            "SERVER_UNAVAILABLE": "○ Сервер недоступен",
            "JOB_REQUIRES_REVIEW": "○ Задание требует проверки",
        }
        self.status_var.set(labels.get(status.connection, status.connection))
        self.device_var.set(self.application.config.display_name or "—")
        if status.last_job_id:
            label = "Отправлено на принтер" if status.last_job_status == "DISPATCHED_TO_SPOOLER" else status.last_job_status
            self.last_job_var.set(f"#{status.last_job_id} — {label}")
        self.root.after(1000, self._refresh)

    def _open_logs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self.log_dir)  # type: ignore[attr-defined]

    def _exit(self) -> None:
        self.application.shutdown()
        self.root.destroy()
