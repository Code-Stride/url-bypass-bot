# Deploying to Railway (https://railway.app)

The bot auto-detects Railway and runs in **webhook mode** — no code changes
needed, just these steps.

## 1. Create the bot & get a token
- Message **@BotFather** on Telegram → `/newbot` → copy the token.

## 2. Put your code on GitHub
- Create a repo and push the contents of this folder
  (`bot.py`, `unshortener.py`, `requirements.txt`, `railway.json`).

## 3. Deploy on Railway
1. Go to https://railway.app → **New Project** → **Deploy from GitHub repo**.
2. Pick your repo. Railway auto-detects Python via `requirements.txt`
   (or the `Dockerfile` if you prefer) and uses the `startCommand` from
   `railway.json`.
3. Open your service → **Variables** → add:
   | Name | Value |
   |------|-------|
   | `BOT_TOKEN` | `123456789:AA...` (from BotFather) |

   That's it — `RAILWAY_PUBLIC_DOMAIN` and `PORT` are injected automatically,
   and the bot sets its own Telegram webhook on startup.

## 4. Test it
- Open your service → **Settings → Networking → Generate Domain** (if not already present).
- Message your bot on Telegram. Paste any link → it replies with the real URL(s).

## Local testing (optional, polling mode)
```bash
pip install -r requirements.txt
export BOT_TOKEN="123456789:AA..."
python bot.py
```

## Notes
- Railway's free trial comes with a credit allowance (~$5). After it's used
  up you'll need the Hobby plan (~$5/mo) to keep it always-on.
- Webhook mode is the right choice here: no sleep issues, and the port
  binding keeps Railway's health checks happy.
- To change anything later: `railway up` or just push to the linked GitHub
  branch and it redeploys automatically.
