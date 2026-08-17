"""
Offline test suite.  Run:  python -m tests.test_all

The classifier tests encode the real-world failures we observed live:
skrresults.com (ad blog) and gplinks link-error pages must never be reported
as an answer, while devuploads.com must be recognised as a true destination.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.classify import is_error_url, is_shortener, pick_best, verdict  # noqa: E402
from app.models import Result  # noqa: E402
from app.resolver import resolve  # noqa: E402
from tests import mock_shortener  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def test_classifier() -> None:
    ok, conf, why = verdict("https://skrresults.com", "gplinks.co")
    check("ad blog (bare domain) rejected", not ok, why)

    ok, _, why = verdict(
        "https://gplinks.com/link-error?alias=ZkVCbbry&error_code=not_enough_steps"
    )
    check("link-error page rejected", not ok, why)
    check("is_error_url catches error_code",
          is_error_url("https://x.com/a?error_code=not_enough_steps"))

    ok, conf, why = verdict("https://devuploads.com/7h77e7ikjhxj", "gplinks.co")
    check("devuploads accepted with high confidence", ok and conf >= 0.9,
          f"{conf} {why}")

    ok, _, _ = verdict("https://gplinks.co/ZkVCbbry")
    check("shortener not a final answer", not ok)
    check("gplinks detected as shortener", is_shortener("https://gplinks.co/x"))
    check("liteshort detected as shortener", is_shortener("https://liteshort.com/al1t"))

    ok, _, _ = verdict("https://scripts.clarity.ms/tag.js")
    check("tracker rejected", not ok)

    ok, conf, _ = verdict("https://cdn.example.org/movie.mkv")
    check("direct file link accepted", ok and conf >= 0.9, str(conf))

    best, conf = pick_best(
        ["https://skrresults.com", "https://devuploads.com/7h77e7ikjhxj",
         "https://scripts.clarity.ms/t.js"],
        "gplinks.co",
    )
    check("pick_best chooses the real file host",
          best == "https://devuploads.com/7h77e7ikjhxj", str(best))


def test_live_regressions() -> None:
    """The exact wrong answers produced against the live gplinks link."""
    visited = {"skrresults.com"}

    ok, _, why = verdict("https://skrresults.com/page/2/", "gplinks.co", visited)
    check("wordpress pagination rejected", not ok, why)

    ok, _, why = verdict("https://skrresults.com/some-article/", "gplinks.co", visited)
    check("interstitial host we walked through rejected", not ok, why)

    # …but a real file host reached mid-chain is still accepted.
    ok, conf, _ = verdict(
        "https://devuploads.com/7h77e7ikjhxj", "gplinks.co", visited
    )
    check("true destination still accepted", ok and conf >= 0.9, str(conf))

    best, _ = pick_best(
        ["https://skrresults.com/page/2/", "https://skrresults.com/x/",
         "https://devuploads.com/7h77e7ikjhxj"],
        "gplinks.co", visited,
    )
    check("pick_best skips interstitial pages",
          best == "https://devuploads.com/7h77e7ikjhxj", str(best))

    from app.engines.browser import AVOID_TEXT_RE
    check("'next' is not clicked", bool(AVOID_TEXT_RE.match("Next")))
    check("'Continue' is still clicked", not AVOID_TEXT_RE.match("Continue"))
    check("'Get Link' is still clicked", not AVOID_TEXT_RE.match("Get Link"))


def test_cookies() -> None:
    from app import cookies as cj

    netscape = (
        "# Netscape HTTP Cookie File\n"
        ".gplinks.co\tTRUE\t/\tTRUE\t1900000000\tAppSession\tabc123\n"
        "#HttpOnly_.google.com\tTRUE\t/\tTRUE\t1900000000\tNID\txyz\n"
    )
    got = cj.parse(netscape)
    check("netscape cookies parsed", len(got) == 2, str([c["name"] for c in got]))
    check("httponly line kept", any(c["name"] == "NID" for c in got))

    js = json.dumps([{"name": "cf_clearance", "value": "v1",
                      "domain": ".gplinks.co", "path": "/", "secure": True,
                      "expirationDate": 1900000000}])
    got = cj.parse(js)
    check("json cookies parsed",
          got and got[0]["name"] == "cf_clearance"
          and got[0]["domain"] == ".gplinks.co", str(got))

    storage = json.dumps({"cookies": [
        {"name": "a", "value": "1", "domain": "x.com", "path": "/"}]})
    check("playwright storage_state parsed", len(cj.parse(storage)) == 1)

    hdr = cj.parse("PHPSESSID=abc; lid=ZkVCbbry", "gplinks.co")
    check("header string parsed",
          len(hdr) == 2 and hdr[0]["domain"] == ".gplinks.co", str(hdr))

    picked = cj.for_domain(cj.parse(netscape), "https://www.gplinks.co/x")
    check("for_domain filters by host", picked == {"AppSession": "abc123"},
          str(picked))

    check("garbage ignored", cj.parse("!!! not cookies !!!") == [])
    check("cookie values never logged",
          "abc123" not in cj.summarise(cj.parse(netscape)))


def test_result_model() -> None:
    r = Result(input="u")
    r.log("navigate", "x")
    r.succeed("https://devuploads.com/a", "browser", 0.95)
    d = r.to_dict(verbose=True)
    check("result serialises", d["ok"] and d["url"].endswith("/a") and d["steps"])


def test_http_engine(port: int) -> None:
    """Plain redirect chains must resolve without a browser."""
    base = f"http://127.0.0.1:{port}"
    r = asyncio.run(resolve(f"{base}/al1t", use_browser=False))
    # The classic flow ends at a gate (#go-link) -> http engine must NOT lie.
    check("http engine refuses to guess at a gate",
          (not r.ok) or r.url == mock_shortener.DESTINATION,
          f"ok={r.ok} url={r.url} err={r.error}")


def test_no_false_positive(port: int) -> None:
    """Even when everything fails, we must never return the ad blog."""
    base = f"http://127.0.0.1:{port}"
    r = asyncio.run(resolve(f"{base}/c/ZkVCbbry", use_browser=False))
    bad = r.url and ("blog" in r.url or "link-error" in r.url)
    check("never returns ad blog / error page as the answer", not bad,
          f"url={r.url} err={r.error}")


def main() -> int:
    srv, port = mock_shortener.start()
    try:
        test_classifier()
        test_live_regressions()
        test_cookies()
        test_result_model()
        test_http_engine(port)
        test_no_false_positive(port)
    finally:
        srv.shutdown()
    print()
    if FAILS:
        print(f"{len(FAILS)} failed: {', '.join(FAILS)}")
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
