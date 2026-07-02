"""Myfxbook cloud sync — pull XM/MT5 trades without desktop terminal.

User links phone MT5 account on myfxbook.com with investor password;
Myfxbook servers sync from XM. Pro Trader reads history via API.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote

import requests

logger = logging.getLogger(__name__)

API_ROOT = "https://www.myfxbook.com/api"
HEADERS = {"User-Agent": "ProTrader/1.0"}

_session_cache: dict[str, Any] = {"session": None, "email": "", "expires_at": 0.0}
SESSION_TTL = 3600


class MyfxbookError(Exception):
    pass


def _parse_mfb_time(raw: str) -> str:
    if not raw:
        return ""
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw.strip(), fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return raw


def _synthetic_ticket(*parts: str) -> int:
    key = "|".join(parts)
    return int(hashlib.md5(key.encode()).hexdigest()[:9], 16)


def _api_call(endpoint: str, params: dict[str, str]) -> dict[str, Any]:
    url = f"{API_ROOT}/{endpoint}.json"
    try:
        resp = requests.post(url, params=params, timeout=30, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise MyfxbookError(f"Myfxbook request failed: {exc}") from exc
    except ValueError as exc:
        raise MyfxbookError("Myfxbook returned invalid JSON") from exc

    if data.get("error"):
        msg = data.get("message") or "Myfxbook API error"
        if "session" in msg.lower():
            clear_session()
        raise MyfxbookError(msg)
    return data


def _api_call_session(
    endpoint: str,
    email: str,
    password: str,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Authenticated API call with one retry after clearing stale session."""
    for attempt in range(2):
        session = _login(email, password)
        try:
            return _api_call(endpoint, {"session": session, **(extra or {})})
        except MyfxbookError as exc:
            if attempt == 0 and "session" in str(exc).lower():
                clear_session()
                continue
            raise
    raise MyfxbookError("Myfxbook session failed")


def _login(email: str, password: str) -> str:
    now = time.time()
    if (
        _session_cache.get("session")
        and _session_cache.get("email") == email
        and now < _session_cache.get("expires_at", 0)
    ):
        return _session_cache["session"]

    data = _api_call("login", {"email": email, "password": password})
    session = unquote(data.get("session") or "")
    if not session:
        raise MyfxbookError("Login succeeded but no session returned")
    _session_cache.update({"session": session, "email": email, "expires_at": now + SESSION_TTL})
    return session


def clear_session() -> None:
    _session_cache.update({"session": None, "email": "", "expires_at": 0.0})


def get_accounts(email: str, password: str) -> list[dict[str, Any]]:
    data = _api_call_session("get-my-accounts", email, password)
    accounts = data.get("accounts") or []
    return [
        {
            "id": a.get("id"),
            "name": a.get("name"),
            "account_id": a.get("accountId"),
            "balance": a.get("balance"),
            "equity": a.get("equity"),
            "currency": a.get("currency"),
            "server": (a.get("server") or {}).get("name"),
            "last_update": a.get("lastUpdateDate"),
            "demo": a.get("demo"),
        }
        for a in accounts
    ]


