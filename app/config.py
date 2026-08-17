"""Configuration, all overridable by environment variables."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


# --- Telegram / web -------------------------------------------------------
BOT_TOKEN = (os.environ.get("BOT_TOKEN", "") or "").strip()
ENABLE_BOT = _bool("ENABLE_BOT", True) and bool(BOT_TOKEN)
PORT = _int("PORT", 8080)
PUBLIC_URL = (
    (os.environ.get("WEBHOOK_URL", "") or os.environ.get("PUBLIC_URL", "")).strip()
    or (
        f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}"
        if os.environ.get("RAILWAY_PUBLIC_DOMAIN")
        else ""
    )
)

# --- Engine behaviour -----------------------------------------------------
# Total budget for one link. Shorteners impose 15-60s of countdowns, so this
# has to be generous; the API/bot report progress meanwhile.
RESOLVE_TIMEOUT = _int("RESOLVE_TIMEOUT", 180)
HTTP_TIMEOUT = _int("HTTP_TIMEOUT", 25)

# Browser engine (the accurate path).
USE_BROWSER = _bool("USE_BROWSER", True)
BROWSER_HEADLESS = _bool("BROWSER_HEADLESS", True)
# Max seconds the browser will spend on a single link.
BROWSER_TIMEOUT = _int("BROWSER_TIMEOUT", 150)
# How many browser resolutions may run at once (each costs ~250MB RAM).
BROWSER_CONCURRENCY = _int("BROWSER_CONCURRENCY", 2)

# Optional external solver for interactive Cloudflare Turnstile.
FLARESOLVERR_URL = (os.environ.get("FLARESOLVERR_URL", "") or "").strip()

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
