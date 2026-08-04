# Pro Trader — Audit & Upgrade Notes

Date: 2026-08-04

## Executive summary

Pro Trader is a multi-asset (EUR/USD, Gold, Bitcoin) dashboard with technical analysis,
news/calendar, Myfxbook ledger, user-drawn S/R, and optional MT5 agent.

**Biggest gaps found and addressed in this pass:**

| Area | Before | After |
|------|--------|-------|
| Decision UX | Client re-derived confusing steps | Backend **coach** card: verb, steps, grade A–F, session, lots |
| Setup quality | Confidence % only | **A–F grade** from alignment, confluence, S/R, R:R, session, news |
| Session awareness | None | London / NY / Asia filter; thin hours demote EURUSD |
| Risk sizing | None | Suggested lots from equity + risk % + SL distance |
| User S/R | Drawn but easy to ignore | Prefer 4H drawings; mid-range WAIT on EURUSD |
| Secret key | Hardcoded default | Env-first + cloud warning |
| UI | Dense multi-panel | Simple action-first UI |

## Remaining risks (not fully fixed)

1. **Yahoo Finance single point of failure** — add a second OHLC source for production.
2. **Unauthenticated local APIs** — Myfxbook/Telegram config POSTs have no auth (OK for local; harden for public Render).
3. **MT5 agent ignores coach/user S/R** partially — agent path should call same `run_analysis` stack.
4. **Eventlet deprecated** — migrate fully to threading/async long-term.
5. **No paper-trade journal** — next big win: log every coach decision vs outcome.
6. **Render free tier cold starts** — keepalive cron helps; paid tier is better for alerts.

## Architecture (current)

```
Browser (simple action card + 4H chart drawings)
    ↓ WebSocket / REST
Flask app.py → run_analysis()
    → OHLC (Yahoo) + news RSS + calendar + Myfxbook API
    → strategy (1H/4H) + user_levels + news override
    → coach.py (grade, session, size, steps)
    → trade_alerts / Socket.IO
```

## How to use for best results

1. Draw **4H H-lines/zones** for EUR/USD.
2. Trade **London / NY** sessions (grade + session badges).
3. Only take **Grade A/B** (C only if at your level with clear R:R).
4. Use suggested **lot size** as a guide; confirm with your broker.
5. Keep Myfxbook synced so equity-based sizing is real.

## Deploy checklist

- [ ] Set `SECRET_KEY` on Render
- [ ] Set `MYFXBOOK_*` and `TELEGRAM_*` env vars
- [ ] Confirm `/health` returns `ok`
- [ ] Blueprint: https://dashboard.render.com/blueprints/new?repo=https://github.com/Rawlincoln/pro-trader