def _history_to_deals(history: list[dict]) -> list[dict]:
    deals: list[dict] = []
    for row in history:
        symbol = (row.get("symbol") or "").strip()
        if not symbol:
            continue
        action = (row.get("action") or "").lower()
        side = "BUY" if "buy" in action else "SELL"
        sizing = row.get("sizing") or {}
        volume = float(sizing.get("value") or 0.01)
        open_time = _parse_mfb_time(row.get("openTime", ""))
        close_time = _parse_mfb_time(row.get("closeTime", ""))
        open_price = float(row.get("openPrice") or 0)
        close_price = float(row.get("closePrice") or 0)
        profit = round(float(row.get("profit") or 0), 2)
        commission = round(float(row.get("commission") or 0), 2)
        swap = round(float(row.get("interest") or row.get("swap") or 0), 2)
        magic = int(row.get("magic") or 0)
        comment = row.get("comment") or "myfxbook"

        pos_id = _synthetic_ticket(open_time, close_time, symbol, side, str(volume))
        in_ticket = _synthetic_ticket("in", str(pos_id))
        out_ticket = _synthetic_ticket("out", str(pos_id))

        deals.append({
            "ticket": in_ticket,
            "order": in_ticket,
            "position_id": pos_id,
            "time": open_time,
            "type": side,
            "entry": "IN",
            "symbol": symbol,
            "volume": volume,
            "price": open_price,
            "profit": 0.0,
            "commission": 0.0,
            "swap": 0.0,
            "fee": 0.0,
            "comment": comment,
            "magic": magic,
            "source": "myfxbook",
        })
        deals.append({
            "ticket": out_ticket,
            "order": out_ticket,
            "position_id": pos_id,
            "time": close_time or open_time,
            "type": side,
            "entry": "OUT",
            "symbol": symbol,
            "volume": volume,
            "price": close_price,
            "profit": profit,
            "commission": commission,
            "swap": swap,
            "fee": 0.0,
            "comment": comment,
            "magic": magic,
            "source": "myfxbook",
        })
    return deals


def _open_trades_to_positions(open_trades: list[dict]) -> list[dict]:
    positions: list[dict] = []
    for row in open_trades:
        action = (row.get("action") or "").lower()
        side = "BUY" if "buy" in action else "SELL"
        sizing = row.get("sizing") or {}
        positions.append({
            "ticket": int(row.get("magic") or _synthetic_ticket(
                row.get("openTime", ""), row.get("symbol", ""), side
            )),
            "symbol": row.get("symbol", ""),
            "type": side,
            "volume": float(sizing.get("value") or 0.01),
            "price_open": float(row.get("openPrice") or 0),
            "price_current": float(row.get("openPrice") or 0),
            "profit": round(float(row.get("profit") or 0), 2),
            "swap": round(float(row.get("swap") or 0), 2),
            "comment": row.get("comment") or "",
        })
    return positions


def fetch_ledger_data(
    email: str,
    password: str,
    account_id: int | str,
) -> dict[str, Any]:
    """Pull closed history, open trades, and account snapshot from Myfxbook."""
    if not email or not password:
        raise MyfxbookError("myfxbook_email and myfxbook_password required in config.json")
    if not account_id:
        raise MyfxbookError("myfxbook_account_id required — run /api/myfxbook/accounts to list IDs")

    aid = str(account_id)

    history_data = _api_call_session("get-history", email, password, {"id": aid})
    open_data = _api_call_session("get-open-trades", email, password, {"id": aid})
    accounts = get_accounts(email, password)

    account_snapshot: dict[str, Any] = {}
    for acc in accounts:
        if str(acc.get("id")) == aid:
            account_snapshot = {
                "login": acc.get("account_id"),
                "balance": acc.get("balance"),
                "equity": acc.get("equity"),
                "currency": acc.get("currency") or "USD",
                "server": acc.get("server"),
                "name": acc.get("name"),
            }
            break

    history = history_data.get("history") or []
    open_trades = open_data.get("openTrades") or []

    return {
        "deals": _history_to_deals(history),
        "open_positions": _open_trades_to_positions(open_trades),
        "account_snapshot": account_snapshot,
        "history_count": len(history),
        "open_count": len(open_trades),
    }


def test_connection(email: str, password: str, account_id: int | str = 0) -> dict[str, Any]:
    try:
        accounts = get_accounts(email, password)
        if account_id:
            match = next((a for a in accounts if str(a.get("id")) == str(account_id)), None)
            if not match:
                return {
                    "ok": False,
                    "error": f"Account id {account_id} not found in your Myfxbook accounts",
                    "accounts": accounts,
                }
        return {
            "ok": True,
            "message": f"Connected — {len(accounts)} account(s) on Myfxbook",
            "accounts": accounts,
        }
    except MyfxbookError as exc:
        return {"ok": False, "error": str(exc), "accounts": []}