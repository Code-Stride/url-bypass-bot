"""FastAPI app: web UI + JSON API + Telegram webhook, all on one port."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import config
from app.resolver import resolve_with_timeout
from app.web.ui import INDEX_HTML

logger = logging.getLogger("bypass.web")

_tg = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tg

    # Warm the browser up front so the first user request is not slow.
    if config.USE_BROWSER:
        from app.engines.browser import ENGINE as BROWSER

        if await BROWSER.start():
            logger.info("browser engine ready")
        else:
            logger.warning("browser engine unavailable: %s", BROWSER.error)

    if config.ENABLE_BOT:
        try:
            from app.bot import build_app

            _tg = build_app()
            await _tg.initialize()
            await _tg.start()
            if config.PUBLIC_URL:
                hook = f"{config.PUBLIC_URL.rstrip('/')}/telegram/{config.BOT_TOKEN}"
                await _tg.bot.set_webhook(url=hook, drop_pending_updates=True)
                logger.info("telegram webhook -> %s", hook)
            else:
                await _tg.updater.start_polling(drop_pending_updates=True)
                logger.info("telegram polling (no public url)")
        except Exception:  # noqa: BLE001
            logger.exception("telegram failed to start — continuing web-only")
            _tg = None
    else:
        logger.info("telegram disabled (no BOT_TOKEN)")

    try:
        yield
    finally:
        if _tg is not None:
            try:
                if _tg.updater and _tg.updater.running:
                    await _tg.updater.stop()
                await _tg.stop()
                await _tg.shutdown()
            except Exception:  # noqa: BLE001
                pass
        if config.USE_BROWSER:
            from app.engines.browser import ENGINE as BROWSER

            await BROWSER.stop()


app = FastAPI(title="URL Bypass", lifespan=lifespan)


def _clean(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url'.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


@app.get("/healthz")
async def healthz():
    browser_ok = False
    if config.USE_BROWSER:
        from app.engines.browser import ENGINE as BROWSER

        browser_ok = BROWSER.available
    return {"ok": True, "bot": config.ENABLE_BOT, "browser": browser_ok}


@app.get("/api/bypass")
async def bypass_get(url: str = "", verbose: bool = False):
    res = await resolve_with_timeout(_clean(url))
    return JSONResponse(res.to_dict(verbose=verbose))


@app.post("/api/bypass")
async def bypass_post(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    res = await resolve_with_timeout(_clean(str(body.get("url", ""))))
    return JSONResponse(res.to_dict(verbose=bool(body.get("verbose"))))


@app.post("/telegram/{token}")
async def telegram_webhook(token: str, request: Request):
    if _tg is None or token != config.BOT_TOKEN:
        raise HTTPException(status_code=403, detail="forbidden")
    from telegram import Update

    await _tg.process_update(Update.de_json(await request.json(), _tg.bot))
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(INDEX_HTML)
