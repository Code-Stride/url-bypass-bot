"""
server.py — single entrypoint for Railway.

Serves the web UI + JSON API, and runs the Telegram bot in the same process
(webhook mode when a public URL is available, polling otherwise).

    python server.py            # binds 0.0.0.0:$PORT (default 8080)

Env:
    BOT_TOKEN     Telegram token; omit to run web-only
    ENABLE_BOT=0  force web-only even when BOT_TOKEN is set
    PORT          injected by Railway
"""

from __future__ import annotations

import logging
import os

import uvicorn

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(
        "webapp:app",
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info"),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
