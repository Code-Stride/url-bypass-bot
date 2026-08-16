"""
setup_bot.py — Configure the bot's profile directly via the Telegram Bot API.

Sets (using stdlib only, no deps):
  * name            -> setMyName
  * about           -> setMyDescription
  * short description-> setMyShortDescription
  * bot menu commands -> setMyCommands

Every value can be overridden with an environment variable.

Usage:
    export BOT_TOKEN="123456:ABC-DEF..."
    python setup_bot.py
"""

import json
import os
import sys
import urllib.request

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

NAME = os.environ.get("BOT_NAME", "URL Bypass Bot")
DESCRIPTION = os.environ.get(
    "BOT_DESCRIPTION",
    "Unshorten any shortened or ad-protected link instantly — no ads, "
    "no captcha, no waiting. Just paste a link and I'll reply with the real "
    "destination link(s). Supports classic shorteners (bit.ly, is.gd, "
    "tinyurl, ...) and link-protection services (linkszilla, mobilejsr, "
    "adf.ly-style, and many more).",
)
SHORT_DESCRIPTION = os.environ.get(
    "BOT_SHORT_DESCRIPTION", "Unshorten any link for free"
)
COMMANDS = [
    ("start", "Start the bot"),
    ("help", "How to use this bot"),
    ("unshort", "Unshorten / bypass a link"),
]


def call(method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN first:  export BOT_TOKEN='123456:ABC...'")

    results = {
        "setMyName": call("setMyName", {"name": NAME}),
        "setMyDescription": call("setMyDescription", {"description": DESCRIPTION}),
        "setMyShortDescription": call(
            "setMyShortDescription", {"short_description": SHORT_DESCRIPTION}
        ),
        "setMyCommands": call(
            "setMyCommands",
            {
                "commands": [
                    {"command": c, "description": d} for c, d in COMMANDS
                ]
            },
        ),
    }

    ok_all = True
    for method, r in results.items():
        ok = bool(r.get("ok"))
        ok_all = ok_all and ok
        detail = r.get("result") or r.get("description") or ""
        print(f"{method:24s} ok={ok}  {detail}")

    print("\nCommands set:", [c for c, _ in COMMANDS])
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
