"""
XM / MetaTrader 5 trade ledger — sync closed deals and build profitability balance sheet.
Runs locally only (MT5 Python API requires Windows + terminal open).
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.config import load_config
from agent.mt5_client import MT5Client

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
LEDGER_PATH = ROOT / "trade_ledger.json"

_lock = threading.Lock()


def _load_ledger() -> dict[str, Any]:
    if LEDGER_PATH.exists():
        try:
            with open(LEDGER_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"deals": [], "synced_at": None, "account_snapshot": {}}


def _save_ledger(data: dict[str, Any]) -> None:
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def _deal_net(deal: dict) -> float:
    return round(
        float(deal.get("profit", 0))
        + float(deal.get("commission", 0))
        + float(deal.get("swap", 0))
        + float(deal.get("fee", 0)),
        2,
    )


def _group_trades(deals: list[dict]) -> list[dict]:
    """Group MT5 deals by position_id into round-trip trades."""
    by_pos: dict[int, list[dict]] = defaultdict(list)
    for d in deals:
        pid = int(d.get("position_id") or 0)
        if pid:
            by_pos[pid].append(d)

    trades: list[dict] = []
    for pos_id, pos_deals in by_pos.items():
        pos_deals.sort(key=lambda x: x.get("time", ""))
        net = round(sum(_deal_net(d) for d in pos_deals), 2)
        vol = max(float(d.get("volume", 0)) for d in pos_deals)
        symbol = pos_deals[0].get("symbol", "")
        side = "BUY" if pos_deals[0].get("type") == "BUY" else "SELL"
        open_deal = next((d for d in pos_deals if d.get("entry") == "IN"), pos_deals[0])
        close_deal = next((d for d in reversed(pos_deals) if d.get("entry") == "OUT"), pos_deals[-1])
        trades.append({
            "position_id": pos_id,
            "symbol": symbol,
            "side": side,
            "volume": vol,
            "open_time": open_deal.get("time"),
            "close_time": close_deal.get("time"),
            "open_price": open_deal.get("price"),
            "close_price": close_deal.get("price"),
            "profit": round(sum(float(d.get("profit", 0)) for d in pos_deals), 2),
            "commission": round(sum(float(d.get("commission", 0)) for d in pos_deals), 2),
            "swap": round(sum(float(d.get("swap", 0)) for d in pos_deals), 2),
            "net_pnl": net,
            "deal_count": len(pos_deals),
            "comment": open_deal.get("comment", ""),
        })

    trades.sort(key=lambda t: t.get("close_time") or "", reverse=True)
    return trades


def _daily_pnl(trades: list[dict]) -> list[dict]:
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        day = (t.get("close_time") or "")[:10]
        if day:
            by_day[day] += float(t.get("net_pnl", 0))

    days = sorted(by_day.keys())
    cumulative = 0.0
    rows = []
    for day in days[-60:]:
        cumulative += by_day[day]
        rows.append({
            "date": day,
            "pnl": round(by_day[day], 2),
            "cumulative": round(cumulative, 2),
        })
    return rows


def _by_symbol(trades: list[dict]) -> list[dict]:
    stats: dict[str, dict] = {}
    for t in trades:
        sym = t.get("symbol", "?")
        s = stats.setdefault(sym, {"symbol": sym, "trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0})
        s["trades"] += 1
        net = float(t.get("net_pnl", 0))
        s["net_pnl"] = round(s["net_pnl"] + net, 2)
        if net > 0:
            s["wins"] += 1
        elif net < 0:
            s["losses"] += 1
    return sorted(stats.values(), key=lambda x: abs(x["net_pnl"]), reverse=True)


def build_balance_sheet(
    deals: list[dict],
    account: dict | None,
    open_positions: list[dict],
) -> dict[str, Any]:
    trades = _group_trades(deals)
    closed_net = round(sum(t["net_pnl"] for t in trades), 2)
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] < 0]
    open_float = round(sum(float(p.get("profit", 0)) for p in open_positions), 2)
    total_won = round(sum(t["net_pnl"] for t in wins), 2)
    total_lost = round(sum(t["net_pnl"] for t in losses), 2)
    decided = len(wins) + len(losses)
    win_rate = round(len(wins) / decided * 100, 1) if decided else 0.0

    balance = float(account.get("balance", 0)) if account else 0
    equity = float(account.get("equity", 0)) if account else 0

    return {
        "account": account or {},
        "summary": {
            "closed_trades": len(trades),
            "open_positions": len(open_positions),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(trades) - len(wins) - len(losses),
            "win_rate_pct": win_rate,
            "total_won": total_won,
            "total_lost": total_lost,
            "closed_net_pnl": closed_net,
            "open_floating_pnl": open_float,
            "total_net_pnl": round(closed_net + open_float, 2),
            "is_profitable": (closed_net + open_float) > 0,
            "balance": balance,
            "equity": equity,
            "status_label": (
                "PROFITABLE" if (closed_net + open_float) > 0
                else "AT LOSS" if (closed_net + open_float) < 0
                else "BREAKEVEN"
            ),
        },
        "by_symbol": _by_symbol(trades),
        "daily_pnl": _daily_pnl(trades),
        "recent_trades": trades[:40],
        "open_positions": open_positions,
        "deal_count": len(deals),
    }


def get_mt5_status() -> dict[str, Any]:
    """Check MT5 availability without persisting connection."""
    cfg = load_config()
    client = MT5Client(cfg)
    ok, msg = client.connect()
    result: dict[str, Any] = {
        "connected": ok,
        "message": msg,
        "broker": cfg.get("broker", "XM"),
        "mt5_available": True,
        "setup": _setup_checklist(cfg, mt5_connected=ok),
    }
    if ok:
        result["account"] = client.get_account()
        result["open_positions"] = client.get_positions_all()
    try:
        client.disconnect()
    except Exception:
        pass
    return result


def _setup_checklist(cfg: dict, mt5_connected: bool = False) -> list[dict]:
    items = [
        {
            "id": "mt5_installed",
            "label": "XM Global MT5 installed and open",
            "done": True,
            "hint": "Download from xm.com if not installed",
        },
        {
            "id": "logged_in",
            "label": "Logged into your XM account in MT5",
            "done": bool(cfg.get("account_login")) or True,
            "hint": "Or leave login blank in config.json — uses the open MT5 session",
        },
        {
            "id": "algo_trading",
            "label": "Algo Trading enabled in MT5 (toolbar button)",
            "done": mt5_connected,
            "hint": "Click 'Algo Trading' so it turns green",
        },
        {
            "id": "config",
            "label": "config.json set (optional login/server for auto-connect)",
            "done": CONFIG_PATH.exists() if (CONFIG_PATH := ROOT / "config.json") else False,
            "hint": "Copy config.example.json → config.json",
        },
    ]
    return items


def sync_from_mt5(days: int = 365) -> dict[str, Any]:
    """Pull deal history from MT5 and refresh balance sheet."""
    cfg = load_config()
    client = MT5Client(cfg)
    ok, msg = client.connect()
    if not ok:
        return {"ok": False, "error": msg, "setup": _setup_checklist(cfg, mt5_connected=False)}

    try:
        account = client.get_account()
        deals = client.get_deals_history(days=days)
        open_positions = client.get_positions_all()

        with _lock:
            ledger = _load_ledger()
            seen = {d["ticket"] for d in ledger.get("deals", [])}
            for deal in deals:
                if deal["ticket"] not in seen:
                    ledger.setdefault("deals", []).append(deal)
                    seen.add(deal["ticket"])
            ledger["deals"] = sorted(
                ledger.get("deals", []),
                key=lambda x: x.get("time", ""),
                reverse=True,
            )[:5000]
            ledger["synced_at"] = datetime.now(timezone.utc).isoformat()
            ledger["account_snapshot"] = account or {}
            _save_ledger(ledger)

        sheet = build_balance_sheet(ledger["deals"], account, open_positions)
        return {
            "ok": True,
            "message": f"Synced {len(deals)} deals from MT5",
            "synced_at": ledger["synced_at"],
            "balance_sheet": sheet,
        }
    finally:
        client.disconnect()


def get_balance_sheet() -> dict[str, Any]:
    """Return balance sheet from cached ledger + live MT5 if connected."""
    ledger = _load_ledger()
    cfg = load_config()
    client = MT5Client(cfg)
    ok, msg = client.connect()
    account = ledger.get("account_snapshot")
    open_positions: list[dict] = []

    if ok:
        account = client.get_account() or account
        open_positions = client.get_positions_all()
        client.disconnect()
    else:
        msg = msg

    sheet = build_balance_sheet(ledger.get("deals", []), account, open_positions)
    return {
        "ok": True,
        "mt5_connected": ok,
        "mt5_message": msg,
        "synced_at": ledger.get("synced_at"),
        "setup": _setup_checklist(cfg, mt5_connected=ok),
        "balance_sheet": sheet,
    }