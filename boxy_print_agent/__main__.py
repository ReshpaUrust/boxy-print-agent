"""Safe command-line entry point for CLI, mock mode, and the thin GUI."""

from __future__ import annotations

import argparse
import getpass
import sys
import threading
from pathlib import Path

from .api_client import HttpBoxyClient
from .autostart import install_autostart, remove_autostart
from .app import AgentApplication, runtime_paths
from .config import ConfigStore, default_data_dir
from .gui import AgentWindow
from .idempotency import PrintLedger
from .logging_setup import configure_logging
from .mock_api import MockBoxyClient, create_mock_job
from .printer import DryRunPrinterBackend, WindowsPrinterBackend
from .secrets import DpapiDeviceTokenStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Boxy Print Agent")
    parser.add_argument("--config", type=Path, default=default_data_dir() / "config.json")
    parser.add_argument("--server-url", help="Save a non-secret Boxy base URL")
    parser.add_argument("--printer", help="Select a locally installed Windows printer")
    parser.add_argument("--dev", action="store_true", help="Use the in-process mock Boxy API")
    parser.add_argument("--dry-run", action="store_true", help="Never call the Windows spooler")
    parser.add_argument("--real-print", action="store_true", help="Explicitly enable Windows printing")
    parser.add_argument("--pair", action="store_true", help="Prompt securely for a one-time pairing code")
    parser.add_argument("--once", action="store_true", help="Process at most one claimed job")
    parser.add_argument("--gui", action="store_true", help="Open the minimal tkinter UI")
    parser.add_argument("--background", action="store_true", help="Run polling without a visible window")
    parser.add_argument("--install-autostart", action="store_true", help="Start this executable at Windows sign-in")
    parser.add_argument("--remove-autostart", action="store_true", help="Remove Windows sign-in autostart")
    parser.add_argument("--verbose", action="store_true")
    return parser


def create_application(args: argparse.Namespace) -> tuple[AgentApplication, dict[str, Path]]:
    config_store = ConfigStore(args.config)
    config = config_store.load()
    if args.server_url:
        config.server_url = args.server_url
    if args.dev:
        config.dev_mode = True
    if args.dry_run:
        config.dry_run = True
    elif args.real_print:
        config.dry_run = False
    paths = runtime_paths(args.config)
    token_store = DpapiDeviceTokenStore(paths["token"])
    token = token_store.load()
    logger = configure_logging(paths["logs"], secrets=[token or ""], verbose=args.verbose)

    if args.dev:
        job, payload = create_mock_job(device_id=config.device_id or "dev-device-1")
        client = MockBoxyClient(jobs=[job], artifacts={job.job_id: payload})
        if token:
            client.set_device_token(token)
    else:
        config.dev_mode = False
        config.validate()
        client = HttpBoxyClient(
            config.server_url,
            device_token=token,
            timeout_seconds=config.request_timeout_seconds,
            dev_mode=False,
        )

    if config.dry_run:
        printer = DryRunPrinterBackend()
        if not config.printer_name:
            config.printer_name = printer.list_printers()[0]
    else:
        printer = WindowsPrinterBackend(real_print_enabled=True)
    if args.printer:
        printer.validate_printer(args.printer)
        config.printer_name = args.printer
    config_store.save(config)

    application = AgentApplication(
        config=config,
        config_store=config_store,
        token_store=token_store,
        client=client,
        ledger=PrintLedger(paths["ledger"]),
        printer=printer,
        logger=logger,
    )
    return application, paths


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.real_print and args.dry_run:
        raise SystemExit("Choose either --dry-run or --real-print, not both")
    if args.install_autostart and args.remove_autostart:
        raise SystemExit("Choose either --install-autostart or --remove-autostart")
    if args.install_autostart:
        install_autostart()
        return 0
    if args.remove_autostart:
        remove_autostart()
        return 0
    application, paths = create_application(args)
    try:
        if args.pair:
            pairing_code = getpass.getpass("One-time Boxy pairing code: ")
            application.pair(pairing_code)
        default_gui = bool(getattr(sys, "frozen", False)) and not (
            args.background or args.once or args.pair
        )
        if args.gui or default_gui:
            AgentWindow(application, log_dir=paths["logs"]).run()
            return 0
        if args.once:
            result = application.run_once()
            print(f"{result.outcome}{f' job_id={result.job_id}' if result.job_id else ''}")
            return 2 if result.outcome in {"ERROR", "REVIEW_REQUIRED"} else 0
        stop_event = threading.Event()
        application.polling.run_forever(stop_event)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        application.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
