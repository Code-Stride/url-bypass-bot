"""
webapp.py — FastAPI app: web UI + JSON API + (optionally) the Telegram
webhook, all served from one port so a single Railway service runs both.

Routes
------
GET  /                      web UI (paste a link, get the destination)
POST /api/bypass            {"url": "...", "all": false} -> JSON result
GET  /api/bypass?url=...    same, convenience GET
GET  /healthz               health check for Railway
POST /telegram/<BOT_TOKEN>  Telegram webhook (registered on startup)
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from unshortener import pick_best, unshorten

logger = logging.getLogger("url-bypass-bot.web")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ENABLE_BOT = bool(BOT_TOKEN) and os.environ.get("ENABLE_BOT", "1") != "0"

_tg_app = None  # python-telegram-bot Application, when enabled


def _public_base_url() -> str | None:
    explicit = os.environ.get("WEBHOOK_URL", "").strip() or os.environ.get(
        "PUBLIC_URL", ""
    ).strip()
    if explicit:
        return explicit.rstrip("/")
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if domain:
        return f"https://{domain}"
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tg_app
    if ENABLE_BOT:
        try:
            from bot import build_app  # lazy: web-only mode needs no token

            base = _public_base_url()
            _tg_app = build_app()
            await _tg_app.initialize()
            await _tg_app.start()
            if base:
                url = f"{base}/telegram/{BOT_TOKEN}"
                await _tg_app.bot.set_webhook(
                    url=url, drop_pending_updates=True, allowed_updates=None
                )
                logger.info("Telegram webhook set -> %s", url)
            else:
                # No public URL (local dev): poll in the background instead.
                await _tg_app.updater.start_polling(drop_pending_updates=True)
                logger.info("No public URL found — Telegram running in polling mode")
        except Exception:  # noqa: BLE001
            # Never let a Telegram problem take the website down (Railway would
            # otherwise crash-loop the whole service).
            logger.exception("Telegram bot failed to start — continuing web-only")
            _tg_app = None
    else:
        logger.info("BOT_TOKEN not set — running web-only")

    try:
        yield
    finally:
        if _tg_app is not None:
            try:
                if _tg_app.updater and _tg_app.updater.running:
                    await _tg_app.updater.stop()
                await _tg_app.stop()
                await _tg_app.shutdown()
            except Exception:  # noqa: BLE001
                logger.exception("error during Telegram shutdown")


app = FastAPI(title="URL Bypass Bot", lifespan=lifespan)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
async def _resolve(url: str) -> dict:
    url = (url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url'.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    res = await asyncio.to_thread(unshorten, url)
    res["best"] = pick_best(res["results"]) if res["results"] else None
    return res


@app.get("/healthz")
async def healthz():
    return {"ok": True, "bot": ENABLE_BOT}


@app.get("/api/bypass")
async def api_bypass_get(url: str = ""):
    return JSONResponse(await _resolve(url))


@app.post("/api/bypass")
async def api_bypass_post(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    return JSONResponse(await _resolve(str(body.get("url", ""))))


@app.get("/api/trace")
async def api_trace(url: str = ""):
    """Diagnostic: show every hop of the chain (status, cookies, form fields)."""
    url = (url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url'.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    from trace_chain import trace

    return JSONResponse(await asyncio.to_thread(trace, url))


@app.get("/api/raw")
async def api_raw(url: str = "", q: str = "", ctx: int = 400, limit: int = 20):
    """
    Diagnostic: fetch a page and return snippets around a keyword (or the
    head of the HTML when no keyword is given).  Lets a failing site's JS be
    inspected from a host that can actually reach it.
    """
    url = (url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Missing 'url'.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    from httpclient import Client

    client = Client()
    resp = await asyncio.to_thread(client.get, url)
    if resp is None:
        raise HTTPException(status_code=502, detail="fetch failed")
    html = resp.text or ""

    snippets: list[str] = []
    if q:
        import re as _re

        for m in list(_re.finditer(_re.escape(q), html, _re.IGNORECASE))[:limit]:
            snippets.append(html[max(0, m.start() - ctx): m.end() + ctx])
    return JSONResponse({
        "url": resp.url,
        "status": resp.status_code,
        "backend": resp.backend,
        "length": len(html),
        "cookies": client.cookies,
        "snippets": snippets or ([html[:3000]] if not q else []),
    })


@app.post("/telegram/{token}")
async def telegram_webhook(token: str, request: Request):
    if not ENABLE_BOT or _tg_app is None or token != BOT_TOKEN:
        raise HTTPException(status_code=403, detail="forbidden")
    from telegram import Update

    data = await request.json()
    await _tg_app.process_update(Update.de_json(data, _tg_app.bot))
    return {"ok": True}


# --------------------------------------------------------------------------
# Web UI
# --------------------------------------------------------------------------
INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>URL Bypass — gplinks, liteshort & more</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; padding: 24px;
    background: radial-gradient(1200px 600px at 50% -10%, #1e293b, #020617 60%);
    font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    color: #e2e8f0;
  }
  .card {
    width: 100%; max-width: 640px; background: #0b1220;
    border: 1px solid #1e293b; border-radius: 18px; padding: 28px;
    box-shadow: 0 30px 80px rgba(0,0,0,.55);
  }
  h1 { margin: 0 0 6px; font-size: 22px; }
  p.sub { margin: 0 0 20px; color: #94a3b8; font-size: 14px; line-height: 1.5; }
  form { display: flex; gap: 10px; flex-wrap: wrap; }
  input[type=url], input[type=text] {
    flex: 1 1 320px; padding: 13px 14px; border-radius: 11px;
    border: 1px solid #26344b; background: #060b16; color: #e2e8f0;
    font-size: 15px; outline: none;
  }
  input:focus { border-color: #38bdf8; }
  button {
    padding: 13px 20px; border-radius: 11px; border: 0; cursor: pointer;
    background: #38bdf8; color: #052030; font-weight: 650; font-size: 15px;
  }
  button:disabled { opacity: .55; cursor: progress; }
  .row { display: flex; align-items: center; gap: 8px; margin-top: 12px;
         color: #94a3b8; font-size: 13px; }
  #out { margin-top: 20px; display: none; }
  .result {
    background: #060b16; border: 1px solid #1e293b; border-radius: 12px;
    padding: 14px; margin-bottom: 10px; word-break: break-all; font-size: 14px;
  }
  .result a { color: #7dd3fc; text-decoration: none; }
  .result a:hover { text-decoration: underline; }
  .label { display: block; font-size: 11px; letter-spacing: .09em;
           text-transform: uppercase; color: #64748b; margin-bottom: 6px; }
  .err { border-color: #7f1d1d; color: #fca5a5; }
  .copy { margin-left: 8px; font-size: 12px; padding: 5px 10px; border-radius: 7px;
          background: #1e293b; color: #cbd5e1; }
  .examples { margin-top: 18px; font-size: 12.5px; color: #64748b; line-height: 1.9; }
  .examples code { background: #0f172a; padding: 3px 7px; border-radius: 6px;
                   cursor: pointer; color: #94a3b8; }
  footer { margin-top: 22px; font-size: 12px; color: #475569; }
  footer a { color: #64748b; }
</style>
</head>
<body>
<div class="card">
  <h1>🔗 URL Bypass</h1>
  <p class="sub">
    Paste a shortened or ad-locked link — gplinks.co, liteshort.com,
    adrinolinks, adf.ly, linkvertise, bit.ly and friends — and get the real
    destination. Cloudflare challenges are handled server-side.
  </p>

  <form id="f">
    <input id="u" type="url" placeholder="https://gplinks.co/ZkVCbbry" required>
    <button id="go" type="submit">Bypass</button>
  </form>
  <label class="row"><input type="checkbox" id="all"> show every mirror</label>

  <div id="out"></div>

  <div class="examples">
    Try:
    <code class="ex">https://liteshort.com/al1t</code>
    <code class="ex">https://gplinks.co/ZkVCbbry</code>
  </div>

  <footer>
    API: <code>GET /api/bypass?url=…</code> ·
    Telegram bot available too.
  </footer>
</div>

<script>
const out = document.getElementById('out');
const inp = document.getElementById('u');

document.querySelectorAll('.ex').forEach(function (e) {
  e.onclick = function () { inp.value = e.textContent; };
});

function card(label, html, cls) {
  return '<div class="result ' + (cls || '') + '"><span class="label">' +
         label + '</span>' + html + '</div>';
}

function linkRow(u) {
  return '<a href="' + u + '" target="_blank" rel="noopener">' + u + '</a>' +
         '<button class="copy" type="button" onclick="navigator.clipboard.writeText(' +
         JSON.stringify(u) + ')">copy</button>';
}

document.getElementById('f').onsubmit = async function (ev) {
  ev.preventDefault();
  const btn = document.getElementById('go');
  const showAll = document.getElementById('all').checked;
  btn.disabled = true;
  out.style.display = 'block';
  out.innerHTML = card('status', 'Resolving… this can take a few seconds ' +
                                 'while the countdown is waited out.');
  try {
    const r = await fetch('/api/bypass', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: inp.value })
    });
    const d = await r.json();
    if (!d.ok) {
      out.innerHTML = card('failed', d.error || d.detail || 'Could not resolve.', 'err');
    } else if (showAll || d.results.length === 1) {
      out.innerHTML = d.results.map(function (u, i) {
        return card('link ' + (i + 1), linkRow(u));
      }).join('');
    } else {
      out.innerHTML = card('best of ' + d.results.length + ' mirrors', linkRow(d.best)) +
                      card('tip', 'Tick “show every mirror” to list them all.');
    }
  } catch (e) {
    out.innerHTML = card('error', String(e), 'err');
  } finally {
    btn.disabled = false;
  }
};
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(INDEX_HTML)
