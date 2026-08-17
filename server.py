"""
Entrypoint: web UI + JSON API + Telegram bot on one port.

    python server.py        # binds 0.0.0.0:$PORT (default 8080)
"""

from __future__ import annotations

import logging
import os

import uvicorn

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
)


def main() -> None:
    from app import config

    uvicorn.run(
        "app.web.api:app",
        host="0.0.0.0",
        port=config.PORT,
        proxy_headers=True,
        forwarded_allow_ips="*",
        timeout_keep_alive=75,
    )


if __name__ == "__main__":
    main()
