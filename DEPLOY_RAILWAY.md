# Deploying to Railway (https://railway.app)

One service runs **both** the Telegram bot and the website/API — `server.py`
binds `0.0.0.0:$PORT`, serves the web UI at `/`, the JSON API at `/api/bypass`,
and registers the Telegram webhook at `/telegram/<BOT_TOKEN>` on startup.

## 1. Create the bot & get a token
- Message **@BotFather** on Telegram → `/newbot` → copy the token.
- Want the website only? Just skip the token (or set `ENABLE_BOT=0`).

## 2. Push this branch to GitHub
Already done — deploy from the branch `arena/01a00df5-url-bypass-bot`.

## 3. Deploy on Railway
1. https://railway.app → **New Project** → **Deploy from GitHub repo**.
2. Pick the repo, then **Settings → Source** → set the branch to
   `arena/01a00df5-url-bypass-bot`.
3. Railway reads `railway.json`: start command `python server.py`,
   health check `/healthz`. (The `Dockerfile` works too if you prefer it.)
4. **Settings → Networking → Generate Domain** — this is required, the bot
   uses that domain for its webhook.
5. **Variables** → add:

   | Name | Value | Required |
   |------|-------|----------|
   | `BOT_TOKEN` | `123456789:AA...` from BotFather | for the bot |
   | `BYPASS_MAX_WAIT` | `12` — max seconds to sit out a shortener countdown | optional |
   | `FLARESOLVERR_URL` | `http://flaresolverr.railway.internal:8191/v1` | optional, for Turnstile |
   | `ENABLE_BOT` | `0` to run website-only | optional |

   `PORT` and `RAILWAY_PUBLIC_DOMAIN` are injected automatically; the webhook
   URL is derived from them. Set `WEBHOOK_URL` only if you use a custom domain.

## 4. Verify
```bash
curl https://<your-domain>/healthz
# {"ok":true,"bot":true}

curl "https://<your-domain>/api/bypass?url=https://gplinks.co/ZkVCbbry"
```
Open `https://<your-domain>/` in a browser, and message your bot on Telegram.

## 5. Optional: FlareSolverr for the hardest Cloudflare pages
`curl_cffi` clears most Cloudflare challenges, but interactive Turnstile needs
a real browser. In the same Railway project:

1. **New → Docker Image** → `ghcr.io/flaresolverr/flaresolverr:latest`.
2. On the bot service set
   `FLARESOLVERR_URL=http://<flaresolverr-service>.railway.internal:8191/v1`.

FlareSolverr needs ~1 GB RAM; leave it off unless links start failing.

## Local run
```bash
pip install -r requirements.txt

# website + bot (bot polls when there's no public URL)
export BOT_TOKEN="123456789:AA..."
python server.py            # http://localhost:8080

# website only
ENABLE_BOT=0 python server.py

# bot only, no web
python bot.py
```

## Notes
- Railway's trial credit (~$5) runs out; the Hobby plan (~$5/mo) keeps it
  always-on.
- Health check `/healthz` returns 200 as soon as the web app is up, so a
  Telegram outage can't fail the deploy — the bot logs the error and the
  website keeps serving.
- Pushing to the linked branch redeploys automatically.
