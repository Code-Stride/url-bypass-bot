"""
unshortener.py — Core URL "bypass" / unshortening engine.

How it works (layered strategy):
  1. Follow HTTP redirects to reach the final landing page.
  2. Handle <meta http-equiv="refresh"> redirects.
  3. For link-protection / "money-earning" shorteners (mobilejsr.com,
     linkszilla.top, adf.ly-style, etc.) the REAL destination link is almost
     always embedded directly in the page HTML (an <a href>, a hidden input,
     a JS variable, a data-* attribute).  We extract every candidate URL.
  4. Filter out noise (ad networks, trackers, the shortener's own domain).
  5. Recursively resolve any candidate that is itself a known shortener,
     up to a depth limit.  Return all genuine destinations (mirrors included).
"""

from __future__ import annotations

import html as _html
import re
from urllib.parse import urljoin, urlparse

import requests

TIMEOUT = 15
MAX_DEPTH = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---------------------------------------------------------------------------
# Known shortener / link-protection domains.  A candidate URL whose domain is
# in this set gets resolved recursively (it is NOT the final destination).
# ---------------------------------------------------------------------------
KNOWN_SHORTENERS = {
    # classic shorteners
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "is.gd", "v.gd", "ow.ly",
    "buff.ly", "cutt.ly", "rb.gy", "rebrand.ly", "shorturl.at", "tiny.cc",
    "shrtco.de", "s.id", "surl.li", "t.ly", "soo.gd", "gg.gg", "clck.ru",
    "vurl.com", "ouo.press", "da.gd", "x.co", "9qr.de", "qps.ru", "chilp.it",
    "cutt.us", "git.io", "hyphen.com", "kutt.it", "mzl.la", "amzn.to",
    "mcaf.ee", "lnkd.in", "okt.to", "pear.do", "psce.pw", "qr.ae", "rotf.lol",
    "shortcm.li", "snip.ly", "t2m.io", "trimurl.co", "u.to", "yourls.org",
    # ad / link-protection ("money") shorteners
    "adf.ly", "adfoc.us", "bc.vc", "bcvc.live", "ouo.io", "shorte.st",
    "shink.me", "shrinkme.io", "shrinkearn.com", "linkvertise.com",
    "link-to.net", "link-center.net", "up-to-down.net", "linkszilla.top",
    "mobilejsr.com", "link.tl", "exe.io", "exey.io", "stfly.me", "za.gl",
    "aylink.co", "ayw.top", "boost.ink", "clk.sh", "cuty.io", "dlaf.info",
    "fc.lc", "gplinks.co", "gplink.co", "krownlinks.me", "laymro.com",
    "link1s.com", "mboost.me", "meoqw.com", "moiity.com", "mytc.pw",
    "newsurl.xyz", "pihe.in", "pnd.tl", "rekonise.com", "short-jambo.com",
    "sub2unlock.com", "sub2unlock.net", "sub2get.com", "tekcrypt.in",
    "thinfi.com", "try2link.com", "urlsopen.com", "vivads.net", "xpshort.com",
    "rswebsols.com", "adshort.co", "adshort.im", "link4m.co", "lnkload.com",
    "sflist.com", "heylink.me", "safevideolink.com", "cyberpandit.in",
    "techymozo.com", "bindaaslinks.com", "indiurl.com", "shorte.be",
    "tutorialslink.com", "linkrex.net", "earnvisits.com", "clicksfly.com",
    "smoner.com", "openget.net", "themefiles.net", "filelink.org",
    "cpmlink.net", "skmurl.com", "clkmein.com", "cobrabirla.com",
}

