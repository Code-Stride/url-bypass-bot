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


@app.get("/version")
async def version():
    """Which commit is actually running — makes deploy state verifiable."""
    import os as _os
    import subprocess as _sp

    rev = _os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")
    if not rev:
        try:
            rev = _sp.check_output(
                ["git", "rev-parse", "--short", "HEAD"], text=True, timeout=5
            ).strip()
        except Exception:  # noqa: BLE001
            rev = "unknown"
    return {
        "commit": rev[:12],
        "browser_headless": config.BROWSER_HEADLESS,
        "stop_threshold": 0.85,
    }


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


@app.get("/api/inspect")
async def inspect(url: str = "", wait: int = 12):
    """
    Diagnostic: open a link in the browser, wait, and report what is on the
    page — visible buttons/links, forms, frames and the body text.  Used to
    tune the click heuristics against a site that is actually reachable.
    """
    url = _clean(url)
    from app.engines.browser import ENGINE as BROWSER

    if not await BROWSER.start():
        raise HTTPException(status_code=503, detail=f"browser: {BROWSER.error}")

    context = await BROWSER._new_context()  # noqa: SLF001 - diagnostic only
    await BROWSER._harden(context)  # noqa: SLF001
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(max(0, min(wait, 60)) * 1000)
        info = await page.evaluate(
            """() => {
              const vis = e => {
                const r = e.getBoundingClientRect();
                const s = getComputedStyle(e);
                return r.width > 0 && r.height > 0 &&
                       s.visibility !== 'hidden' && s.display !== 'none';
              };
              const pick = sel => [...document.querySelectorAll(sel)]
                .slice(0, 60).map(e => ({
                  tag: e.tagName.toLowerCase(),
                  id: e.id || '',
                  cls: (e.className && e.className.toString
                        ? e.className.toString() : '').slice(0, 80),
                  text: (e.innerText || e.value || '').trim().slice(0, 60),
                  href: e.href || '',
                  visible: vis(e),
                }));
              return {
                title: document.title,
                url: location.href,
                buttons: pick('button, input[type=submit], a'),
                forms: [...document.forms].slice(0, 10).map(f => ({
                  id: f.id, action: f.action, method: f.method,
                  fields: [...f.elements].slice(0, 15)
                    .map(i => i.name).filter(Boolean),
                })),
                frames: [...document.querySelectorAll('iframe')]
                  .slice(0, 10).map(f => f.src),
                text: (document.body.innerText || '').slice(0, 1500),
              };
            }"""
        )
        info["frame_urls"] = [f.url for f in page.frames][:10]
        return JSONResponse(info)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)[:300])
    finally:
        await context.close()


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
