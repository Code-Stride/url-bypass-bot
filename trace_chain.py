"""
trace_chain.py — diagnostic tracer for shortener chains.

Walks a link hop by hop with the Cloudflare-capable client and reports what
was seen at every step: status, Location, cookies planted, whether the page
looks like AdLinkFly, which form fields were found, and any candidate
destinations embedded in the HTML.

Used by `GET /api/trace?url=…` and by `python bypass_cli.py --trace <url>`
so a failing real-world link can be diagnosed from a host that can actually
reach the site.
"""

from __future__ import annotations

import html as _html
import re
from urllib.parse import parse_qs, urljoin, urlparse

import adlinkfly
from httpclient import Client, is_cloudflare_challenge

MAX_HOPS = 12

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_REFRESH_RE = re.compile(
    r"""<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]+content\s*=\s*["']\s*\d+\s*;\s*url\s*=\s*([^"']+)["']""",
    re.IGNORECASE,
)
_JS_LOC_RE = re.compile(
    r"""(?:window\.)?location(?:\.href|\.replace|\.assign)?\s*(?:=|\()\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
# Any absolute URL that is not an asset — useful to eyeball the real target.
_URL_RE = re.compile(r"""https?://[^\s"'<>\\)]+""")
_ASSET_RE = re.compile(
    r"\.(js|css|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|otf|eot|map)(\?|$)", re.I
)


def _title(html: str) -> str:
    m = _TITLE_RE.search(html or "")
    return _html.unescape(m.group(1)).strip()[:120] if m else ""


def _interesting_urls(html: str, base: str, own_host: str, limit: int = 25) -> list[str]:
    out, seen = [], set()
    for u in _URL_RE.findall(html or ""):
        u = u.rstrip(".,);")
        if _ASSET_RE.search(u):
            continue
        h = adlinkfly.host_of(u)
        if not h or h == own_host:
            continue
        if any(
            k in h
            for k in (
                "google", "gstatic", "facebook", "cloudflare", "jquery",
                "bootstrap", "fontawesome", "w3.org", "schema.org", "jsdelivr",
                "youtube", "twitter", "adsterra", "propeller", "doubleclick",
            )
        ):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def trace(url: str, max_hops: int = MAX_HOPS) -> dict:
    """Follow the chain manually and describe each hop."""
    client = Client()
    hops: list[dict] = []
    current = url
    origin_host = adlinkfly.host_of(url)

    for i in range(max_hops):
        resp = client.get(current, allow_redirects=False)
        if resp is None:
            hops.append({"step": i + 1, "url": current, "error": "fetch failed"})
            break

        headers = {k.lower(): v for k, v in (resp.headers or {}).items()}
        html = resp.text or ""
        hop = {
            "step": i + 1,
            "url": current,
            "status": resp.status_code,
            "backend": resp.backend,
            "final_url": resp.url,
            "server": headers.get("server", ""),
            "content_type": headers.get("content-type", ""),
            "length": len(html),
            "title": _title(html),
            "cloudflare_challenge": is_cloudflare_challenge(resp),
            "cookies": sorted(client.cookies.keys()),
            "query": {k: v[0] for k, v in parse_qs(urlparse(current).query).items() if v},
            "looks_like_adlinkfly": adlinkfly.looks_like_adlinkfly(html),
        }

        fields = adlinkfly.extract_go_link_fields(html)
        if fields:
            hop["go_link_fields"] = {
                k: (v[:60] + "…" if len(v) > 60 else v) for k, v in fields.items()
            }

        hop["candidates"] = _interesting_urls(
            html, resp.url, adlinkfly.host_of(resp.url) or origin_host
        )

        nxt = headers.get("location")
        if nxt:
            hop["redirect"] = urljoin(current, nxt)
        else:
            m = _META_REFRESH_RE.search(html)
            if m:
                hop["meta_refresh"] = urljoin(resp.url, _html.unescape(m.group(1)).strip())
            js = [
                u.replace("\\/", "/")
                for u in _JS_LOC_RE.findall(html)
                if u.startswith("http")
            ]
            if js:
                hop["js_redirect"] = js[:5]

        hops.append(hop)

        follow = hop.get("redirect") or hop.get("meta_refresh")
        if not follow:
            break
        if follow == current:
            break
        current = follow

    return {
        "input": url,
        "hops": hops,
        "cookies_final": client.cookies,
        "bypass_result": _safe_bypass(url),
    }


def _safe_bypass(url: str) -> str | None:
    try:
        return adlinkfly.bypass(url)
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"
