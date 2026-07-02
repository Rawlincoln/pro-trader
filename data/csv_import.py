"""Parse MT5 / XM deal history CSV or tab-separated exports (desktop or member area)."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime, timezone
from typing import Any


def _parse_float(val: str) -> float:
    if not val:
        return 0.0
    cleaned = str(val).strip().replace("\xa0", "").replace(" ", "")
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", h.lower())


def _parse_time(parts: list[str]) -> str:
    raw = " ".join(p for p in parts if p).strip()
    if not raw:
        return ""
    for fmt in (
        "%Y.%m.%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y-%m-%d %H:%M",
    ):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return raw


def _synthetic_ticket(row: dict) -> int:
    key = f"{row.get('time')}|{row.get('symbol')}|{row.get('type')}|{row.get('volume')}|{row.get('price')}|{row.get('profit')}"
    return int(hashlib.md5(key.encode()).hexdigest()[:9], 16)


def parse_mt5_history_csv(text: str) -> tuple[list[dict], str]:
    """
    Parse MT5 History report CSV/TSV.
    Supports desktop export and XM statement-style files.
    """
    if not text or not text.strip():
        return [], "Empty file"

    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return [], "No rows found"

    header_idx = 0
    for i, row in enumerate(rows[:5]):
        joined = " ".join(row).lower()
        if "symbol" in joined or "profit" in joined or "deal" in joined or "position" in joined:
            header_idx = i
            break

    headers = [_norm_header(h) for h in rows[header_idx]]
    data_rows = rows[header_idx + 1:]

    col = {name: idx for idx, name in enumerate(headers) if name}

    def pick(*names: str) -> int | None:
        for n in names:
            if n in col:
                return col[n]
        return None

    time_i = pick("time", "date", "opentime", "closetime")
    symbol_i = pick("symbol", "item")
    type_i = pick("type", "direction", "action")
    vol_i = pick("volume", "lots", "size")
    price_i = pick("price", "closeprice", "openprice")
    profit_i = pick("profit", "pnl")
    comm_i = pick("commission", "comm")
    swap_i = pick("swap")
    deal_i = pick("deal", "ticket", "position", "order")
    pos_i = pick("position", "positionid")

    if symbol_i is None and profit_i is None:
        return [], "Could not find Symbol/Profit columns — export from MT5 History → Report"

    deals: list[dict] = []
    for row in data_rows:
        if len(row) < 3:
            continue
        symbol = row[symbol_i].strip() if symbol_i is not None and symbol_i < len(row) else ""
        if not symbol or symbol.lower() in ("symbol", "total"):
            continue

        raw_type = (row[type_i].strip().lower() if type_i is not None and type_i < len(row) else "")
        if "buy" in raw_type:
            deal_type = "BUY"
        elif "sell" in raw_type:
            deal_type = "SELL"
        else:
            deal_type = "BUY" if "buy" in " ".join(row).lower() else "SELL"

        time_str = _parse_time([row[time_i]] if time_i is not None and time_i < len(row) else row[:2])
        profit = _parse_float(row[profit_i] if profit_i is not None and profit_i < len(row) else "0")
        commission = _parse_float(row[comm_i] if comm_i is not None and comm_i < len(row) else "0")
        swap = _parse_float(row[swap_i] if swap_i is not None and swap_i < len(row) else "0")
        volume = _parse_float(row[vol_i] if vol_i is not None and vol_i < len(row) else "0")
        price = _parse_float(row[price_i] if price_i is not None and price_i < len(row) else "0")

        ticket_raw = row[deal_i].strip() if deal_i is not None and deal_i < len(row) else ""
        try:
            ticket = int(float(ticket_raw)) if ticket_raw else 0
        except ValueError:
            ticket = 0

        entry = "OUT" if profit != 0 or "close" in raw_type else "IN"
        position_id = 0
        if pos_i is not None and pos_i < len(row):
            try:
                position_id = int(float(row[pos_i].strip()))
            except ValueError:
                position_id = 0
        if not position_id and ticket:
            position_id = ticket

        deal = {
            "ticket": ticket,
            "order": ticket,
            "position_id": position_id,
            "time": time_str,
            "type": deal_type,
            "entry": entry,
            "symbol": symbol,
            "volume": volume or 0.01,
            "price": price,
            "profit": round(profit, 2),
            "commission": round(commission, 2),
            "swap": round(swap, 2),
            "fee": 0.0,
            "comment": "csv-import",
            "magic": 0,
            "source": "csv",
        }
        if not deal["ticket"]:
            deal["ticket"] = _synthetic_ticket(deal)
            deal["position_id"] = deal["ticket"]
        deals.append(deal)

    if not deals:
        return [], "No trades parsed — use MT5 History → right-click → Report → CSV"

    return deals, f"Imported {len(deals)} rows"