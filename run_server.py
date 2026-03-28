"""Lightweight entrypoint for running the LocalClaw web server."""

from __future__ import annotations

import os
import socket
import sys


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8016


def _get_port() -> int:
    raw = os.getenv("LOCALCLAW_SERVER_PORT", str(DEFAULT_PORT)).strip()
    try:
        port = int(raw)
    except ValueError:
        print(f"Invalid LOCALCLAW_SERVER_PORT={raw!r}, falling back to {DEFAULT_PORT}.")
        return DEFAULT_PORT
    if not (1 <= port <= 65535):
        print(f"Out-of-range LOCALCLAW_SERVER_PORT={port}, falling back to {DEFAULT_PORT}.")
        return DEFAULT_PORT
    return port


def _is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def main() -> int:
    host = os.getenv("LOCALCLAW_SERVER_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    port = _get_port()

    print("Python starting...")
    print(f"Python version: {sys.version}")
    print("Importing localclaw...")
    from localclaw.channels.web import create_app  # noqa: F401 - import check for early failures
    print("Import successful!")

    if not _is_port_available(host, port):
        print(f"Port {host}:{port} is already in use.")
        print(f"If LocalClaw is already running, open http://{host}:{port}/ in your browser.")
        print(f"To inspect/stop the process on Windows: netstat -ano | findstr :{port}")
        return 1

    import uvicorn

    print(f"Starting server on http://{host}:{port} ...")
    uvicorn.run(
        "localclaw.channels.web:create_app",
        host=host,
        port=port,
        factory=True,
        use_colors=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
