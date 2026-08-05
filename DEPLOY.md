# Deploy Pro Trader Online

## Option 1 — Quick share (free, temporary URL)

Double-click **`start-online.bat`**

- Starts the app on port 5000
- Opens a **trycloudflare.com** public link
- **Keep the window open** while sharing — URL changes each restart

## Option 2 — Permanent hosting on Render (free)

Repo: **https://github.com/Rawlincoln/pro-trader**

### One-click Blueprint (recommended)

1. Push latest code (already on GitHub if you used `sync-github.ps1` / `deploy.ps1`):
   ```powershell
   .\deploy.ps1 -GitHubUser Rawlincoln
   ```
2. Open Blueprint deploy (creates/updates the service from `render.yaml`):
   **https://dashboard.render.com/blueprints/new?repo=https://github.com/Rawlincoln/pro-trader**
3. Click **Apply** — wait ~3–5 minutes for the first build.
4. App URL: **https://pro-trader.onrender.com**  
   Health: **https://pro-trader.onrender.com/health**

If you previously deleted the service or the name is taken, Render will still create it from the blueprint (`name: pro-trader` in `render.yaml`).

### Manual Web Service (if Blueprint fails)

- **New → Web Service** → connect `Rawlincoln/pro-trader`
- Runtime: Python
- Build: `pip install -r requirements-cloud.txt`
- Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
- Health check path: `/health`
### Myfxbook on Render (required for balance sync)

`config.json` is **not** deployed (gitignored). Set secrets in the Render dashboard:

1. Open **https://dashboard.render.com** → service **pro-trader**
2. **Environment** → **Add Environment Variable**

  | Key | Value |
  |-----|--------|
  | `MYFXBOOK_EMAIL` | your Myfxbook login email |
  | `MYFXBOOK_PASSWORD` | your Myfxbook password |
  | `MYFXBOOK_ACCOUNT_ID` | optional — e.g. `12098095` (auto-picks first account if omitted) |
  | `MYFXBOOK_AUTO_SYNC` | `true` |
  | `TELEGRAM_BOT_TOKEN` | from @BotFather (optional) |
  | `TELEGRAM_CHAT_ID` | your chat id (optional) |
  | `TELEGRAM_ENABLED` | `true` |
  | `TRADE_ALERTS_ENABLED` | `true` |

3. **Save Changes** → **Manual Deploy** → **Deploy latest commit**

Until env vars are set, auto-sync will show a setup hint instead of failing silently.

Free tier sleeps after ~15 min idle; first load may take ~30s.  
`render.yaml` includes a keepalive cron that pings `/health` every 12 minutes.

**Note:** XM trading agent (MetaTrader 5) only works on your local Windows PC, not on Render.

## Option 3 — Auto-sync to GitHub

Run **`start-sync.bat`** — auto-commits and pushes when you save files.

## LAN access (same Wi‑Fi)

While the app runs locally: `http://YOUR-PC-IP:5000`

## Local run

```powershell
cd C:\Users\MMghongo\eur-usd-trader
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5000/**
