# 🔗 URL Bypass Bot (Telegram)

A Telegram bot that **unshortens / bypasses any shortened or ad-protected link**
for free — no ads, no captcha, no waiting. Paste a link, get the real
destination(s) instantly.

It handles two kinds of links:

1. **Classic shorteners** — `bit.ly`, `tinyurl.com`, `is.gd`, `goo.gl`, `t.co`, `cutt.ly`, …
2. **Link-protection / "earn money" shorteners** — `linkszilla.top`, `mobilejsr.com`,
   `adf.ly`, `ouo.io`, `shrinkme.io`, `linkvertise.com`, `gplinks.co`, and many more
   (a large built-in list, easy to extend).

## How it works

The engine (`unshortener.py`) uses a layered strategy:

1. Follows HTTP redirects to the final landing page.
2. Handles `<meta http-equiv="refresh">` redirects.
3. Link-protection pages embed the *real* destination directly in their HTML
   (an `<a href>`, a hidden input, a JS variable, a `data-*` attribute). The
   bot extracts every candidate URL and filters out noise (ad networks,
   trackers, CDNs, the shortener's own pages).
4. If a candidate is itself a known shortener, it recurses one level deeper.
5. Returns all genuine destinations — mirror links included.

## Setup

### 1. Create the bot
- Open Telegram, message **@BotFather**.
- Send `/newbot`, pick a name and a username.
- Copy the **token** it gives you (looks like `123456789:AA...`).

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Run it
```bash
export BOT_TOKEN="123456789:AA...your_token..."
python bot.py
```

> Optionally copy `.env.example` to `.env` and source it.

### 4. Use it
Just send the bot a link (or use a command):

```
https://secure.linkszilla.top/view/jknmzhNyFZ
```

```
/unshort https://mobilejsr.com/view/S1cE9SKSnr
```

Commands: `/start`, `/help`, `/unshort <url>` (alias `/u <url>`).

## Example output

```
✅ Found 10 links:

1. https://dl.direct-cloud.top/d/Xy-dkAG
2. https://dl.uploadflix.com/7hamrutt1qiz
3. https://hubcloud.cx/drive/kszefbzhzcbfs8c
4. https://new3.gdflix.io/file/uEJOpA4qq9XmX7X
5. https://clicknupload.cam/rodovibqfn0b
6. https://gofile.io/d/rbCpIy
7. https://vikingfile.com/f/olIIcFVpYj
8. https://megaup.net/9814baf60c12b1669582d8caf3f441a9/
9. https://1fichier.com/?qfma73eg8cf8n3mtkqd3
10. https://multiup.io/download/ef3e2c07d611d437b677a8383b78d20a/
```

## Extending it

- **Add a shortener domain** → append it to `KNOWN_SHORTENERS` in `unshortener.py`.
- **Add a noise/ad domain to ignore** → append it to `NOISE_DOMAINS`.

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Telegram bot (commands + auto-detect pasted links). |
| `unshortener.py` | The resolution engine (library — usable without Telegram). |
| `test_unshortener.py` | Quick CLI test of the engine. |
| `requirements.txt` | Python dependencies. |

## Notes

- The engine works on **any** host that embeds the destination in plain HTML.
  Sites that build the link only via heavy JavaScript with anti-bot checks
  (e.g. some Cloudflare-protected pages) may not resolve — in those cases the
  bot reports it couldn't resolve the link.
- Run it on a VPS / always-on machine for 24/7 availability (a free tier of
  Render/Railway/Replit works too).
