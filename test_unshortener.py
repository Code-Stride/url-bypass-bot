"""Quick CLI test of the unshortener engine (no Telegram needed)."""
import sys

from unshortener import unshorten

SAMPLES = [
    "https://secure.linkszilla.top/view/jknmzhNyFZ",
    "https://mobilejsr.com/view/S1cE9SKSnr",
]


def main() -> None:
    urls = sys.argv[1:] or SAMPLES
    for u in urls:
        r = unshorten(u)
        print("INPUT :", u)
        print("OK    :", r["ok"], "| error:", r["error"])
        for x in r["results"]:
            print("   ->", x)
        print()


if __name__ == "__main__":
    main()
