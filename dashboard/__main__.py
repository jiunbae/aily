"""Entry point: python -m dashboard"""

import asyncio
import os

from aiohttp import web

from dashboard.app import create_app
from dashboard.logging_config import setup_logging


def main() -> None:
    setup_logging()
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8080"))
    app = asyncio.run(create_app())

    config = app.get("config")
    if config and getattr(config, '_token_auto_generated', False):
        print(f"\n{'='*60}")
        print(f"  Dashboard token (auto-generated): {config.dashboard_token}")
        print(f"  Set DASHBOARD_TOKEN env var for persistent auth")
        print(f"{'='*60}\n")

    web.run_app(app, host=host, port=port, shutdown_timeout=10.0)


if __name__ == "__main__":
    main()
