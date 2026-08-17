"""
adlinkfly.py — bypass for AdLinkFly-based shorteners.

Covers gplinks.co, liteshort.com and the ~dozens of clones that run the same
"AdLinkFly" PHP script (adrinolinks, link.liteshort.com, shortnest, ouo-style
clones, …).  All of them share one protocol:

    1.  GET https://host/<code>
        -> 302 to  https://host/<code>?vid=<visitor id>   (or an ad
           interstitial such as powergam.online carrying lid/pid/vid).
    2.  GET the ?vid= page.  It contains

            <form id="go-link" ...>
              <input name="_token" value="...">
              <input name="ad_form_data" value="...">
              <input name="_Token" value="...">        (clone-specific names)
            </form>

        …plus a JS countdown ("please wait N seconds").
    3.  Wait out the countdown, then

            POST https://host/links/go
            X-Requested-With: XMLHttpRequest
            <the form fields>

        -> {"status":"success","url":"https://real-destination/..."}

Interstitial variant (newer gplinks): the short link bounces to an ad blog
(powergam.online & co) that stores `lid`, `pid`, `pages`, `vid` in cookies /
query string.  The real AdLinkFly page is then

    https://gplinks.co/<base64url-decoded lid>?pid=<decoded pid>&vid=<vid>

which we build directly instead of walking the "Step 1 of 2 → CONTINUE" ads.

Everything goes through httpclient.Client, so Cloudflare (incl. v3 managed
challenge / Turnstile via FlareSolverr) is handled one layer below.
"""

from __future__ import annotations

import base64
import html as _html
import json
import re
from urllib.parse import parse_qs, urljoin, urlparse

from httpclient import Client, detect_countdown, polite_sleep

# Hosts known to run AdLinkFly.  Any other host is still attempted when the
# page structure matches (see looks_like_adlinkfly).
ADLINKFLY_HOSTS = {
    "gplinks.co", "gplinks.in", "gplink.co", "gplink.in",
    "liteshort.com", "link.liteshort.com", "litelink.in",
    "adrinolinks.com", "adrinolinks.in", "adrinolinks.link",
    "shortnest.com", "linkjust.com", "pahe.plus", "go.zovo.ink",
    "gadinow.in", "jobsmbn.in", "strictstrategies.com", "carrnissan.com",
    "clk.sh", "cutt.io", "cuty.io", "exe.io", "exey.io", "fc.lc",
    "za.gl", "zagl.link", "ez4short.com", "link1s.com", "link4m.co",
    "urlsopen.com", "xpshort.com", "techymozo.com", "krownlinks.me",
    "mdisk.pro", "atglinks.com", "tnlink.in", "indianshortner.in",
    "urlspay.in", "earn4link.in", "vearnl.in", "sklinks.in",
    "onepagelink.in", "shorturllink.in", "modijiurl.com", "dulink.in",
}

# Ad-blog interstitials used by gplinks & friends.
INTERSTITIAL_HINT_KEYS = {"lid", "pid", "vid", "pages"}

_FORM_RE = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.IGNORECASE | re.DOTALL)
_INPUT_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""(\w[\w:-]*)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_GO_LINK_BLOCK_RE = re.compile(
    r"""id\s*=\s*["']go-link["'](.*?)</form>""", re.IGNORECASE | re.DOTALL
)
_JSON_URL_RE = re.compile(r'"url"\s*:\s*"([^"]+)"')


