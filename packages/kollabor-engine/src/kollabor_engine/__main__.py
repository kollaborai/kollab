"""python -m kollabor_engine serve [--port PORT] [--log-level LEVEL]"""

import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path
import sys


def _build_engine_log_path(now: datetime | None = None) -> Path:
    """Return the per-session engine log path for the current project."""
    from kollabor_config.config_utils import get_logs_dir

    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    return get_logs_dir() / f"kollab-engine-{timestamp}.log"


def _configure_logging(log_level: str, log_path: Path) -> None:
    """Send engine logging and stderr prints to the timestamped log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, log_level.upper())

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )
    sys.stderr = open(log_path, "a", buffering=1, encoding="utf-8")  # noqa: SIM115
    logging.getLogger(__name__).info("Engine logging to %s", log_path)


def main():
    parser = argparse.ArgumentParser(description="Kollab Engine Server")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Start the engine server")
    serve.add_argument(
        "--port", type=int, default=7433, help="Port to bind (default: 7433)"
    )
    serve.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1, local only)",
    )

    args = parser.parse_args()

    if args.command != "serve":
        parser.print_help()
        sys.exit(1)

    _configure_logging(args.log_level, _build_engine_log_path())

    import uvicorn  # type: ignore[import-not-found]

    # Generate auth token BEFORE starting server so the file exists
    # when the parent process reads it after the READY signal.
    from .auth import generate_token
    from .server import create_app

    generate_token()

    app = create_app()
    port = args.port
    host = args.host
    log_level = args.log_level

    async def _serve():
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=log_level,
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)

        # Patch startup to emit READY only after uvicorn is actually listening.
        # This prevents the race condition where Rust reads READY and immediately
        # tries to connect before the socket is bound.
        _original_startup = server.startup

        async def _startup_with_ready(sockets=None):
            await _original_startup(sockets)
            print(f"READY:{port}", flush=True)

        server.startup = _startup_with_ready
        await server.serve()

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
