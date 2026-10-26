"""PyInstaller-safe entry point for the Boxy Print Agent package."""

from boxy_print_agent.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