def host_of(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def is_adlinkfly_host(url: str) -> bool:
    h = host_of(url)
    if h in ADLINKFLY_HOSTS:
        return True
    # subdomains of a known host (link.liteshort.com, go.gplinks.co, …)
    return any(h.endswith("." + d) for d in ADLINKFLY_HOSTS)


def looks_like_adlinkfly(html: str) -> bool:
    low = (html or "").lower()
    return (
        'id="go-link"' in low
        or "id='go-link'" in low
        or "/links/go" in low
        or "ad_form_data" in low
    )


def _attrs(tag: str) -> dict[str, str]:
    return {k.lower(): _html.unescape(v) for k, v in _ATTR_RE.findall(tag)}


def _fields_from(fragment: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag in _INPUT_RE.findall(fragment):
        a = _attrs(tag)
        name = a.get("name")
        if name:
            out[name] = a.get("value", "")
    return out


def extract_go_link_fields(html: str) -> dict[str, str]:
    """Pull the hidden fields of the #go-link form (or any /links/go form)."""
    m = _GO_LINK_BLOCK_RE.search(html or "")
    if m:
        fields = _fields_from(m.group(1))
        if fields:
            return fields

    for fm in _FORM_RE.finditer(html or ""):
        open_tag, inner = fm.group(1), fm.group(2)
        a = _attrs("<form " + open_tag + ">")
        if "go-link" in (a.get("id", "") + a.get("class", "")) or "links/go" in a.get("action", ""):
            fields = _fields_from(inner)
            if fields:
                return fields

    # Some clones build the payload in JS: var ad_form_data = "..."
    fields = {}
    for name in ("_token", "ad_form_data", "_Token", "token", "csrf_token"):
        jm = re.search(
            rf"""["']?{re.escape(name)}["']?\s*[:=]\s*["']([^"']+)["']""",
            html or "",
        )
        if jm:
            fields[name] = jm.group(1)
    return fields


def _b64url_decode(s: str) -> str:
    if not s or not re.fullmatch(r"[A-Za-z0-9\-_=]+", s):
        return s
    t = s.replace("-", "+").replace("_", "/")
    t += "=" * (-len(t) % 4)
    try:
        return base64.b64decode(t).decode("utf-8", "replace")
    except Exception:
        return s


_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{4,32}$")


def _params_from(url: str, cookies: dict[str, str]) -> dict[str, tuple[str, str]]:
    """
    Collect lid/pid/vid/pages, remembering where each value came from.

    The two live variants differ in encoding:
      * query string (powergam-style ?lid=…&pid=…) -> values are base64url
      * cookies (skrresults-style, set by the ad blog) -> values are raw
    so the source decides whether to decode.
    """
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items() if v}
    out: dict[str, tuple[str, str]] = {}
    for key in ("lid", "pid", "vid", "pages"):
        if q.get(key):
            out[key] = (q[key], "query")
        elif cookies.get(key):
            out[key] = (cookies[key], "cookie")
    return out


def _code_candidates(lid: str, source: str) -> list[str]:
    """Possible short codes for a lid, best guess first."""
    cands: list[str] = []
    if source == "cookie":
        # Cookies hold the raw code (lid=ZkVCbbry); decoding it yields junk.
        cands.append(lid)
        dec = _b64url_decode(lid)
        if dec != lid:
            cands.append(dec)
    else:
        dec = _b64url_decode(lid)
        if dec != lid:
            cands.append(dec)
        cands.append(lid)
    out = []
    for c in cands:
        c = (c or "").strip()
        if c and _CODE_RE.match(c) and c not in out:
            out.append(c)
    return out


def interstitial_target(
    url: str,
    cookies: dict[str, str],
    origin_host: str,
    scheme: str = "https",
) -> str | None:
    """
    Rebuild the real AdLinkFly URL from an ad-blog interstitial's parameters,
    skipping the whole "Step 1 of N / CONTINUE" ad walk.
    """
    targets = interstitial_targets(url, cookies, origin_host, scheme)
    return targets[0] if targets else None


def interstitial_targets(
    url: str,
    cookies: dict[str, str],
    origin_host: str,
    scheme: str = "https",
) -> list[str]:
    """All plausible rebuilt URLs (raw vs base64-decoded lid), best first."""
    p = _params_from(url, cookies)
    if "lid" not in p:
        return []
    lid, lid_src = p["lid"]

    pid_val, pid_src = p.get("pid", ("", "cookie"))
    pid = pid_val if pid_src == "cookie" else _b64url_decode(pid_val)
    vid = p.get("vid", ("", ""))[0]

    q = []
    if pid:
        q.append(f"pid={pid}")
    if vid:
        q.append(f"vid={vid}")
    tail = ("?" + "&".join(q)) if q else ""

    return [
        f"{scheme}://{origin_host}/{code}{tail}"
        for code in _code_candidates(lid, lid_src)
    ]


def _post_links_go(
    client: Client, origin: str, fields: dict[str, str], referer: str
) -> str | None:
    """POST the unlock form to /links/go and read the destination out of it."""
    endpoints = [f"{origin}/links/go", f"{origin}/links/go.php", f"{origin}/go"]
    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": origin,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    for ep in endpoints:
        resp = client.post(ep, headers=headers, data=fields, referer=referer)
        if resp is None or not resp.ok or not resp.text:
            continue
        body = resp.text.strip()
        try:
            data = json.loads(body)
            url = data.get("url") or data.get("link") or data.get("data")
            if isinstance(url, dict):
                url = url.get("url")
            if url and str(url).startswith("http"):
                return str(url)
        except (ValueError, AttributeError):
            m = _JSON_URL_RE.search(body)
            if m:
                cand = m.group(1).replace("\\/", "/")
                if cand.startswith("http"):
                    return cand
    return None


def _unlock_page(
    client: Client, page_url: str, origin: str, referer: str
) -> str | None:
    """Load an AdLinkFly unlock page and POST its #go-link form."""
    page = client.get(page_url, referer=referer)
    if page is None:
        return None
    html = page.text or ""
    if not looks_like_adlinkfly(html):
        return None
    fields = extract_go_link_fields(html)
    if not fields:
        return None
    polite_sleep(detect_countdown(html, 8.0))
    return _post_links_go(client, origin, fields, referer=page.url)


def _try_targets(
    client: Client, targets: list[str], origin: str, origin_host: str
) -> str | None:
    """Try each rebuilt AdLinkFly URL until one unlocks."""
    for t in targets:
        dest = _unlock_page(client, t, origin, referer=origin + "/")
        if dest and host_of(dest) != origin_host:
            return dest
    return None


def bypass(url: str, client: Client | None = None, _depth: int = 0) -> str | None:
    """
    Resolve one AdLinkFly short link (gplinks / liteshort / clone) to its
    real destination.  Returns None if the flow does not apply or fails.
    """
    if _depth > 3:
        return None
    client = client or Client()
    parsed = urlparse(url)
    origin_host = host_of(url)
    scheme = parsed.scheme or "https"
    origin = f"{scheme}://{parsed.netloc}"

    # Step 1 — first hop, unredirected, to capture the ?vid= visitor id.
    first = client.get(url, allow_redirects=False)
    if first is None:
        return None

    target = url
    loc = None
    for k, v in (first.headers or {}).items():
        if k.lower() == "location":
            loc = v
            break

    if loc:
        loc = urljoin(url, loc)
        loc_host = host_of(loc)
        if loc_host == origin_host:
            target = loc
        else:
            # Bounced off-site.  This is either an ad-blog interstitial or a
            # genuine destination — the parameters decide which.
            rebuilt = interstitial_targets(
                loc, client.cookies, parsed.netloc, scheme
            )
            if not rebuilt:
                # The live gplinks flow redirects with NO query params: the
                # ad blog plants lid/pid/vid as cookies in its own response,
                # so we must load it before we can read them.
                blog = client.get(loc, referer=origin + "/")
                blog_url = blog.url if blog is not None else loc
                rebuilt = interstitial_targets(
                    blog_url, client.cookies, parsed.netloc, scheme
                )
                if not rebuilt and blog is not None:
                    # Ad blog running AdLinkFly itself? Unlock it in place.
                    if looks_like_adlinkfly(blog.text):
                        return bypass(blog.url, client, _depth + 1)

            if rebuilt:
                dest = _try_targets(client, rebuilt, origin, origin_host)
                if dest:
                    return dest
                target = rebuilt[0]
            elif is_adlinkfly_host(loc):
                return bypass(loc, client, _depth + 1)
            else:
                # Plain redirect to the destination — nothing to unlock.
                return loc
    elif "vid=" not in url:
        # No redirect: some clones expect an explicit ?vid marker.
        target = url

    # Step 2 — load the unlock page.
    page = client.get(target, referer=origin + "/")
    if page is None:
        return None

    # Landed straight on the destination?
    if host_of(page.url) not in (origin_host, "") and not looks_like_adlinkfly(page.text):
        if not is_adlinkfly_host(page.url):
            return page.url

    html = page.text or ""

    # Interstitial served as a page (no Location header).
    if not looks_like_adlinkfly(html):
        rebuilt = interstitial_target(
            page.url, client.cookies, parsed.netloc, scheme
        )
        if rebuilt and rebuilt != target:
            page = client.get(rebuilt, referer=page.url)
            if page is None:
                return None
            html = page.text or ""

    fields = extract_go_link_fields(html)
    if not fields:
        return None

    # Step 3 — wait out the countdown, then unlock.
    polite_sleep(detect_countdown(html, 8.0))
    dest = _post_links_go(client, origin, fields, referer=page.url)
    if dest and is_adlinkfly_host(dest) and host_of(dest) != origin_host:
        deeper = bypass(dest, client, _depth + 1)
        return deeper or dest
    return dest
