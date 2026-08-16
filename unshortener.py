"""
unshortener.py — Advanced URL "bypass" / unshortening engine.

How it works (layered strategy):
  1. Parameter & Token Unpacking:
     Detect embedded / Base64 / Hex / URL-encoded destination URLs in query
     parameters (e.g. sfl.gl/ready/go?u=..., ?url=..., ?link=..., ?target=...,
     ?r=..., ?dest=..., ?go=..., etc.).
  2. Follow HTTP redirects (301, 302, 303, 307, 308) with realistic browser
     session headers and cookie persistence.
  3. Intermediary / Safelink Script Handling:
     Detect redirect scripts (/redirect.php, /safe.php, /go.php, /ready/go, etc.)
     on intermediary blogs (khaddavi.net, tutwuri.id, bahasteknologi.com, etc.),
     fetch/submit them with session cookies & Referer, and unpack the target.
  4. Form Unlock & CSRF:
     Submit link-protection / countdown POST forms with CSRF and hidden tokens.
  5. JS / Service Specific Decoders:
     AdF.ly ysmm decoder, AdFoc.us click_url, Sub2Unlock / Rekonise / Boost.ink
     data-url and inline JSON target extractors.
  6. Noise & Priority Filtering:
     Filter out ad networks, trackers, CDN static assets, social share links,
     anti-adblock pages, and rank genuine destination hosts (Mediafire, Mega,
     Google Drive, GitHub, etc.) at the top.
  7. Recursive Resolution:
     Recursively resolve any candidate that is itself a known shortener or
     intermediary hop up to MAX_DEPTH.
"""

from __future__ import annotations

import base64
import html as _html
import json
import re
import urllib.parse
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests

TIMEOUT = 15
MAX_DEPTH = 6

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# ---------------------------------------------------------------------------
# Known shortener / link-protection / safelink intermediary domains.
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
    "cleanuri.com", "tinyone.cf", "bl.ly", "linklyhq.com",

    # Safelink / SafelinkU / Tutwuri / Indonesian shorteners & blogs
    "sfl.gl", "sfl2.gl", "sflist.com", "safelinku.com", "safelinku.net",
    "tutwuri.id", "tutwuri.net", "khaddavi.net", "app.khaddavi.net",
    "bahasteknologi.com", "litetekno.com", "idsly.bid", "idsly.net",
    "suarankri.me", "lokerwfh.net", "shortxlinks.in", "v2links.me",
    "clks.pro", "clk.wiki", "teknosemesta.com", "karyawan.co.id",
    "tempatwisata.pro", "lajangspot.web.id", "inshortnote.com",
    "teknoasian.com", "beritain.id", "seputargit.com", "reminimod.co",
    "adikfilm.link", "adikfilm.click", "billasopus.com", "besargaji.com",
    "droplink.co", "droplink.org", "semawur.com", "semawur.id", "rodimalam.com",

    # ad / link-protection ("money") shorteners
    "adf.ly", "adfoc.us", "bc.vc", "bcvc.live", "ouo.io", "shorte.st", "sh.st",
    "shink.me", "shrinkme.io", "shrinkearn.com", "linkvertise.com",
    "link-to.net", "link-center.net", "up-to-down.net", "linkszilla.top",
    "mobilejsr.com", "link.tl", "exe.io", "exey.io", "stfly.me", "za.gl",
    "aylink.co", "ayw.top", "boost.ink", "clk.sh", "cuty.io", "dlaf.info",
    "fc.lc", "fc-lc.com", "fc-lc.xyz", "gplinks.co", "gplink.co", "gplinks.in",
    "krownlinks.me", "laymro.com", "link1s.com", "mboost.me", "meoqw.com",
    "moiity.com", "mytc.pw", "newsurl.xyz", "pihe.in", "pnd.tl", "rekonise.com",
    "short-jambo.com", "sub2unlock.com", "sub2unlock.net", "sub2unlock.io",
    "sub4unlock.io", "sub4unlock.co", "sub2get.com", "tekcrypt.in",
    "thinfi.com", "try2link.com", "urlsopen.com", "vivads.net", "xpshort.com",
    "rswebsols.com", "adshort.co", "adshort.im", "link4m.co", "lnkload.com",
    "heylink.me", "safevideolink.com", "cyberpandit.in", "techymozo.com",
    "bindaaslinks.com", "indiurl.com", "shorte.be", "tutorialslink.com",
    "linkrex.net", "earnvisits.com", "clicksfly.com", "smoner.com",
    "openget.net", "themefiles.net", "filelink.org", "cpmlink.net",
    "skmurl.com", "clkmein.com", "cobrabirla.com", "shortzon.com",
    "adpaylink.com", "cash4link.link", "pdiskpro.in", "slfly.net",
    "icashfly.com", "10short.pro", "10short.vip", "crazyblog.in",
    "ushort.xyz", "flashlinks.in", "filmypoints.in", "forextrader.site",
    "kpslink.in", "techleets.xyz", "happyfiles.dtglinks.in", "bestshortlink.top",
    "getslinks.online", "cloudshrinker.com", "eductin.com", "pvidly.in",
    "speedynews.xyz", "paylinnk.com", "syflink.com", "acortalink.net",
    "acortalink.me", "bstlar.com", "rotizer.net", "linkforearn.com",
    "downfile.site", "enlacito.com", "adtival.network", "imagereviser.com",
    "amanguides.com", "stockmarg.com", "8tm.net", "bestfonts.pro",
    "paycut.pro", "forex-trnd.com", "sharetext.me", "fansonlinehub.com",
    "slink.bid", "creditsgoal.com", "zegtrends.com", "linkspy.cc",
    "dinheiromoney.com", "flamebook.eu.org", "jobzhub.store", "curto.win",
    "infonerd.org", "yitarx.com", "videolyrics.in", "takefile.link",
    "coinsrev.com", "socialwolvez.com", "shortit.pw", "playnano.online",
    "2linkes.com", "mazen-ve3.com", "1shortlink.com", "1short.io",
    "revlink.pro", "cshort.org", "linksly.co", "lksfy.com", "almontsf.com",
}