# Domains that are never the real destination (ad networks, trackers, cdn).
NOISE_DOMAINS = {
    "google.com", "google.co.in", "googleapis.com", "gstatic.com",
    "googletagmanager.com", "googlesyndication.com", "doubleclick.net",
    "google-analytics.com", "googleadservices.com", "googleusercontent.com",
    "facebook.com", "fbcdn.net", "facebook.net", "twitter.com", "x.com",
    "twimg.com", "instagram.com", "youtube.com", "youtube-nocookie.com",
    "ytimg.com", "tiktok.com", "linkedin.com", "reddit.com", "pinterest.com",
    "whatsapp.com", "telegram.org", "t.me", "vk.com", "discord.com",
    "cloudflare.com", "cloudflareinsights.com", "w3.org", "schema.org",
    "jquery.com", "gravatar.com", "recaptcha.net", "hcaptcha.com",
    "addthis.com", "sharethis.com", "disqus.com", "onesignal.com",
    "pushalert.co", "pushowl.com", "profitablecpmratenetwork.com",
    "propellerads.com", "popads.net", "popcash.net", "adsterra.com",
    "exoclick.com", "juicyads.com", "adf.ly.cdn", "cdn.jsdelivr.net",
    "unpkg.com", "bootstrapcdn.com", "fontawesome.com", "favicon.cc",
    "waust.at", "driverhugoverblown.com", "dmus.in", "revenuehits.com",
    "llvpn.com", "adl-media.com", "amung.us", "statcounter.com",
    "histats.com", "hitcounter.com", "clicky.com", "zopim.com",
    "tawk.to", "intercom.io", "crisp.chat", "livechatinc.com",
    # anti-adblock / "please disable adblocker" landing pages — never a
    # real destination, they are navigation/help links injected by trackers.
    "antiblock.org", "adblockplus.org", "adblock.com", "getadblock.com",
    "ublock.org", "ublockorigin.com", "ghostery.com", "adguard.com",
    "nothanks.com", "blockadblock.com", "fuckadblock.site",
}

