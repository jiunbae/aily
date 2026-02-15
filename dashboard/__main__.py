"""Entry point: python -m dashboard"""

import asyncio

from aiohttp import web

from dashboard.app import create_app


def main() -> None:
    app = asyncio.run(create_app())
    web.run_app(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