# Domains that are never the real destination (ad networks, trackers, cdn).
NOISE_DOMAINS = {
    "google.com", "google.co.in", "googleapis.com", "gstatic.com",
    "googletagmanager.com", "googlesyndication.com", "doubleclick.net",
    "google-analytics.com", "googleadservices.com", "googleusercontent.com",
    "facebook.com", "fbcdn.net", "facebook.net", "twitter.com", "x.com",
    "twimg.com", "instagram.com", "youtube.com", "youtube-nocookie.com",
    "ytimg.com", "tiktok.com", "linkedin.com", "reddit.com", "pinterest.com",
    "whatsapp.com", "api.whatsapp.com", "telegram.org", "t.me", "vk.com",
    "discord.com", "cloudflare.com", "cloudflareinsights.com", "w3.org",
    "schema.org", "jquery.com", "gravatar.com", "recaptcha.net", "hcaptcha.com",
    "addthis.com", "sharethis.com", "disqus.com", "onesignal.com",
    "pushalert.co", "pushowl.com", "profitablecpmratenetwork.com",
    "propellerads.com", "popads.net", "popcash.net", "adsterra.com",
    "exoclick.com", "juicyads.com", "adf.ly.cdn", "cdn.jsdelivr.net",
    "unpkg.com", "bootstrapcdn.com", "fontawesome.com", "favicon.cc",
    "waust.at", "driverhugoverblown.com", "dmus.in", "revenuehits.com",
    "llvpn.com", "adl-media.com", "amung.us", "statcounter.com",
    "histats.com", "hitcounter.com", "clicky.com", "zopim.com",
    "tawk.to", "intercom.io", "crisp.chat", "livechatinc.com",
    # anti-adblock / "please disable adblocker" landing pages
    "antiblock.org", "adblockplus.org", "adblock.com", "getadblock.com",
    "ublock.org", "ublockorigin.com", "ghostery.com", "adguard.com",
    "nothanks.com", "blockadblock.com", "fuckadblock.site",
}

