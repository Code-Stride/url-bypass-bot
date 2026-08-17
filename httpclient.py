"""
httpclient.py — a single HTTP layer that can get through Cloudflare.

Why this exists
---------------
Shorteners such as gplinks.co and liteshort.com sit behind Cloudflare.  Plain
`requests` is rejected during the TLS handshake / with a "Just a moment…"
interstitial, because Cloudflare fingerprints the TLS ClientHello (JA3/JA4),
the HTTP/2 settings and the header order — not just the User-Agent.

The client escalates through backends, cheapest first:

  1. curl_cffi  — impersonates a real Chrome TLS + HTTP/2 fingerprint.  This
     alone clears Cloudflare's "under attack" JS challenge for most link
     shorteners, because their challenge is the non-interactive (managed /
     JS) one, not an interactive captcha.
  2. cloudscraper — solves the legacy IUAM JS challenge.
  3. FlareSolverr — a real headless browser you run yourself
     (`docker run -p 8191:8191 ghcr.io/flaresolverr/flaresolverr`).  Set
     FLARESOLVERR_URL=http://localhost:8191/v1 and it will be used for pages
     the first two backends cannot open (Turnstile / "Cloudflare v3" managed
     challenges that really need JS execution).

Cookies (including cf_clearance) are shared across backends inside one
Client instance, so once a challenge is solved the rest of the flow — the
POST to /links/go, etc. — reuses the clearance cookie.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import requests

try:  # optional, strongly recommended
    from curl_cffi import requests as curl_requests
except Exception:  # pragma: no cover - optional dependency
    curl_requests = None

try:  # optional
    import cloudscraper
except Exception:  # pragma: no cover - optional dependency
    cloudscraper = None


TIMEOUT = int(os.environ.get("BYPASS_TIMEOUT", "25"))
IMPERSONATE = os.environ.get("BYPASS_IMPERSONATE", "chrome")
FLARESOLVERR_URL = os.environ.get("FLARESOLVERR_URL", "").strip()

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": CHROME_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# Markers of a Cloudflare interstitial (v1 IUAM, v2 JS, v3 managed/Turnstile).
_CF_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-platform",
    "cdn-cgi/challenge-platform",
    "turnstile",
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "ray id",
)


@dataclass
class Response:
    """Backend-agnostic response object."""

    url: str
    status_code: int
    headers: dict
    text: str
    cookies: dict = field(default_factory=dict)
    backend: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def json(self) -> Any:
        import json as _json

        return _json.loads(self.text)


def is_cloudflare_challenge(resp: Response | None) -> bool:
    """True if the response is a Cloudflare interstitial rather than content."""
    if resp is None:
        return True
    hdrs = {k.lower(): str(v).lower() for k, v in (resp.headers or {}).items()}
    if "cf-mitigated" in hdrs:  # Cloudflare says: challenged
        return True
    if resp.status_code in (403, 429, 503):
        server = hdrs.get("server", "")
        if "cloudflare" in server:
            return True
    body = (resp.text or "")[:20000].lower()
    if resp.status_code in (403, 429, 503) and any(m in body for m in _CF_MARKERS):
        return True
    # A 200 that is still the challenge page (v3 serves 200 + JS challenge).
    if len(body) < 40000 and (
        "cdn-cgi/challenge-platform" in body or "just a moment" in body
    ):
        return True
    return False


class Client:
    """One browsing session; escalates backends until Cloudflare lets us in."""

    def __init__(self, impersonate: str = IMPERSONATE, timeout: int = TIMEOUT):
        self.timeout = timeout
        self.impersonate = impersonate
        self.cookies: dict[str, str] = {}
        self._plain = requests.Session()
        self._curl = None
        self._scraper = None
        self._flare_session: str | None = None
        if curl_requests is not None:
            try:
                self._curl = curl_requests.Session(impersonate=impersonate)
            except Exception:  # pragma: no cover
                self._curl = None

    # -- cookie plumbing ---------------------------------------------------
    def _absorb(self, jar) -> None:
        try:
            for k, v in dict(jar).items():
                if v:
                    self.cookies[k] = v
        except Exception:  # pragma: no cover
            pass

    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    # -- low-level backends ------------------------------------------------
    def _via_curl(self, method, url, **kw) -> Response | None:
        if self._curl is None:
            return None
        try:
            r = self._curl.request(
                method, url,
                timeout=self.timeout,
                impersonate=self.impersonate,
                **kw,
            )
        except Exception:
            return None
        self._absorb(r.cookies)
        return Response(str(r.url), r.status_code, dict(r.headers), r.text,
                        dict(self.cookies), "curl_cffi")

    def _via_scraper(self, method, url, **kw) -> Response | None:
        if cloudscraper is None:
            return None
        if self._scraper is None:
            try:
                self._scraper = cloudscraper.create_scraper(
                    browser={"browser": "chrome", "platform": "windows", "mobile": False},
                    delay=5,
                )
            except Exception:  # pragma: no cover
                return None
        try:
            self._scraper.cookies.update(self.cookies)
            r = self._scraper.request(method, url, timeout=self.timeout, **kw)
        except Exception:
            return None
        self._absorb(r.cookies)
        return Response(str(r.url), r.status_code, dict(r.headers), r.text,
                        dict(self.cookies), "cloudscraper")

    def _via_plain(self, method, url, **kw) -> Response | None:
        try:
            self._plain.cookies.update(self.cookies)
            r = self._plain.request(method, url, timeout=self.timeout, **kw)
        except Exception:
            return None
        self._absorb(r.cookies)
        return Response(str(r.url), r.status_code, dict(r.headers), r.text,
                        dict(self.cookies), "requests")

    def _via_flaresolverr(self, url: str) -> Response | None:
        """Real headless browser — the only thing that clears Turnstile."""
        if not FLARESOLVERR_URL:
            return None
        try:
            if self._flare_session is None:
                s = requests.post(
                    FLARESOLVERR_URL,
                    json={"cmd": "sessions.create"},
                    timeout=90,
                ).json()
                self._flare_session = s.get("session")
            payload = {
                "cmd": "request.get",
                "url": url,
                "maxTimeout": 90000,
            }
            if self._flare_session:
                payload["session"] = self._flare_session
            data = requests.post(FLARESOLVERR_URL, json=payload, timeout=120).json()
        except Exception:
            return None
        sol = (data or {}).get("solution") or {}
        if not sol:
            return None
        for c in sol.get("cookies", []):
            if c.get("name"):
                self.cookies[c["name"]] = c.get("value", "")
        return Response(
            sol.get("url", url),
            int(sol.get("status", 0) or 0),
            {k.lower(): v for k, v in (sol.get("headers") or {}).items()},
            sol.get("response", "") or "",
            dict(self.cookies),
            "flaresolverr",
        )

    # -- public API --------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict | None = None,
        data: Any = None,
        json: Any = None,
        allow_redirects: bool = True,
        referer: str | None = None,
    ) -> Response | None:
        hdrs = dict(BASE_HEADERS)
        if referer:
            hdrs["Referer"] = referer
            hdrs["Sec-Fetch-Site"] = "same-origin"
        if headers:
            hdrs.update(headers)
        if self.cookies:
            hdrs["Cookie"] = self.cookie_header()

        kw: dict[str, Any] = {
            "headers": hdrs,
            "allow_redirects": allow_redirects,
        }
        if data is not None:
            kw["data"] = data
        if json is not None:
            kw["json"] = json

        had_clearance = "cf_clearance" in self.cookies

        resp = self._via_curl(method, url, **kw)
        if resp is not None and not is_cloudflare_challenge(resp):
            return self._reload_after_clearance(
                method, url, kw, resp, had_clearance
            )

        alt = self._via_scraper(method, url, **kw)
        if alt is not None and not is_cloudflare_challenge(alt):
            return alt

        # GET-only escape hatch: a real browser via FlareSolverr.
        if method.upper() == "GET":
            flare = self._via_flaresolverr(url)
            if flare is not None and not is_cloudflare_challenge(flare):
                return flare
            if flare is not None and resp is None and alt is None:
                return flare

        if resp is None and alt is None:
            plain = self._via_plain(method, url, **kw)
            if plain is not None:
                return plain
        return resp or alt

    def _reload_after_clearance(
        self, method, url, kw, resp: Response, had_clearance: bool
    ) -> Response:
        """
        Cloudflare often answers the first request with a clearance-setting
        response whose body is not the page we asked for.  When a fresh
        cf_clearance appears, replay the request once with it.
        """
        if had_clearance or "cf_clearance" not in self.cookies:
            return resp
        kw = dict(kw)
        hdrs = dict(kw.get("headers") or {})
        hdrs["Cookie"] = self.cookie_header()
        kw["headers"] = hdrs
        retry = self._via_curl(method, url, **kw)
        if retry is not None and not is_cloudflare_challenge(retry):
            return retry
        return resp

    def get(self, url: str, **kw) -> Response | None:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> Response | None:
        return self.request("POST", url, **kw)


_SLEEP_CAP = int(os.environ.get("BYPASS_MAX_WAIT", "12"))


def polite_sleep(seconds: float) -> None:
    """Wait out a shortener countdown, capped so the bot stays responsive."""
    time.sleep(max(0.0, min(float(seconds), _SLEEP_CAP)))


_COUNTDOWN_RE = re.compile(
    r"(?:var\s+(?:count|seconds|timer|_?time)\s*=\s*|data-timer\s*=\s*[\"']?)(\d{1,3})",
    re.IGNORECASE,
)


def detect_countdown(html: str, default: float = 8.0) -> float:
    m = _COUNTDOWN_RE.search(html or "")
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return default
