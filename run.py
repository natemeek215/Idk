#!/usr/bin/env python3
"""Set Builder -- single entry point.

    python run.py            start the app and open the browser
    python run.py --no-open  start without opening a browser
    python run.py <command>  anything else is passed to the CLI

Everything else has a sane default: no config file to edit, no flags to learn.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser


def _venv_python() -> str | None:
    """Path to the venv interpreter, if we are not already running under it."""
    root = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.path.join(root, ".venv", "bin", "python"),
                      os.path.join(root, ".venv", "Scripts", "python.exe")):
        if os.path.exists(candidate) and \
                os.path.realpath(candidate) != os.path.realpath(sys.executable):
            return candidate
    return None


def _reexec_into_venv() -> None:
    """Re-run under ./.venv if the current interpreter lacks the deps."""
    try:
        import fastapi  # noqa: F401  -- presence probe, not a usage
        return
    except ImportError:
        pass
    interpreter = _venv_python()
    if not interpreter:
        print("Dependencies are missing and no .venv was found.\n"
              "Run ./setup.sh first.", file=sys.stderr)
        sys.exit(1)
    os.execv(interpreter, [interpreter, os.path.abspath(__file__)] + sys.argv[1:])


def _free_port(preferred: int) -> int:
    """Use the preferred port, or the next free one if it is taken."""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def _open_browser_when_ready(url: str, host: str, port: int) -> None:
    deadline = time.time() + 20
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.4)
            if probe.connect_ex((host, port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.2)


def main() -> None:
    _reexec_into_venv()

    arguments = sys.argv[1:]
    # Anything that is not a server flag goes to the CLI.
    if arguments and not arguments[0].startswith("-"):
        from setbuilder.cli import main as cli_main
        cli_main(arguments)
        return

    import uvicorn

    from setbuilder import config

    config.ensure_home()
    host = config.HOST
    port = _free_port(config.PORT)
    url = f"http://{host}:{port}"

    if port != config.PORT:
        print(f"Port {config.PORT} was busy — using {port} instead.")

    if "--no-open" not in arguments:
        threading.Thread(target=_open_browser_when_ready,
                         args=(url, host, port), daemon=True).start()

    print(f"\n  Set Builder  →  {url}")
    print(f"  data in {config.HOME}")
    print("  ctrl-c to stop\n")

    uvicorn.run("setbuilder.api:app", host=host, port=port,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