# Preferred file hosts / genuine destination services for priority ranking
HIGH_PRIORITY_HOSTS = {
    "mediafire.com", "mega.nz", "mega.co.nz", "drive.google.com",
    "dropbox.com", "github.com", "gitlab.com", "gofile.io",
    "1fichier.com", "pixeldrain.com", "rapidgator.net", "krakenfiles.com",
    "workupload.com", "uploadhaven.com", "apkadmin.com", "files.fm",
    "katfile.com", "udrop.com", "buzzheavier.com", "bowfile.com",
    "dailyuploads.net", "megaup.net", "mega4upload.net", "zippyshare.com",
    "modsfire.com", "terabox.com", "terabox.app", "upload.ee", "dbree.me",
    "easyupload.io", "dropgalaxy.com", "file-upload.net", "file-upload.org",
    "filemoon.sx", "send.now", "dataupload.net", "turbobit.net",
    "sharemods.com", "desiupload.co", "modsbase.com", "doodrive.com",
    "qiwi.gg", "up-4ever.net", "hitfile.net", "sourceforce.net",
}

# Intermediary / Safelink redirect scripts and paths that are NEVER final destinations
INTERMEDIARY_PATH_RE = re.compile(
    r"""/(?:redirect|safe|go|out|links|link|get-link|direct|download|view|landing)\.php\b|/(?:ready/go|go/|safe/|links?/)\b""",
    re.IGNORECASE,
)

