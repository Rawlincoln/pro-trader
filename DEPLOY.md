# Deploy Pro Trader Online

## Option 1 — Quick share (free, temporary URL)

Double-click **`start-online.bat`**

- Starts the app on port 5000
- Opens a **trycloudflare.com** public link
- **Keep the window open** while sharing — URL changes each restart

## Option 2 — Permanent hosting on Render (free)

1. Push this folder to GitHub:
   ```powershell
   .\deploy.ps1 -GitHubUser Rawlincoln
   ```
2. Go to [render.com/blueprints](https://dashboard.render.com/blueprints) → **New Blueprint Instance**
3. Connect **Rawlincoln/pro-trader**
4. Render reads `render.yaml` and deploys automatically
5. You get a permanent `https://pro-trader.onrender.com` URL

Free tier sleeps after 15 min idle; first load may take ~30s.

**Note:** XM trading agent (MetaTrader 5) only works on your local Windows PC, not on Render.

## Option 3 — Auto-sync to GitHub

Run **`start-sync.bat`** — auto-commits and pushes when you save files.

## LAN access (same Wi‑Fi)

While the app runs locally: `http://YOUR-PC-IP:5000`