_LINK_RE = re.compile(
    r"""(?:href|src|action|data-url|data-link|data-href|data-target-url|content)\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)
_META_REFRESH_RE = re.compile(
    r"""<meta[^>]+http-equiv\s*=\s*["']?refresh["']?[^>]+content\s*=\s*["']\d+\s*;\s*url\s*=\s*([^"']+)["']""",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_URL_RE = re.compile(
    r"""["']((?:https?://)[^"'\s<>]{4,})["']""",
    re.IGNORECASE,
)

# --- Link-protection "unlock" form handling --------------------------------
# Many link-protection sites (mobilejsr.com and friends) hide the real links
# behind a <form method="post"> that must be submitted together with a hidden
# CSRF token.  The token is tied to the PHP session cookie, so we must GET the
# page first (which plants the cookie) and then POST the form back.
_FORM_BLOCK_RE = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.IGNORECASE | re.DOTALL)
_HIDDEN_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_NAME_RE = re.compile(r"""name\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_VALUE_RE = re.compile(r"""value\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_ACTION_RE = re.compile(r"""action\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_CSRF_NAMES = {
    "_csrf", "csrf", "csrf_token", "csrftoken", "csrfToken", "token",
    "_token", "__requestverificationtoken", "authenticity_token",
}

_session = requests.Session()


def _get(url: str) -> requests.Response | None:
    """Fetch a URL following redirects, with a browser-like UA."""
    try:
        resp = _session.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        return resp
    except requests.RequestException:
        return None


def _host(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    if h.startswith("www."):
        h = h[3:]
    return h


# Static assets are never the destination a user is looking for.
_ASSET_EXT_RE = re.compile(
    r"\.(js|css|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|otf|eot|mp3|mp4|"
    r"webm|avi|mov|zip|rar|7z|gz|tar|pdf|map|json|xml|txt)$",
    re.IGNORECASE,
)


def _normalize(url: str) -> str:
    """Unescape HTML entities and make a relative URL absolute-ish."""
    url = _html.unescape(url).strip()
    url = url.replace("&amp;", "&")
    return url


def _is_shortener(url: str) -> bool:
    return _host(url) in KNOWN_SHORTENERS


def _is_noise(url: str) -> bool:
    h = _host(url)
    if not h:
        return True
    for d in NOISE_DOMAINS:
        if h == d or h.endswith("." + d):
            return True
    return False


def _try_csrf_unlock(final_url: str, html: str) -> str | None:
    """
    If the page is a link-protection "unlock" page, find its POST form and
    submit it (with the hidden CSRF token + session cookie) to reveal the
    real links.  Returns the unlocked HTML on success, else None.
    """
    for m in _FORM_BLOCK_RE.finditer(html):
        open_tag = m.group(1)
        inner = m.group(2)
        if "post" not in open_tag.lower():
            continue

        am = _ACTION_RE.search(open_tag)
        action = urljoin(final_url, _html.unescape(am.group(1))) if am else final_url

        data: dict[str, str] = {}
        has_csrf = False
        for inp in _HIDDEN_RE.findall(inner):
            nm = _NAME_RE.search(inp)
            if not nm:
                continue
            name = _html.unescape(nm.group(1))
            vm = _VALUE_RE.search(inp)
            value = _html.unescape(vm.group(1)) if vm else ""
            data[name] = value
            if name.lower() in _CSRF_NAMES:
                has_csrf = True

        if not has_csrf:
            continue

        try:
            resp = _session.post(
                action,
                data=data,
                headers={**HEADERS, "Referer": final_url},
                timeout=TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException:
            continue
        if resp.ok and resp.text:
            return resp.text
    return None


def _extract_candidates(html: str, base_url: str, own_host: str) -> list[str]:
    """Pull every plausible destination URL out of the page."""
    found: list[str] = []

    # 1. tag attributes (href / src / data-url / action / ...)
    for raw in _LINK_RE.findall(html):
        url = _normalize(raw)
        if url.startswith("//"):
            url = "https:" + url
        url = urljoin(base_url, url)
        found.append(url)

    # 2. raw http(s) URLs embedded anywhere (scripts, JSON, JS variables)
    for raw in _SCRIPT_URL_RE.findall(html):
        url = _normalize(raw)
        found.append(urljoin(base_url, url))

    out: list[str] = []
    seen: set[str] = set()
    for url in found:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            continue
        host = (p.hostname or "").lower()
        if not host:
            continue
        # drop static assets and known noise/ad domains
        if _ASSET_EXT_RE.search(p.path):
            continue
        host_n = _host(url)
        # drop the shortener's own pages and known noise/ad domains
        if host_n == own_host or _is_noise(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def resolve(url: str, depth: int = 0, seen: set[str] | None = None) -> list[str]:
    """
    Resolve a (possibly shortened / protected) URL to its real destination(s).

    Returns a list of destination URLs (mirrors included), empty on failure.
    """
    seen = seen if seen is not None else set()
    if depth > MAX_DEPTH:
        return []
    if url in seen:
        return []
    seen.add(url)

    resp = _get(url)
    if resp is None:
        return []

    final_url = resp.url
    content_type = resp.headers.get("Content-Type", "").lower()

    # Not an HTML page -> this IS the destination (file, image, api, ...).
    if content_type and "html" not in content_type:
        return [final_url]

    html = resp.text or ""
    own_host = _host(url)

    # meta-refresh redirect
    m = _META_REFRESH_RE.search(html)
    if m:
        nxt = urljoin(final_url, _normalize(m.group(1)))
        if nxt != url:
            return resolve(nxt, depth + 1, seen)

    # A cross-host HTTP redirect means the shortener bounced us straight to
    # the real destination (e.g. bit.ly -> example.com).  Treat that as the
    # answer, UNLESS the target is itself a known shortener / protection page
    # or a known noise/ad domain.
    if _host(final_url) != own_host and not _is_shortener(final_url):
        if _is_noise(final_url):
            return []
        return [final_url]

    # Link-protection "unlock" form (POST + CSRF) -> reveals the real links.
    unlocked = _try_csrf_unlock(final_url, html)
    if unlocked:
        html = unlocked

    candidates = _extract_candidates(html, final_url, own_host)
    results: list[str] = []

    for cand in candidates:
        if _is_shortener(cand):
            # still a shortener -> dig one level deeper
            results.extend(resolve(cand, depth + 1, seen))
        else:
            # looks like a genuine destination
            results.append(cand)

    if results:
        return list(dict.fromkeys(results))  # dedup, keep order

    # Fallback: no embedded links found, but the redirect landed elsewhere.
    if _host(final_url) != own_host:
        return [final_url]
    return []


def unshorten(url: str) -> dict:
    """Convenience wrapper returning a small result dict."""
    try:
        results = resolve(url)
        return {
            "ok": bool(results),
            "input": url,
            "results": results,
            "error": None if results else "Could not resolve the link.",
        }
    except Exception as exc:  # noqa: BLE001 - report any failure gracefully
        return {"ok": False, "input": url, "results": [], "error": str(exc)}