_LINK_RE = re.compile(
    r"""(?:href|src|action|data-url|data-link|data-href|data-target-url|data-target|data-destination|content)\s*=\s*["']([^"']+)["']""",
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
_JS_LOCATION_RE = re.compile(
    r"""(?:window\.)?(?:location|document\.location)(?:\.href)?\s*=\s*["']([^"']+)["']|location\.(?:replace|assign)\s*\(\s*["']([^"']+)["']\s*\)""",
    re.IGNORECASE,
)
_ADFLY_YSMM_RE = re.compile(r"""(?:var\s+)?ysmm\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)
_ADFOCUS_CLICK_RE = re.compile(r"""(?:var\s+)?click_url\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)

# Form parsing
_FORM_BLOCK_RE = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.IGNORECASE | re.DOTALL)
_INPUT_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE)
_NAME_RE = re.compile(r"""name\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_VALUE_RE = re.compile(r"""value\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_ACTION_RE = re.compile(r"""action\s*=\s*["']([^"']*)["']""", re.IGNORECASE)

_session = requests.Session()


def _get(url: str, referer: str | None = None) -> requests.Response | None:
    """Fetch a URL following redirects, with a browser-like UA and Referer."""
    req_headers = dict(HEADERS)
    if referer:
        req_headers["Referer"] = referer
    try:
        resp = _session.get(
            url,
            headers=req_headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        return resp
    except requests.RequestException:
        return None


def _host(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    if h.startswith("www."):
        h = h[4:]
    return h


# Static assets are never the destination a user is looking for.
_ASSET_EXT_RE = re.compile(
    r"\.(js|css|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|otf|eot|mp3|mp4|"
    r"webm|avi|mov|map|json|xml|txt)$",
    re.IGNORECASE,
)


def _normalize(url: str) -> str:
    """Unescape HTML entities and strip whitespace."""
    url = _html.unescape(url).strip()
    url = url.replace("&amp;", "&")
    return url


def _is_shortener(url: str) -> bool:
    h = _host(url)
    if not h:
        return False
    if h in KNOWN_SHORTENERS:
        return True
    for s in KNOWN_SHORTENERS:
        if h.endswith("." + s):
            return True
    return False


def _is_noise(url: str) -> bool:
    h = _host(url)
    if not h:
        return True
    for d in NOISE_DOMAINS:
        if h == d or h.endswith("." + d):
            return True
    # Filter social share endpoints
    p = urlparse(url)
    if "facebook.com/sharer" in url or "twitter.com/intent" in url or "api.whatsapp.com" in url:
        return True
    return False


def _is_intermediary(url: str) -> bool:
    """Check if URL points to an intermediary redirect script rather than a final page."""
    p = urlparse(url)
    if INTERMEDIARY_PATH_RE.search(p.path):
        return True
    return False


def try_decode_base64_url(s: str) -> str | None:
    """
    Attempt to decode a string as single or multi-level Base64 to find an embedded URL.
    Supports standard Base64, URL-safe Base64, and URL-encoded strings.
    """
    if not isinstance(s, str) or len(s) < 8:
        return None
    curr = s.strip()
    for _ in range(6):
        try:
            curr = unquote(curr)
            # URL-safe base64 substitution
            std_b64 = curr.replace("-", "+").replace("_", "/")
            padded = std_b64 + "=" * (-len(std_b64) % 4)
            decoded_bytes = base64.b64decode(padded, validate=False)
            decoded = decoded_bytes.decode("utf-8", errors="ignore").strip()
            if not decoded:
                break
            if decoded.startswith(("http://", "https://")):
                return decoded
            m = re.search(r"https?://[^\s\"'<>]+", decoded)
            if m:
                return m.group(0)
            # If decoded looks like another base64 string, continue loop
            if re.match(r"^[A-Za-z0-9+/=_-]{8,}$", decoded):
                curr = decoded
            else:
                break
        except Exception:
            break
    return None


def unpack_embedded_url(url: str) -> str | None:
    """
    Check if a URL contains an embedded destination URL in its query parameters,
    fragment, or path (e.g. sfl.gl/ready/go?u=..., ?url=..., ?r=..., ?dest=...).
    """
    try:
        p = urlparse(url)
    except Exception:
        return None

    # 1. Query parameters
    qs = parse_qs(p.query, keep_blank_values=False)
    # Check common target param keys first, then all
    priority_keys = [
        "u", "url", "link", "target", "dest", "destination", "r", "to",
        "go", "dl", "download", "file", "safe", "redirect", "redirect_to",
        "uri", "data", "i", "newwll", "token", "k", "href", "out"
    ]
    all_keys = priority_keys + [k for k in qs if k not in priority_keys]

    for k in all_keys:
        for val in qs.get(k, []):
            val = val.strip()
            # Direct URL
            if val.startswith(("http://", "https://")):
                return val
            # URL-encoded direct URL
            unq = unquote(val)
            if unq.startswith(("http://", "https://")):
                return unq
            # Base64 encoded URL
            b64 = try_decode_base64_url(val)
            if b64:
                return b64

    # 2. Fragment
    if p.fragment:
        frag = p.fragment.strip()
        if frag.startswith(("http://", "https://")):
            return frag
        b64 = try_decode_base64_url(frag)
        if b64:
            return b64

    # 3. Path suffix / encoded segments (e.g. /go/aHR0cHM...)
    path_segments = p.path.strip("/").split("/")
    for seg in path_segments:
        b64 = try_decode_base64_url(seg)
        if b64:
            return b64

    return None


def decode_adfly(ysmm: str) -> str | None:
    """Decode AdF.ly / ysmm obfuscated target URL."""
    try:
        I, X = "", ""
        for i in range(len(ysmm)):
            if i % 2 == 0:
                I += ysmm[i]
            else:
                X = ysmm[i] + X
        encoded = list(I + X)
        for i in range(len(encoded)):
            if encoded[i].isdigit():
                for j in range(i + 1, len(encoded)):
                    if encoded[j].isdigit():
                        num = int(encoded[i]) ^ int(encoded[j])
                        if num < 10:
                            encoded[i] = str(num)
                        break
        decoded = base64.b64decode("".join(encoded).encode()).decode("utf-8", errors="ignore")
        res = decoded[16:-16]
        if res.startswith(("http://", "https://")):
            return res
    except Exception:
        pass
    return None


def _try_form_unlock(final_url: str, html: str) -> list[str]:
    """
    Handle POST forms on link-protection / safelink pages (mobilejsr, khaddavi,
    tutwuri, ouo, etc.) and submit them to retrieve the unlocked HTML or redirect.
    """
    results: list[str] = []
    for m in _FORM_BLOCK_RE.finditer(html):
        open_tag = m.group(1)
        inner = m.group(2)

        am = _ACTION_RE.search(open_tag)
        action = urljoin(final_url, _html.unescape(am.group(1))) if am else final_url

        data: dict[str, str] = {}
        for inp in _INPUT_RE.findall(inner):
            nm = _NAME_RE.search(inp)
            if not nm:
                continue
            name = _html.unescape(nm.group(1))
            vm = _VALUE_RE.search(inp)
            value = _html.unescape(vm.group(1)) if vm else ""
            data[name] = value

            # Check if any input value is an encoded URL
            unpacked = unpack_embedded_url(f"?val={value}")
            if unpacked and not _is_noise(unpacked):
                results.append(unpacked)

        if not data and "post" not in open_tag.lower():
            continue

        try:
            resp = _session.post(
                action,
                data=data,
                headers={**HEADERS, "Referer": final_url},
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            if resp.ok and resp.url and resp.url != final_url:
                results.append(resp.url)
            if resp.ok and resp.text:
                # Check for unpacked URL in response url
                unp = unpack_embedded_url(resp.url)
                if unp:
                    results.append(unp)
        except requests.RequestException:
            continue
    return results


def _extract_candidates(html: str, base_url: str, own_host: str) -> list[str]:
    """Pull every plausible destination URL out of the page HTML / JS."""
    found: list[str] = []

    # 1. Check for AdFly ysmm
    ym = _ADFLY_YSMM_RE.search(html)
    if ym:
        decoded = decode_adfly(ym.group(1))
        if decoded:
            found.append(decoded)

    # 2. Check for AdFocus click_url
    cm = _ADFOCUS_CLICK_RE.search(html)
    if cm:
        target = _normalize(cm.group(1))
        if target.startswith(("http://", "https://")):
            found.append(target)

    # 3. Tag attributes (href / src / data-url / data-destination / action / ...)
    for raw in _LINK_RE.findall(html):
        url = _normalize(raw)
        if url.startswith("//"):
            url = "https:" + url
        url = urljoin(base_url, url)
        found.append(url)

    # 4. JS location changes
    for m in _JS_LOCATION_RE.finditer(html):
        target = m.group(1) or m.group(2)
        if target:
            target = urljoin(base_url, _normalize(target))
            found.append(target)

    # 5. Raw http(s) URLs embedded anywhere in scripts / JSON / variables
    for raw in _SCRIPT_URL_RE.findall(html):
        url = _normalize(raw)
        found.append(urljoin(base_url, url))

    # 6. Embedded base64 strings in script / HTML
    for b64_match in re.findall(r"""["']([A-Za-z0-9+/=_-]{20,})["']""", html):
        decoded = try_decode_base64_url(b64_match)
        if decoded and not _is_noise(decoded):
            found.append(decoded)

    out: list[str] = []
    seen: set[str] = set()
    for url in found:
        # Check if URL itself has embedded params
        unpacked = unpack_embedded_url(url)
        candidates_to_add = [unpacked, url] if unpacked else [url]

        for cand in candidates_to_add:
            p = urlparse(cand)
            if p.scheme not in ("http", "https"):
                continue
            host = (p.hostname or "").lower()
            if not host:
                continue
            if _ASSET_EXT_RE.search(p.path):
                continue
            if _is_noise(cand):
                continue
            if cand in seen:
                continue
            seen.add(cand)
            out.append(cand)
    return out


def _score_candidate(url: str) -> int:
    """Score candidate URLs so real download/file hosts are ranked highest."""
    h = _host(url)
    for p in HIGH_PRIORITY_HOSTS:
        if h == p or h.endswith("." + p):
            return 100
    if _is_intermediary(url) or _is_shortener(url):
        return 10
    return 50


def resolve(url: str, depth: int = 0, seen: set[str] | None = None) -> list[str]:
    """
    Resolve a (possibly shortened / protected) URL to its real destination(s).

    Returns a list of destination URLs, empty on failure.
    """
    seen = seen if seen is not None else set()
    if depth > MAX_DEPTH:
        return []
    if url in seen:
        return []
    seen.add(url)

    # Step 1: Pre-check if input URL has embedded destination (e.g. sfl.gl/ready/go?u=...)
    unpacked = unpack_embedded_url(url)
    if unpacked and unpacked not in seen:
        if not _is_shortener(unpacked) and not _is_intermediary(unpacked) and not _is_noise(unpacked):
            # Already genuine destination!
            return [unpacked]
        sub = resolve(unpacked, depth + 1, seen)
        if sub:
            return sub

    # Step 2: Fetch the URL following HTTP redirects
    resp = _get(url)
    if resp is None:
        return []

    final_url = resp.url
    content_type = resp.headers.get("Content-Type", "").lower()

    # Step 3: Check landing URL for embedded destination
    unpacked_final = unpack_embedded_url(final_url)
    if unpacked_final and unpacked_final not in seen:
        if not _is_shortener(unpacked_final) and not _is_intermediary(unpacked_final) and not _is_noise(unpacked_final):
            return [unpacked_final]
        sub = resolve(unpacked_final, depth + 1, seen)
        if sub:
            return sub

    # Not an HTML page -> this IS the destination (file, archive, image, etc.).
    if content_type and "html" not in content_type:
        return [final_url]

    html = resp.text or ""
    own_host = _host(url)

    # Step 4: Meta-refresh redirect
    m = _META_REFRESH_RE.search(html)
    if m:
        nxt = urljoin(final_url, _normalize(m.group(1)))
        if nxt != url and nxt not in seen:
            return resolve(nxt, depth + 1, seen)

    # Step 5: Form unlock / CSRF submit
    form_results = _try_form_unlock(final_url, html)
    for fr in form_results:
        if fr not in seen:
            sub = resolve(fr, depth + 1, seen)
            if sub:
                return sub

    # Step 6: Extract candidate URLs from page
    candidates = _extract_candidates(html, final_url, own_host)

    # Handle intermediary redirect scripts (e.g. redirect.php on safelink blog)
    # Fetch them with active session cookies and Referer
    for cand in list(candidates):
        if _is_intermediary(cand) and cand not in seen:
            try:
                sub_res = resolve(cand, depth + 1, seen)
                if sub_res:
                    return sub_res
            except Exception:
                pass

    # A cross-host HTTP redirect where final_url is not a shortener or intermediary
    # and no intermediary scripts exist
    if (
        _host(final_url) != own_host
        and not _is_shortener(final_url)
        and not _is_intermediary(final_url)
        and not _is_noise(final_url)
        and not any(_is_intermediary(c) for c in candidates)
    ):
        return [final_url]

    # Resolve candidates
    results: list[str] = []
    # Sort candidates by score (file hosts first)
    candidates.sort(key=_score_candidate, reverse=True)

    for cand in candidates:
        if _is_intermediary(cand):
            continue  # Already attempted above
        if _is_shortener(cand):
            results.extend(resolve(cand, depth + 1, seen))
        else:
            results.append(cand)

    # Filter out intermediary scripts and noise from final results
    filtered_results = [
        r for r in results
        if not _is_noise(r) and not _is_intermediary(r)
    ]

    if filtered_results:
        # Deduplicate while preserving order
        return list(dict.fromkeys(filtered_results))

    # Fallback: if redirect landed elsewhere and is not noise/intermediary
    if _host(final_url) != own_host and not _is_noise(final_url) and not _is_intermediary(final_url):
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
