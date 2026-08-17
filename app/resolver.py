"""
resolver.py — orchestrates the engines and guarantees the answer is sane.

Order:
  1. HTTP engine  — instant for plain redirect chains; bails out at any gate.
  2. Browser      — performs the real flow (countdowns, ad steps, Cloudflare).

Whatever an engine returns is re-checked with classify.verdict(), and a
destination that is itself a shortener is resolved again (bounded), so a
gplinks -> another-shortener -> file chain ends on the file.
"""

from __future__ import annotations

import asyncio
import time

from app import config
from app.classify import host_of, is_shortener, verdict
from app.engines import http as http_engine
from app.models import Result

MAX_CHAIN = 4


async def resolve(url: str, use_browser: bool | None = None) -> Result:
    """Resolve one link to its real destination."""
    started = time.monotonic()
    url = (url or "").strip()
    result = Result(input=url)

    if not url:
        return result.fail("empty url")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
        result.input = url

    want_browser = config.USE_BROWSER if use_browser is None else use_browser
    chain_seen: set[str] = set()
    current = url
    final: Result | None = None

    for depth in range(MAX_CHAIN):
        if current in chain_seen:
            break
        chain_seen.add(current)

        hop = Result(input=current)
        hop.steps = result.steps  # share the log so the user sees one story

        # 1. cheap path
        await asyncio.to_thread(http_engine.ENGINE.resolve, current, hop)

        # 2. real browser
        if not hop.ok and want_browser:
            from app.engines.browser import ENGINE as BROWSER

            hop = Result(input=current)
            hop.steps = result.steps
            hop.candidates = result.candidates
            await BROWSER.resolve(current, hop)

        result.candidates = list(dict.fromkeys(result.candidates + hop.candidates))

        if not hop.ok or not hop.url:
            final = hop
            break

        # Verify before accepting.
        ok, conf, why = verdict(hop.url, host_of(current))
        if not ok:
            hop.log("error", f"rejected answer ({why})", hop.url)
            final = hop.fail(f"engine returned an unusable link ({why})")
            break

        # Another shortener? keep going.
        if is_shortener(hop.url) and depth + 1 < MAX_CHAIN:
            hop.log("redirect", "destination is itself a shortener — resolving on",
                    hop.url)
            current = hop.url
            final = hop
            continue

        hop.confidence = min(hop.confidence, conf) if conf else hop.confidence
        final = hop
        break

    out = final or result
    out.input = url
    out.candidates = result.candidates
    out.steps = result.steps
    out.elapsed = time.monotonic() - started

    if out.ok and out.url:
        ok, conf, why = verdict(out.url, host_of(url))
        if not ok:
            out.ok = False
            out.error = f"unusable result ({why})"
            out.url = None
    return out


async def resolve_with_timeout(url: str, use_browser: bool | None = None) -> Result:
    try:
        return await asyncio.wait_for(
            resolve(url, use_browser), timeout=config.RESOLVE_TIMEOUT
        )
    except asyncio.TimeoutError:
        r = Result(input=url)
        r.elapsed = config.RESOLVE_TIMEOUT
        return r.fail(f"timed out after {config.RESOLVE_TIMEOUT}s")
