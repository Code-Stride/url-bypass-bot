"""
Command-line resolver.

    python cli.py https://gplinks.co/ZkVCbbry
    python cli.py --verbose --no-browser https://bit.ly/xyz
"""

from __future__ import annotations

import asyncio
import sys

from app.resolver import resolve_with_timeout


async def run(urls: list[str], verbose: bool, browser: bool) -> int:
    rc = 0
    for u in urls:
        print(f"\n=== {u}")
        res = await resolve_with_timeout(u, use_browser=browser)
        if res.ok:
            print(f"  -> {res.url}")
            print(f"     {round(res.confidence * 100)}% · {res.engine} · {res.elapsed:.1f}s")
        else:
            rc = 1
            print(f"  FAILED: {res.error}")
        if verbose:
            for s in res.steps:
                print(f"     [{s.kind}] {s.detail} {s.url}")
    if sys.platform != "win32":
        pass
    return rc


def main() -> int:
    args = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args
    browser = "--no-browser" not in args
    urls = [a for a in args if not a.startswith("-")]
    if not urls:
        print(__doc__)
        return 2
    return asyncio.run(run(urls, verbose, browser))


if __name__ == "__main__":
    raise SystemExit(main())
