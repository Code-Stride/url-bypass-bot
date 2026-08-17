"""Telegram bot: paste a link, get the destination."""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app import config
from app.resolver import resolve_with_timeout

logger = logging.getLogger("bypass.bot")

URL_RE = re.compile(r"https?://[^\s<>()\"]+[^\s<>()\".!?,;:]")
MAX_LINKS = 3

WELCOME = (
    "👋 <b>URL Bypass Bot</b>\n\n"
    "Send me any shortened or ad-locked link and I'll return the real "
    "destination.\n\n"
    "I open the link in a real browser and complete the ad steps, countdowns "
    "and Cloudflare checks for you — so it works on <b>gplinks</b>, "
    "<b>liteshort</b>, adrinolinks, adf.ly, linkvertise, bit.ly and more.\n\n"
    "Commands:\n"
    "<code>/bypass &lt;url&gt;</code> — resolve a link\n"
    "<code>/details</code> — show how the last link was solved\n\n"
    "⏳ Ad-locked links can take 30–60 seconds. That's the countdown, not me."
)


def _fmt(res) -> str:
    if not res.ok or not res.url:
        return (
            f"❌ <b>Could not resolve</b>\n<code>{res.input}</code>\n\n"
            f"{res.error or 'unknown error'}"
        )
    pct = round(res.confidence * 100)
    warn = "\n\n⚠️ Low confidence — please double-check." if res.confidence < 0.6 else ""
    return (
        f"✅ <b>Destination</b>\n{res.url}\n\n"
        f"<i>{pct}% confidence · {res.engine} · {res.elapsed:.0f}s</i>{warn}"
    )


async def _handle(url: str, message, context) -> None:
    status = await message.reply_html(f"🔍 Resolving…\n<code>{url}</code>")
    res = await resolve_with_timeout(url)
    context.chat_data["last"] = res
    try:
        await status.edit_text(
            _fmt(res), parse_mode=ParseMode.HTML, disable_web_page_preview=False
        )
    except Exception:  # noqa: BLE001
        await message.reply_html(_fmt(res))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(WELCOME)


async def bypass_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    urls = URL_RE.findall(update.message.text or "")
    if not urls:
        await update.message.reply_html(
            "⚠️ Include a link, e.g.\n<code>/bypass https://gplinks.co/ZkVCbbry</code>"
        )
        return
    await _handle(urls[0], update.message, context)


async def details_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    res = context.chat_data.get("last")
    if res is None:
        await update.message.reply_html("ℹ️ Send me a link first.")
        return
    lines = [f"🧾 <b>Steps for</b> <code>{res.input}</code>", ""]
    for s in res.steps[-25:]:
        line = f"• <b>{s.kind}</b> — {s.detail}"
        if s.url:
            line += f"\n  <code>{s.url[:90]}</code>"
        lines.append(line)
    if res.candidates:
        lines += ["", "<b>Candidates seen:</b>"]
        lines += [f"• {c}" for c in res.candidates[:8]]
    await update.message.reply_html("\n".join(lines)[:4000])


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    urls = URL_RE.findall(update.message.text or "")
    for url in urls[:MAX_LINKS]:
        await _handle(url, update.message, context)


def build_app() -> Application:
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler(["bypass", "b", "unshort", "u"], bypass_cmd))
    app.add_handler(CommandHandler("details", details_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app
