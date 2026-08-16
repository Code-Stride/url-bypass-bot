"""
bot.py — Telegram URL-unshortener / bypass bot.

Runs in one of two modes, auto-detected from environment:

  * Webhook mode  — when WEBHOOK_URL (or RAILWAY_PUBLIC_DOMAIN) is set.
                    Recommended on Railway / Render / any host with a public
                    URL. Binds to 0.0.0.0:$PORT.
  * Polling mode  — otherwise. Good for local dev / a bare VPS.

Usage:
    export BOT_TOKEN="123456:ABC-DEF..."   # from @BotFather
    python bot.py
"""

import asyncio
import logging
import os
import re

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from unshortener import unshorten

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("url-bypass-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# URL detection (handles bare URLs and trailing punctuation).
URL_RE = re.compile(r"https?://[^\s<>()\"]+[^\s<>()\".!?,;:]")

MAX_LINKS_PER_MESSAGE = 5

WELCOME = (
    "👋 <b>BypassBot</b>\n\n"
    "I unshorten any shortened / ad-protected link and give you the real "
    "destination — no ads, no captcha, no waiting.\n\n"
    "👉 Just send me a link, or use:\n"
    "<code>/unshort https://example.short/xyz</code>\n\n"
    "I support classic shorteners (bit.ly, is.gd, tinyurl...) and "
    "link-protection services (linkszilla, mobilejsr, adf.ly-style, etc.)."
)


def format_results(url: str, results: list[str]) -> str:
    if not results:
        return (
            f"❌ Could not resolve:\n{url}\n\n"
            "It may be dead, geo-blocked, or needs JavaScript."
        )
    if len(results) == 1:
        return f"✅ <b>Unshortened:</b>\n\n{results[0]}"
    lines = [f"✅ <b>Found {len(results)} links:</b>", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r}")
    return "\n".join(lines)


async def _resolve_and_reply(url: str, reply) -> None:
    try:
        # resolve() is blocking network I/O — run it off the event loop.
        res = await asyncio.to_thread(unshorten, url)
    except Exception as exc:  # noqa: BLE001
        logger.exception("resolve failed for %s", url)
        await reply(f"⚠️ Something went wrong resolving:\n{url}\n\nError: {exc}")
        return

    text = format_results(url, res["results"])
    await reply(text, parse_mode="HTML", disable_web_page_preview=False)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(WELCOME)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(WELCOME)


async def unshort_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    urls = URL_RE.findall(text)
    if not urls:
        await update.message.reply_html(
            "⚠️ Please include a URL, e.g.\n<code>/unshort https://bit.ly/xxxx</code>"
        )
        return
    status = await update.message.reply_text(f"🔍 Resolving <code>{urls[0]}</code>…", parse_mode="HTML")
    await _resolve_and_reply(urls[0], status.edit_text)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-detect any URLs the user pastes."""
    text = update.message.text or ""
    urls = URL_RE.findall(text)
    if not urls:
        return  # not our business; ignore

    for url in urls[:MAX_LINKS_PER_MESSAGE]:
        status = await update.message.reply_text(f"🔍 Resolving <code>{url}</code>…", parse_mode="HTML")
        await _resolve_and_reply(url, status.edit_text)


def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("unshort", unshort_cmd))
    app.add_handler(CommandHandler("u", unshort_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


def _webhook_url_from_env() -> str | None:
    explicit = os.environ.get("WEBHOOK_URL", "").strip()
    if explicit:
        return explicit
    domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if domain:
        return f"https://{domain}"
    return None


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "Set BOT_TOKEN first:\n"
            "  export BOT_TOKEN='123456:ABC-DEF...'\n"
            "Get one from @BotFather on Telegram."
        )

    app = build_app()
    webhook_url = _webhook_url_from_env()

    if webhook_url:
        port = int(os.environ.get("PORT", "8443"))
        # Use the token as the secret path so only Telegram can reach it.
        path = BOT_TOKEN
        full_webhook = f"{webhook_url.rstrip('/')}/{path}"
        logger.info("Starting webhook mode on 0.0.0.0:%s -> %s", port, full_webhook)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=path,
            webhook_url=full_webhook,
            drop_pending_updates=True,
        )
    else:
        logger.info("Starting polling mode…")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
