# Deploy Pro Trader Online

## Option 1 — Quick share (free, temporary URL)

Double-click **`start-online.bat`**

- Starts the app on port 5000
- Opens a **trycloudflare.com** public link
- **Keep the window open** while sharing — URL changes each restart

## Option 2 — Permanent hosting on Render (free)

Same setup as **soccer-under-strategy** (which uses plain `gunicorn` + threads).

1. Push to GitHub:
   ```powershell
   .\deploy.ps1 -GitHubUser Rawlincoln
   ```
2. Open: **https://dashboard.render.com/blueprints/new?repo=https://github.com/Rawlincoln/pro-trader**
3. Click **Apply** — Render reads `render.yaml`
4. Wait ~3 min for build → **https://pro-trader.onrender.com**

If Blueprint fails, create manually:
- **New Web Service** → repo `Rawlincoln/pro-trader`
- Build: `pip install -r requirements-cloud.txt`
- Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
- Health check path: `/health`

Free tier sleeps after 15 min idle; first load may take ~30s.

**Note:** XM trading agent (MetaTrader 5) only works on your local Windows PC, not on Render.

## Option 3 — Auto-sync to GitHub

Run **`start-sync.bat`** — auto-commits and pushes when you save files.

## LAN access (same Wi‑Fi)

While the app runs locally: `http://YOUR-PC-IP:5000`