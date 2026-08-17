"""
Offline tests for the AdLinkFly (gplinks / liteshort) bypass and for the
Cloudflare-challenge detection.

Run:  python -m tests.test_adlinkfly
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import adlinkfly  # noqa: E402
from httpclient import Client, Response, is_cloudflare_challenge  # noqa: E402
from tests import mock_adlinkfly  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILURES.append(name)


def test_cf_detection() -> None:
    chal = Response("u", 403, {"server": "cloudflare"},
                    "<title>Just a moment...</title> cdn-cgi/challenge-platform")
    ok = Response("u", 200, {"server": "cloudflare"}, "<html>real content</html>")
    mitigated = Response("u", 200, {"cf-mitigated": "challenge"}, "x")
    check("cloudflare 403 challenge detected", is_cloudflare_challenge(chal))
    check("cf-mitigated header detected", is_cloudflare_challenge(mitigated))
    check("normal page not flagged", not is_cloudflare_challenge(ok))


def test_field_extraction() -> None:
    html = """<form id="go-link" method="post" action="/links/go">
      <input type="hidden" name="_token" value="abc">
      <input type="hidden" name="ad_form_data" value="ZGF0YQ==">
    </form>"""
    f = adlinkfly.extract_go_link_fields(html)
    check("go-link fields extracted", f.get("_token") == "abc"
          and f.get("ad_form_data") == "ZGF0YQ==", str(f))

    js = 'var _token = "tkn9"; var ad_form_data = "payload1";'
    f2 = adlinkfly.extract_go_link_fields(js)
    check("js-built fields extracted", f2.get("_token") == "tkn9", str(f2))


def test_host_matching() -> None:
    check("gplinks recognised", adlinkfly.is_adlinkfly_host("https://gplinks.co/ZkVCbbry"))
    check("liteshort recognised", adlinkfly.is_adlinkfly_host("https://liteshort.com/al1t"))
    check("liteshort subdomain recognised",
          adlinkfly.is_adlinkfly_host("https://link.liteshort.com/abc"))
    check("random host not matched", not adlinkfly.is_adlinkfly_host("https://example.com/x"))


def test_interstitial_rebuild() -> None:
    import base64

    lid = base64.urlsafe_b64encode(b"gPxzXmyD").decode().rstrip("=")
    pid = base64.urlsafe_b64encode(b"194570").decode().rstrip("=")
    url = f"https://powergam.online/article/?lid={lid}&pid={pid}&pages=2&vid=993862"
    out = adlinkfly.interstitial_target(url, {}, "gplinks.co")
    check("interstitial rebuilt to real gplinks url",
          out == "https://gplinks.co/gPxzXmyD?pid=194570&vid=993862", str(out))


def test_cookie_interstitial_params() -> None:
    """The live gplinks/skrresults variant: raw values in cookies, no query."""
    cookies = {
        "lid": "ZkVCbbry", "pid": "1093510",
        "vid": "MTA0NjUxODg5NQ", "pages": "5",
    }
    out = adlinkfly.interstitial_targets("https://skrresults.com/", cookies, "gplinks.co")
    expected = "https://gplinks.co/ZkVCbbry?pid=1093510&vid=MTA0NjUxODg5NQ"
    check("cookie lid kept raw (not base64-decoded)", out and out[0] == expected, str(out))


def test_full_flow(port: int) -> None:
    base = f"http://127.0.0.1:{port}"
    adlinkfly.ADLINKFLY_HOSTS.add("127.0.0.1")

    # plain flow:  /<code> -> ?vid= -> POST /links/go
    dest = adlinkfly.bypass(f"{base}/al1t", Client())
    check("plain short link bypassed", dest == mock_adlinkfly.DESTINATION, str(dest))

    # interstitial flow: /i/<code> -> ad blog (lid/pid/vid) -> real page
    dest2 = adlinkfly.bypass(f"{base}/i/ZkVCbbry", Client())
    check("interstitial short link bypassed", dest2 == mock_adlinkfly.DESTINATION, str(dest2))

    # live-style flow: 302 to an ad blog with NO params; the blog sets
    # lid/pid/vid cookies which rebuild the real gplinks URL.
    dest3 = adlinkfly.bypass(f"{base}/c/ZkVCbbry", Client())
    check("cookie-based interstitial bypassed", dest3 == mock_adlinkfly.DESTINATION, str(dest3))

    # through the public engine
    import unshortener

    res = unshortener.unshorten(f"{base}/al1t")
    check("engine returns destination", res["ok"] and res["results"][0] == mock_adlinkfly.DESTINATION,
          str(res))


def main() -> int:
    os.environ.setdefault("BYPASS_MAX_WAIT", "1")
    srv, port = mock_adlinkfly.start()
    try:
        test_cf_detection()
        test_field_extraction()
        test_host_matching()
        test_interstitial_rebuild()
        test_cookie_interstitial_params()
        test_full_flow(port)
    finally:
        srv.shutdown()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} test(s) failed: {', '.join(FAILURES)}")
        return 1
    print("all tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
