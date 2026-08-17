# 🔗 URL Bypass — Telegram bot + web app

Paste a shortened or ad-locked link, get the real destination.
Works on **gplinks**, **liteshort**, adrinolinks, adf.ly, linkvertise,
bit.ly and friends.

Rebuilt from scratch around one lesson learned the hard way: these sites
validate progress **server-side**, so replaying HTTP requests cannot win.
A real browser does the real flow instead.

---

## Why a browser (and why the old approach failed)

The previous version tried to replay the AdLinkFly protocol over HTTP:
read the `#go-link` form, forge the `step_count` cookie, POST `/links/go`.
Tested against the live site, gplinks answered:

```
https://gplinks.com/link-error?alias=ZkVCbbry&error_code=not_enough_steps
```

The ad-step counter is tracked **on the server** and advanced by JavaScript
on the ad pages. Faking cookies does nothing. Worse, the old code then
reported the ad blog it landed on (`https://skrresults.com`) as if it were
the answer — a confidently wrong result, the worst kind.

So the engine order is now:

| # | Engine | Handles | Behaviour at a gate |
|---|--------|---------|---------------------|
| 1 | **HTTP** (`curl_cffi`, Chrome TLS fingerprint) | plain 30x chains, meta-refresh, embedded links | **stops and admits it** — never guesses |
| 2 | **Browser** (Playwright Chromium) | countdowns, "Continue"/"Get Link" steps, ad-step counters, Cloudflare JS challenges | performs the actual clicks |

Every answer from either engine is re-checked by `app/classify.py` before a
user ever sees it.

## Accuracy, honestly

There is no such thing as a 100% bypass, and anything claiming it is lying:
these sites change their flow, add captchas, geo-block, and some links are
simply dead. What this project guarantees instead is **no confident lies**:

- ad blogs / bare domains (`https://skrresults.com`) → rejected
- error pages (`error_code=not_enough_steps`) → rejected
- trackers, CDNs, social, ad networks → rejected
- another shortener → resolved again, not returned
- every answer carries a **confidence score**, and the bot warns below 60%

If it cannot solve a link it says so, with the steps it took. That is far
more useful than a wrong URL.

## Run it

```bash
pip install -r requirements.txt
python -m playwright install chromium     # the accurate engine

export BOT_TOKEN="123456:AA..."           # optional; omit for web-only
python server.py                          # http://localhost:8080
```

Command line:

```bash
python cli.py https://gplinks.co/ZkVCbbry
python cli.py --verbose --no-browser https://bit.ly/xyz
```

## API

| Route | Purpose |
|---|---|
| `GET /` | web UI |
| `POST /api/bypass` | `{"url": "...", "verbose": true}` |
| `GET /api/bypass?url=…&verbose=true` | same, as GET |
| `GET /healthz` | health + whether the browser is live |
| `POST /telegram/<BOT_TOKEN>` | Telegram webhook (auto-registered) |

```json
{
  "ok": true,
  "url": "https://devuploads.com/7h77e7ikjhxj",
  "engine": "browser",
  "confidence": 0.96,
  "elapsed": 41.2
}
```

`verbose=true` adds `steps` — every navigation, wait and click — so a
failure can be diagnosed instead of guessed at.

## Deploy to Railway

The `Dockerfile` is based on Microsoft's Playwright image, so Chromium and
all its system libraries are present — this is what makes the browser engine
work in production.

1. **New Project → Deploy from GitHub repo**, branch
   `arena/01a00df5-url-bypass-bot`.
2. Railway reads `railway.json` (Docker build, health check `/healthz`).
3. **Settings → Networking → Generate Domain** (needed for the webhook).
4. **Variables → `BOT_TOKEN`**. That's it.

Give the service ~1 GB RAM; each concurrent browser costs ~250 MB
(`BROWSER_CONCURRENCY`, default 2).

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `BOT_TOKEN` | – | Telegram token; omit for web-only |
| `ENABLE_BOT` | `1` | `0` = website only |
| `USE_BROWSER` | `1` | `0` = HTTP only (much less accurate) |
| `BROWSER_CONCURRENCY` | `2` | parallel browser resolutions |
| `RESOLVE_TIMEOUT` | `180` | total budget per link (s) |
| `BROWSER_TIMEOUT` | `150` | browser budget per link (s) |

## Telegram commands

`/start` · `/bypass <url>` (also `/b`, `/u`) · `/details` — shows exactly how
the last link was solved.

## Layout

| Path | Purpose |
|---|---|
| `app/classify.py` | decides destination vs shortener vs noise — the accuracy backbone |
| `app/resolver.py` | runs the engines, re-verifies, follows shortener chains |
| `app/engines/http.py` | fast path; bails out at gates |
| `app/engines/browser.py` | Playwright engine that does the real flow |
| `app/web/api.py`, `app/web/ui.py` | FastAPI app and the UI |
| `app/bot.py` | Telegram bot |
| `server.py`, `cli.py` | entrypoints |
| `tests/` | offline suite + a mock replicating the live gplinks behaviour |

## Tests

```bash
python -m tests.test_all
```

The mock in `tests/mock_shortener.py` reproduces the real traces: a
parameter-less redirect to an ad blog, cookie-planted `lid/pid/vid`,
**server-side** step validation, and the `not_enough_steps` refusal — so the
regressions that broke the old build are locked in.

## Legal note

Intended for reaching content you are entitled to access without hostile
interstitials. Link shorteners fund creators through ads; bypassing them
removes that income. Use responsibly and respect each site's terms.
