"""
http.py — cheap, fast engine for links that do not need a browser.

Handles: plain 30x redirect chains (bit.ly, t.co, …), meta-refresh hops and
pages that embed the destination in plain HTML.  It deliberately gives up as
soon as it hits a real gate (countdown, ad steps, Cloudflare challenge) and
lets the browser engine take over, rather than guessing and being wrong.
"""

from __future__ import annotations

import html as _html
import re
from urllib.parse import urljoin

import requests

from app import config
from app.classify import host_of, is_error_url, is_shortener, pick_best, verdict
from app.models import Result

try:
    from curl_cffi import requests as curl_requests
except Exception:  # pragma: no cover - optional
    curl_requests = None

BASE_HEADERS = {
    "User-Agent": config.CHROME_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

_META_REFRESH_RE = re.compile(
    r"""<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]+content\s*=\s*"""
    r"""["']\s*\d+\s*;\s*url\s*=\s*([^"']+)["']""",
    re.IGNORECASE,
)
_JS_LOC_RE = re.compile(
    r"""(?:window\.)?location(?:\.href|\.replace|\.assign)?\s*(?:=|\()\s*"""
    r"""["']([^"']+)["']""",
    re.IGNORECASE,
)
_HREF_RE = re.compile(r"""<a\b[^>]+href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

# Signals that this page is a gate a browser must handle.
GATE_RE = re.compile(
    r"(?:id=[\"']go-link|links/go|ad_form_data|step_count|please\s*wait|"
    r"challenge-platform|turnstile|just a moment)",
    re.IGNORECASE,
)

MAX_HOPS = 10


class HttpEngine:
    name = "http"

    def _session(self):
        if curl_requests is not None:
            try:
                return curl_requests.Session(impersonate="chrome"), True
            except Exception:  # pragma: no cover
                pass
        return requests.Session(), False

    def resolve(self, url: str, result: Result) -> Result:
        """Follow the chain; return a destination only if we are confident."""
        sess, impersonating = self._session()
        origin_host = host_of(url)
        current = url

        for hop in range(MAX_HOPS):
            try:
                resp = sess.get(
                    current,
                    headers=BASE_HEADERS,
                    timeout=config.HTTP_TIMEOUT,
                    allow_redirects=False,
                )
            except Exception as exc:  # noqa: BLE001
                result.log("error", f"fetch failed: {type(exc).__name__}")
                return result.fail(f"fetch failed: {exc}")

            status = resp.status_code
            headers = {k.lower(): v for k, v in dict(resp.headers).items()}
            result.log("navigate", f"hop {hop + 1} -> HTTP {status}", current)

            # 30x
            loc = headers.get("location")
            if loc and 300 <= status < 400:
                nxt = urljoin(current, loc)
                if is_error_url(nxt):
                    return result.fail("shortener refused: " + nxt)
                ok, conf, why = verdict(nxt, origin_host)
                if ok and conf >= 0.75 and not is_shortener(nxt):
                    result.candidates.append(nxt)
                    result.log("redirect", f"destination via redirect ({why})", nxt)
                    return result.succeed(nxt, self.name, conf)
                current = nxt
                continue

            try:
                body = resp.text or ""
            except Exception:  # noqa: BLE001
                body = ""

            # A gate: stop and let the browser do it properly.
            if GATE_RE.search(body):
                result.log("wait", "gate detected — needs the browser engine")
                return result.fail("gate requires browser")

            m = _META_REFRESH_RE.search(body)
            if m:
                current = urljoin(current, _html.unescape(m.group(1)).strip())
                result.log("redirect", "meta refresh", current)
                continue

            js = [
                u.replace("\\/", "/")
                for u in _JS_LOC_RE.findall(body)
                if u.startswith("http")
            ]
            for cand in js:
                ok, conf, why = verdict(cand, origin_host)
                if ok and conf >= 0.8:
                    result.candidates.append(cand)
                    result.log("redirect", f"js redirect ({why})", cand)
                    return result.succeed(cand, self.name, conf)

            # Harvest anchors as candidates.
            found = []
            for raw in _HREF_RE.findall(body):
                cand = urljoin(current, _html.unescape(raw).strip())
                ok, conf, _why = verdict(cand, origin_host)
                if ok and conf >= 0.8:
                    found.append(cand)
            if found:
                result.candidates.extend(found)
                best, conf = pick_best(found, origin_host)
                if best and conf >= 0.85:
                    result.log("redirect", "high-confidence embedded link", best)
                    return result.succeed(best, self.name, conf)

            # Nothing actionable at this hop.
            break

        return result.fail("no destination found over plain HTTP")


ENGINE = HttpEngine()
