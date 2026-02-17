"""Entry point: python -m dashboard"""

import asyncio

from aiohttp import web

from dashboard.app import create_app
from dashboard.logging_config import setup_logging


def main() -> None:
    setup_logging()
    app = asyncio.run(create_app())
    web.run_app(app, host="0.0.0.0", port=8080, shutdown_timeout=10.0)


if __name__ == "__main__":
    main()
