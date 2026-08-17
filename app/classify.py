"""
classify.py — decide whether a URL is a real destination, a shortener, or
noise (ads, trackers, the shortener's own error/interstitial pages).

This module is the accuracy backbone: the previous system's worst failure was
reporting an ad blog (skrresults.com) or an error page
(gplinks.com/link-error?error_code=not_enough_steps) as "the answer".  Every
result must pass through `verdict()` before it is shown to a user.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Shorteners / link-lockers — never a final answer, always resolve further.
# ---------------------------------------------------------------------------
SHORTENERS: set[str] = {
    # AdLinkFly family & Indian shortener network
    "gplinks.co", "gplinks.com", "gplinks.in", "gplink.co", "gplink.in",
    "liteshort.com", "litelink.in", "adrinolinks.com", "adrinolinks.in",
    "adrinolinks.link", "shortnest.com", "linkjust.com", "pahe.plus",
    "zovo.ink", "gadinow.in", "jobsmbn.in", "strictstrategies.com",
    "carrnissan.com", "atglinks.com", "tnlink.in", "indianshortner.in",
    "urlspay.in", "earn4link.in", "vearnl.in", "sklinks.in", "dulink.in",
    "onepagelink.in", "shorturllink.in", "modijiurl.com", "mdisk.pro",
    "krownlinks.me", "techymozo.com", "xpshort.com", "urlsopen.com",
    "link4m.co", "link1s.com", "ez4short.com", "za.gl", "fc.lc", "clk.sh",
    "cuty.io", "exe.io", "exey.io", "cutt.io", "try2link.com", "stfly.me",
    "boost.ink", "mboost.me", "aylink.co", "shrinkme.io", "shrinkearn.com",
    "shorte.st", "adf.ly", "adfoc.us", "ouo.io", "ouo.press", "bc.vc",
    "linkvertise.com", "link-to.net", "link-center.net", "up-to-down.net",
    "rekonise.com", "sub2unlock.com", "sub2unlock.net", "sub2get.com",
    "linkszilla.top", "mobilejsr.com", "safelinku.com", "sfl.gl",
    "khaddavi.net", "clicksfly.com", "cpmlink.net", "earnvisits.com",
    # classic shorteners
    "bit.ly", "tinyurl.com", "goo.gl", "t.co", "is.gd", "v.gd", "ow.ly",
    "buff.ly", "cutt.ly", "rb.gy", "rebrand.ly", "shorturl.at", "tiny.cc",
    "s.id", "surl.li", "t.ly", "gg.gg", "clck.ru", "da.gd", "u.to",
    "shrtco.de", "soo.gd", "kutt.it", "lnkd.in", "amzn.to", "t2m.io",
}

# ---------------------------------------------------------------------------
# Ad networks, trackers, CDNs, social — never a destination.
# ---------------------------------------------------------------------------
NOISE: set[str] = {
    "google.com", "googleapis.com", "gstatic.com", "googletagmanager.com",
    "googlesyndication.com", "doubleclick.net", "google-analytics.com",
    "googleadservices.com", "googleusercontent.com", "gmpg.org",
    "facebook.com", "fbcdn.net", "facebook.net", "twitter.com", "x.com",
    "instagram.com", "youtube.com", "ytimg.com", "tiktok.com", "vk.com",
    "linkedin.com", "reddit.com", "pinterest.com", "whatsapp.com",
    "telegram.org", "t.me", "discord.com", "discord.gg",
    "cloudflare.com", "cloudflareinsights.com", "w3.org", "schema.org",
    "jquery.com", "jsdelivr.net", "unpkg.com", "bootstrapcdn.com",
    "fontawesome.com", "gravatar.com", "recaptcha.net", "hcaptcha.com",
    "clarity.ms", "w.org", "wordpress.org", "wp.com", "onesignal.com",
    "disqus.com", "addthis.com", "sharethis.com", "statcounter.com",
    "histats.com", "amung.us", "propellerads.com", "popads.net",
    "popcash.net", "adsterra.com", "exoclick.com", "juicyads.com",
    "revenuehits.com", "profitablecpmratenetwork.com", "tawk.to",
    "adblockplus.org", "getadblock.com", "ublockorigin.com", "adguard.com",
    "blogger.com", "blogspot.com", "gtranslate.io", "bing.com",
}

# Hosts that are *known* real file/stream destinations — a strong positive.
KNOWN_DESTINATIONS: dict[str, int] = {
    "devuploads.com": 96, "mediafire.com": 96, "mega.nz": 95, "gofile.io": 93,
    "1fichier.com": 92, "megaup.net": 90, "multiup.io": 90, "pixeldrain.com": 90,
    "krakenfiles.com": 88, "clicknupload.com": 86, "clicknupload.cam": 86,
    "hubcloud.cx": 85, "hubcloud.foo": 85, "hubdrive.space": 85,
    "gdflix.io": 85, "gdtot.pro": 84, "filepress.wiki": 84, "send.now": 82,
    "uploadflix.com": 82, "vikingfile.com": 82, "direct-cloud.top": 82,
    "uploadhub.dad": 80, "frdl.io": 80, "buzzheavier.com": 80,
    "drive.google.com": 94, "dropbox.com": 90, "onedrive.live.com": 88,
    "github.com": 85, "archive.org": 85,
    "streamtape.com": 70, "dood.watch": 68, "mixdrop.co": 68,
    "filemoon.sx": 68, "voe.sx": 68, "luluvdo.com": 68,
}

# Paths that mean "the shortener refused", not "here is your link".
ERROR_PATH_RE = re.compile(
    r"/(?:link-error|error|blocked|expired|not-found|404|invalid|banned|"
    r"suspended|report|abuse|captcha|verify-you|bot-detected)\b",
    re.IGNORECASE,
)
ERROR_QUERY_RE = re.compile(
    r"(?:error_code|error|reason|denied)=", re.IGNORECASE
)

# File extensions that make a URL almost certainly the target.
FILE_EXT_RE = re.compile(
    r"\.(mkv|mp4|avi|mov|wmv|flv|ts|m4v|mp3|flac|m4a|wav|ogg|aac|zip|rar|7z|"
    r"gz|tar|apk|xapk|exe|msi|iso|img|pdf|epub|mobi|cbz|torrent|deb|dmg)"
    r"(?=[?#]|$)",
    re.IGNORECASE,
)

ASSET_EXT_RE = re.compile(
    r"\.(js|css|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|otf|eot|map|xml|txt)"
    r"(?=[?#]|$)",
    re.IGNORECASE,
)

# Ad-blog / interstitial signals: WordPress content sites used as ad steps.
INTERSTITIAL_HINT_RE = re.compile(
    r"\b(?:step\s*\d+\s*of\s*\d+|please\s*wait|click\s*(?:here\s*)?to\s*continue|"
    r"generating\s*(?:your\s*)?link|verify\s*you\s*are\s*human)\b",
    re.IGNORECASE,
)


# WordPress pagination / archive noise on ad blogs: /page/2/, ?paged=3, …
PAGINATION_RE = re.compile(
    r"(?:/page/\d+/?$|[?&]paged?=\d+|/category/|/tag/|/author/|/feed/?$)",
    re.IGNORECASE,
)


def is_pagination(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return bool(PAGINATION_RE.search(p.path + ("?" + p.query if p.query else "")))


def host_of(url: str) -> str:
    """Registrable-ish hostname, lowercase, without a leading www."""
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def _matches(host: str, table: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in table)


def is_shortener(url: str) -> bool:
    return _matches(host_of(url), SHORTENERS)


def is_noise(url: str) -> bool:
    h = host_of(url)
    return not h or _matches(h, NOISE)


def is_error_url(url: str) -> bool:
    """gplinks & co answer refusals with 200 + a link-error URL."""
    try:
        p = urlparse(url)
    except ValueError:
        return True
    return bool(ERROR_PATH_RE.search(p.path) or ERROR_QUERY_RE.search(p.query or ""))


def known_destination_score(url: str) -> int | None:
    h = host_of(url)
    for d, score in KNOWN_DESTINATIONS.items():
        if h == d or h.endswith("." + d):
            return score
    return None


def verdict(
    url: str,
    origin_host: str = "",
    visited_hosts: set[str] | None = None,
) -> tuple[bool, float, str]:
    """
    Judge a candidate destination.

    Returns (acceptable, confidence 0..1, reason).  `acceptable` False means
    never show this to the user as the answer.

    `visited_hosts` are hosts the flow merely passed *through* (ad blogs,
    interstitials).  A gate you walked through is never the prize — this is
    what stops "gplinks -> skrresults.com/page/2/" being reported as an
    answer.  A genuinely known file host still wins, in case a shortener
    legitimately lands on one mid-chain.
    """
    if not url or not url.startswith(("http://", "https://")):
        return False, 0.0, "not an http url"

    host = host_of(url)
    if not host:
        return False, 0.0, "no host"
    if origin_host and (host == origin_host or host.endswith("." + origin_host)):
        return False, 0.0, "same host as the shortener"
    if is_pagination(url):
        return False, 0.0, "pagination/archive link, not a destination"
    if visited_hosts and known_destination_score(url) is None:
        for vh in visited_hosts:
            if vh and (host == vh or host.endswith("." + vh)):
                return False, 0.05, "interstitial host we passed through"
    if is_error_url(url):
        return False, 0.0, "shortener error page"
    if is_noise(url):
        return False, 0.0, "ad/tracker/social domain"
    if ASSET_EXT_RE.search(urlparse(url).path):
        return False, 0.0, "static asset"
    if is_shortener(url):
        return False, 0.15, "another shortener — needs further resolution"

    score = known_destination_score(url)
    if score is not None:
        conf = score / 100
        if FILE_EXT_RE.search(urlparse(url).path):
            conf = min(0.99, conf + 0.03)
        return True, conf, "known file/stream host"

    if FILE_EXT_RE.search(urlparse(url).path):
        return True, 0.9, "direct file link"

    # An unknown host with a real path is a plausible destination.
    path = urlparse(url).path.strip("/")
    if path:
        return True, 0.62, "unknown host with a content path"
    # A bare domain with no path is what ad-blog interstitials look like
    # (e.g. gplinks -> https://skrresults.com). Never accept it as the answer.
    return False, 0.1, "bare domain, no path — looks like an ad interstitial"


def pick_best(
    urls: list[str],
    origin_host: str = "",
    visited_hosts: set[str] | None = None,
) -> tuple[str | None, float]:
    """Choose the most credible destination out of several candidates."""
    scored: list[tuple[float, int, str]] = []
    for u in urls:
        ok, conf, _ = verdict(u, origin_host, visited_hosts)
        if ok:
            # Prefer shorter URLs as a tie-break (less tracking cruft).
            scored.append((conf, -len(u), u))
    if not scored:
        return None, 0.0
    scored.sort(reverse=True)
    best = scored[0]
    return best[2], best[0]
