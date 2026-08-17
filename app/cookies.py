"""
cookies.py — load browser cookies and hand them to the engines.

Why cookies help (and where they do not)
----------------------------------------
Cookies let this server reuse a session that a *real* browser already
established, which fixes the "you look like a bot" class of block:

  ✅ Google / interstitial "unusual traffic" captchas — a solved session
     carries an abuse-exemption cookie, so the bounce stops.
  ✅ Shortener session state (AppSession, csrfToken, PHPSESSID, lid/pid/vid)
     — the flow starts already recognised instead of from scratch.
  ✅ Consent / age / region gates that only set a cookie.

  ❌ Cloudflare `cf_clearance` — bound to the IP **and** User-Agent that
     solved it. A cookie from your home browser will not work from Railway;
     Cloudflare re-challenges immediately. (We still send it: harmless, and
     it does work when the server and browser share an IP/proxy.)
  ❌ A hard origin block (gplinks' 403 from openresty) — that is refused
     before any cookie is read.

Accepted formats
----------------
  * Netscape `cookies.txt` (what most browser extensions export)
  * JSON list  [{"name":…,"value":…,"domain":…,"path":…}, …]
      (EditThisCookie / Cookie-Editor export)
  * Playwright `storage_state` JSON  {"cookies":[…]}
  * A raw header string  "a=1; b=2"  (needs `default_domain`)

Sources, merged in this order (later wins):
  COOKIES_FILE  -> path to any of the above
  COOKIES_JSON  -> the JSON/heder content inline
  per-request   -> the `cookies` field of /api/bypass
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlparse

Cookie = dict[str, Any]

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.!#$%&'*+^`|~]+$")


def _norm(
    name: str,
    value: str,
    domain: str = "",
    path: str = "/",
    secure: bool | None = None,
    expires: float | None = None,
) -> Cookie | None:
    name = (name or "").strip()
    if not name or not _SAFE_NAME_RE.match(name):
        return None
    domain = (domain or "").strip().lstrip(".")
    c: Cookie = {
        "name": name,
        "value": (value or "").strip(),
        "path": path or "/",
    }
    if domain:
        # Leading dot => valid for subdomains, which is what we usually want.
        c["domain"] = "." + domain
    if secure is not None:
        c["secure"] = bool(secure)
    if expires and expires > 0:
        c["expires"] = float(expires)
    return c


def parse_netscape(text: str) -> list[Cookie]:
    """Parse a Netscape cookies.txt file."""
    out: list[Cookie] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            # "#HttpOnly_.example.com\tTRUE\t/..." is still a cookie line.
            if not line.startswith("#HttpOnly_"):
                continue
            line = line[len("#HttpOnly_"):]
        parts = line.split("\t")
        if len(parts) < 7:
            parts = re.split(r"\s+", line)
        if len(parts) < 7:
            continue
        domain, _flag, path, secure, expires, name, value = parts[:7]
        try:
            exp = float(expires)
        except ValueError:
            exp = 0.0
        c = _norm(name, value, domain, path, secure.upper() == "TRUE", exp)
        if c:
            out.append(c)
    return out


def parse_json(data: Any) -> list[Cookie]:
    """Parse EditThisCookie / Cookie-Editor / Playwright storage_state JSON."""
    if isinstance(data, dict):
        data = data.get("cookies", [])
    if not isinstance(data, list):
        return []
    out: list[Cookie] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        exp = item.get("expires", item.get("expirationDate"))
        try:
            exp = float(exp) if exp else None
        except (TypeError, ValueError):
            exp = None
        c = _norm(
            item.get("name", ""),
            str(item.get("value", "")),
            item.get("domain", "") or "",
            item.get("path", "/") or "/",
            item.get("secure"),
            exp,
        )
        if c:
            out.append(c)
    return out


def parse_header(text: str, default_domain: str = "") -> list[Cookie]:
    """Parse a raw `Cookie:` header string."""
    out: list[Cookie] = []
    for pair in text.split(";"):
        if "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        c = _norm(name, value, default_domain)
        if c:
            out.append(c)
    return out


def parse(text: str, default_domain: str = "") -> list[Cookie]:
    """Auto-detect the format of a cookie blob."""
    text = (text or "").strip()
    if not text:
        return []
    if text[0] in "[{":
        try:
            return parse_json(json.loads(text))
        except ValueError:
            return []
    if "\t" in text or text.lstrip().startswith("#") or "\n" in text:
        got = parse_netscape(text)
        if got:
            return got
    return parse_header(text, default_domain)


def _read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def load(extra: str = "", url: str = "") -> list[Cookie]:
    """Collect cookies from the environment plus a per-request blob."""
    default_domain = ""
    if url:
        host = (urlparse(url).hostname or "").lower()
        default_domain = host[4:] if host.startswith("www.") else host

    blobs: list[str] = []
    path = (os.environ.get("COOKIES_FILE", "") or "").strip()
    if path:
        blobs.append(_read_file(path))
    inline = (os.environ.get("COOKIES_JSON", "") or "").strip()
    if inline:
        blobs.append(inline)
    if extra:
        blobs.append(extra)

    merged: dict[tuple[str, str], Cookie] = {}
    for blob in blobs:
        for c in parse(blob, default_domain):
            merged[(c["name"], c.get("domain", ""))] = c
    return list(merged.values())


def for_domain(cookies: list[Cookie], url: str) -> dict[str, str]:
    """The subset of `cookies` a plain HTTP client should send to `url`."""
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    out: dict[str, str] = {}
    for c in cookies:
        dom = (c.get("domain") or "").lstrip(".").lower()
        if not dom or host == dom or host.endswith("." + dom):
            out[c["name"]] = c["value"]
    return out


def summarise(cookies: list[Cookie]) -> str:
    """A short, non-secret description for logs and the step trail."""
    if not cookies:
        return "none"
    doms: dict[str, int] = {}
    for c in cookies:
        doms[(c.get("domain") or "?").lstrip(".")] = (
            doms.get((c.get("domain") or "?").lstrip("."), 0) + 1
        )
    top = sorted(doms.items(), key=lambda kv: -kv[1])[:4]
    return f"{len(cookies)} cookie(s): " + ", ".join(f"{d}×{n}" for d, n in top)
