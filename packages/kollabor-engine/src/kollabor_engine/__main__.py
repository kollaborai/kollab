"""python -m kollabor_engine serve [--port PORT] [--log-level LEVEL]"""

import argparse
import asyncio
import logging
import sys


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

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

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
