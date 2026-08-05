# Telegram alerts 24/7 (PC can be off)

Alerts run on **Render** (cloud), not on your laptop. Keep the site deployed and set env vars.

## 1. Create a bot

1. Open Telegram → search **@BotFather**
2. Send `/newbot` → follow prompts → copy the **bot token**  
   Example: `7123456789:AAH...`

## 2. Get your chat ID

1. Open your new bot → tap **Start** → send `hi`
2. Either:
   - Use the dashboard **Find chat ID** button (local or online), or
   - Open: `https://api.telegram.org/bot<TOKEN>/getUpdates`  
     Look for `"chat":{"id": 123456789`

## 3. Put secrets on Render (required for 24/7)

1. https://dashboard.render.com → **pro-trader** → **Environment**
2. Add:

| Key | Value |
|-----|--------|
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | your numeric chat id |
| `TELEGRAM_ENABLED` | `true` |
| `TRADE_ALERTS_ENABLED` | `true` |
| `ALERT_PRECISION_MODE` | `true` |
| `ALERT_MIN_GRADE` | `B` |

3. **Save** → **Manual Deploy**

## 4. Test

After deploy:

```text
https://pro-trader.onrender.com/health
```

Should show `"telegram_24_7": true` and `"server_push_ready": true`.

Or open the site → **More details → Alert settings → Test Telegram**.

## Precision rules (what you get)

| Alert | When it fires |
|-------|----------------|
| **BUY / SELL** | Signal flips, conf ≥ 72%, grade ≥ B, 1H+4H aligned |
| **ENTRY** | Plan is ENTER_LONG/SHORT **and** price is at entry |
| **EXIT** | SL or TP3 hit (immediate) |
| **PARTIAL** | Off by default (less noise) |
| **News** | Only high/immediate urgency + high conf |

Cooldowns: signal 1h · entry 4h · exit 30m (same alert won’t spam).

## Keepalive

`render.yaml` pings `/health` every 10 minutes so free tier doesn’t sleep forever.  
For best reliability, upgrade Render to a paid always-on instance.

## Local testing (optional)

Save token + chat ID on the dashboard once; they go to `trade_alerts_config.json` (gitignored).  
**24/7 requires Render env vars** — local only works while your PC is on.
