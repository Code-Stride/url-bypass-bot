"""
bypass_cli.py — resolve links from the command line (no Telegram needed).

    python bypass_cli.py https://liteshort.com/al1t https://gplinks.co/ZkVCbbry
    python bypass_cli.py --all https://gplinks.co/ZkVCbbry
    python bypass_cli.py --debug https://liteshort.com/al1t

Env knobs:
    FLARESOLVERR_URL=http://localhost:8191/v1   use a headless browser for
                                                Cloudflare Turnstile pages
    BYPASS_MAX_WAIT=12                          cap on countdown waiting
"""

from __future__ import annotations

import sys

import adlinkfly
from httpclient import Client
from unshortener import pick_best, unshorten


def main() -> int:
    args = [a for a in sys.argv[1:]]
    show_all = "--all" in args
    debug = "--debug" in args
    urls = [a for a in args if not a.startswith("--")]
    if not urls:
        print(__doc__)
        return 2

    for url in urls:
        print(f"\n=== {url}")
        if debug:
            c = Client()
            r = c.get(url)
            if r is None:
                print("  fetch: FAILED (network blocked or host down)")
            else:
                print(f"  fetch: {r.status_code} via {r.backend} ({len(r.text)} bytes)")
            if adlinkfly.is_adlinkfly_host(url):
                print("  route: AdLinkFly flow (visitor id -> #go-link -> /links/go)")

        res = unshorten(url)
        if not res["ok"]:
            print(f"  ERROR: {res['error']}")
            continue
        results = res["results"]
        if show_all or len(results) == 1:
            for i, r in enumerate(results, 1):
                print(f"  {i}. {r}")
        else:
            print(f"  best of {len(results)}: {pick_best(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
