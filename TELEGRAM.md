# Telegram alerts 24/7 (PC can be off)

Alerts run on **Render** (cloud), not on your laptop. Keep the site deployed and set env vars.

## 1. Create a bot

1. Open Telegram → search **@BotFather**
2. Send `/newbot` → follow prompts → copy the **bot token**  
   Example: `7123456789:AAH...`

## 2. Get your personal chat ID

1. Open your bot → tap **Start** → send `hi`
2. Either:
   - Use the dashboard **Find chat ID** button, or
   - Open: `https://api.telegram.org/bot<TOKEN>/getUpdates`  
     Look for `"chat":{"id": 123456789`

## 3. Paid channel (monetization — recommended)

This is the best way to monetize: bot posts **full precision alerts** into a **paid channel**. Subscribers pay Telegram (Stars) or you sell invite links.

### Create the channel

1. Telegram → pencil / menu → **New Channel**  
   Name example: `Laibon Pro Signals`
2. Make it **Private**
3. **Add admin** → search `@LaibonProTraderbot` → promote as admin with **Post Messages** enabled  
   (bot must be admin or it cannot post)
4. Post any message in the channel (so Telegram registers activity)

### Get the channel ID

Channel IDs are usually negative, like `-1001234567890`.

**Option A — BotFather / getUpdates**  
1. Forward a channel post to `@userinfobot` or `@RawDataBot`, **or**  
2. After the bot is admin, post in the channel, then open:  
   `https://api.telegram.org/bot<TOKEN>/getUpdates`  
   Look for `"chat":{"id": -100…, "title":"…", "type":"channel"}`

**Option B — Dashboard**  
Use **Find chat ID** after the bot is admin and something was posted / the bot can see updates.

### Charge money (Stars subscription)

1. Open your channel → **Manage channel** → **Invite links**  
2. Create a link → enable **Require monthly fee** (Telegram Stars)  
3. Share that link as your paid product  

Typical signal pricing: **$29–79 / month** equivalent in Stars (adjust after you have a public track record).

**Or** sell externally (Stripe/Gumroad) and send a private invite link after payment.

### Free teaser (optional)

Create a second **public** channel for delayed samples / daily audit only.  
Keep live ENTRY/SFP alerts only on the paid channel.

## 4. Put secrets on Render (required for 24/7)

1. https://dashboard.render.com → **pro-trader** → **Environment**
2. Add:

| Key | Value |
|-----|--------|
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | your personal chat id (optional if channel-only) |
| `TELEGRAM_CHANNEL_ID` | paid channel id (`-100…`) |
| `TELEGRAM_ENABLED` | `true` |
| `TRADE_ALERTS_ENABLED` | `true` |
| `ALERT_PRECISION_MODE` | `true` |
| `ALERT_MIN_GRADE` | `B` |

You can also put **both** IDs in `TELEGRAM_CHAT_ID` as a comma list:  
`8825553536,-1001234567890`

3. **Save** → **Manual Deploy**

Alerts broadcast to **every** configured destination (your DM + paid channel).

## 5. Test

```text
https://pro-trader-fjrc.onrender.com/health
```

Should show `"telegram_24_7": true`.

Site → **Alert settings → Test Telegram** — you should get the test in **personal chat and channel**.

## Precision rules (what paying members get)

| Alert | When it fires |
|-------|----------------|
| **BUY / SELL** | Signal flips, conf ≥ 72%, grade ≥ B, 1H+4H aligned |
| **ENTRY** | Plan is ENTER_LONG/SHORT **and** price is at entry |
| **EXIT** | SL or TP3 hit (immediate) |
| **A+ SFP** | Swing failure at PDH/PDL/session liquidity |
| **PARTIAL** | Off by default |
| **News** | Only high/immediate urgency + high conf |

Cooldowns: signal 1h · entry 4h · exit 30m · SFP 2h.

## Keepalive

GitHub Actions pings `/healthz` every 10 minutes so free-tier Render stays awake.  
For rock-solid uptime, upgrade Render to a paid always-on instance.

## Local testing (optional)

Save token + chat/channel IDs on the dashboard once (`trade_alerts_config.json`, gitignored).  
**24/7 requires Render env vars.**

## Disclaimer for your channel bio

Use something like:

> Educational market alerts only — not financial advice. Trading involves risk of loss. Past performance ≠ future results.
