"""Leveraged stock investment portfolios (KES) for the Pro Trader Investments page."""

from __future__ import annotations

import math
from datetime import date, timedelta

ENTRY_FEE_RATE = 0.02
EXIT_FEE_RATE = 0.03
# August 6,000 KES book: gross returns 9,900. Same multiple applied to the other books.
GROSS_RETURN_MULTIPLE = 9900 / 6000
WINDOW_START = date(2025, 9, 2)
WINDOW_END = date(2026, 9, 2)
CURRENCY = "KES"

# Five books: same 2% entry / 3% exit fees. 6,000 KES is August 2026; others span the prior 12 months.
_PORTFOLIO_SPECS: tuple[dict, ...] = (
    {
        "id": "eqty-3x",
        "name": "Equity Group 3×",
        "symbol": "EQTY",
        "market": "NSE",
        "side": "LONG",
        "leverage": 3,
        "gross_invest": 120_000,
        "opened": date(2025, 9, 22),
        "closed": date(2025, 11, 14),
        "color": "#7dd3fc",
        "note": "3× CFD · banking rebound into Q4 2025",
    },
    {
        "id": "kcb-2x",
        "name": "KCB Group 2×",
        "symbol": "KCB",
        "market": "NSE",
        "side": "LONG",
        "leverage": 2,
        "gross_invest": 60_000,
        "opened": date(2025, 12, 8),
        "closed": date(2026, 1, 28),
        "color": "#c4b5fd",
        "note": "2× CFD · year-end carry into January",
    },
    {
        "id": "eabl-4x",
        "name": "EABL 4×",
        "symbol": "EABL",
        "market": "NSE",
        "side": "LONG",
        "leverage": 4,
        "gross_invest": 230_000,
        "opened": date(2026, 2, 16),
        "closed": date(2026, 4, 22),
        "color": "#3dffa8",
        "note": "4× CFD · consumer recovery swing",
    },
    {
        "id": "bat-6x",
        "name": "BAT Kenya 6×",
        "symbol": "BAT",
        "market": "NSE",
        "side": "LONG",
        "leverage": 6,
        "gross_invest": 88_000,
        "opened": date(2026, 5, 12),
        "closed": date(2026, 7, 3),
        "color": "#ffd166",
        "note": "6× CFD · momentum book, mid-year",
    },
    {
        "id": "scom-5x-aug",
        "name": "Safaricom 5× · August",
        "symbol": "SCOM",
        "market": "NSE",
        "side": "LONG",
        "leverage": 5,
        "gross_invest": 6_000,
        "gross_returns": 9_900,
        "opened": date(2026, 8, 4),
        "closed": date(2026, 8, 28),
        "color": "#ff6b9d",
        "note": "August 2026 book · 5× CFD on SCOM",
        "highlight": True,
    },
)


def _round_kes(value: float) -> float:
    return round(float(value), 2)


def settle_book(gross_invest: float, gross_returns: float | None = None) -> dict:
    """Apply 2% entry fee and 3% fee on gross returns."""
    invest = _round_kes(gross_invest)
    returns = _round_kes(gross_returns if gross_returns is not None else invest * GROSS_RETURN_MULTIPLE)
    entry_fee = _round_kes(invest * ENTRY_FEE_RATE)
    net_deployed = _round_kes(invest - entry_fee)
    exit_fee = _round_kes(returns * EXIT_FEE_RATE)
    net_proceeds = _round_kes(returns - exit_fee)
    net_pnl = _round_kes(net_proceeds - invest)
    roi_pct = _round_kes((net_pnl / invest) * 100) if invest else 0.0
    return {
        "gross_invest": invest,
        "entry_fee_rate": ENTRY_FEE_RATE,
        "entry_fee": entry_fee,
        "net_deployed": net_deployed,
        "gross_returns": returns,
        "exit_fee_rate": EXIT_FEE_RATE,
        "exit_fee": exit_fee,
        "net_proceeds": net_proceeds,
        "net_pnl": net_pnl,
        "roi_pct": roi_pct,
    }


def _hold_days(opened: date, closed: date) -> int:
    return max((closed - opened).days, 1)


def _leveraged_mark(net_deployed: float, gross_returns: float, t: float, leverage: int, phase: float) -> float:
    """Path from net deployed to gross exit, with leverage-scaled wiggle."""
    t = min(max(t, 0.0), 1.0)
    eased = t ** 0.9
    base = net_deployed + (gross_returns - net_deployed) * eased
    amplitude = abs(gross_returns - net_deployed) * (0.035 + leverage * 0.01)
    wiggle = math.sin((t * math.pi * (2.1 + leverage * 0.18)) + phase) * amplitude * (1 - t * 0.72)
    return _round_kes(max(base + wiggle, net_deployed * 0.55))


def _date_range(start: date, end: date, step_days: int = 2) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=step_days)
    if days[-1] != end:
        days.append(end)
    return days


def _build_portfolio(spec: dict) -> dict:
    money = settle_book(spec["gross_invest"], spec.get("gross_returns"))
    opened: date = spec["opened"]
    closed: date = spec["closed"]
    hold = _hold_days(opened, closed)
    leverage = int(spec["leverage"])
    implied_move = _round_kes(((money["gross_returns"] / money["gross_invest"]) - 1) / leverage * 100)
    return {
        **{k: spec[k] for k in (
            "id", "name", "symbol", "market", "side", "leverage", "color", "note",
        )},
        "highlight": bool(spec.get("highlight")),
        "opened": opened.isoformat(),
        "closed": closed.isoformat(),
        "hold_days": hold,
        "period_label": opened.strftime("%b %Y") if opened.month == closed.month else (
            f"{opened.strftime('%b %Y')} – {closed.strftime('%b %Y')}"
        ),
        "implied_underlying_move_pct": implied_move,
        "status": "closed",
        "currency": CURRENCY,
        **money,
    }


def _series_for(portfolio: dict, dates: list[date]) -> list[float | None]:
    opened = date.fromisoformat(portfolio["opened"])
    closed = date.fromisoformat(portfolio["closed"])
    hold = max((closed - opened).days, 1)
    phase = (sum(ord(c) for c in portfolio["id"]) % 17) * 0.37
    values: list[float | None] = []
    for day in dates:
        if day < opened:
            values.append(None)
        elif day < closed:
            t = (day - opened).days / hold
            values.append(
                _leveraged_mark(
                    portfolio["net_deployed"],
                    portfolio["gross_returns"],
                    t,
                    portfolio["leverage"],
                    phase,
                )
            )
        else:
            values.append(portfolio["net_proceeds"])
    return values


def build_investments_snapshot() -> dict:
    portfolios = [_build_portfolio(spec) for spec in _PORTFOLIO_SPECS]
    dates = _date_range(WINDOW_START, WINDOW_END, step_days=2)
    iso_dates = [d.isoformat() for d in dates]
    series = []
    total: list[float] = [0.0] * len(dates)
    for p in portfolios:
        path = _series_for(p, dates)
        series.append({
            "id": p["id"],
            "name": p["name"],
            "color": p["color"],
            "values": path,
        })
        for i, v in enumerate(path):
            if v is not None:
                total[i] += v
    total = [_round_kes(v) for v in total]

    invested = _round_kes(sum(p["gross_invest"] for p in portfolios))
    entry_fees = _round_kes(sum(p["entry_fee"] for p in portfolios))
    exit_fees = _round_kes(sum(p["exit_fee"] for p in portfolios))
    net_proceeds = _round_kes(sum(p["net_proceeds"] for p in portfolios))
    net_pnl = _round_kes(sum(p["net_pnl"] for p in portfolios))
    gross_returns = _round_kes(sum(p["gross_returns"] for p in portfolios))
    roi_pct = _round_kes((net_pnl / invested) * 100) if invested else 0.0

    return {
        "currency": CURRENCY,
        "as_of": WINDOW_END.isoformat(),
        "window": {
            "start": WINDOW_START.isoformat(),
            "end": WINDOW_END.isoformat(),
            "label": "Past 12 months to 2 Sep 2026",
        },
        "fees": {
            "entry_rate": ENTRY_FEE_RATE,
            "exit_rate": EXIT_FEE_RATE,
            "entry_label": "2% entry fee",
            "exit_label": "3% fee on gross returns",
        },
        "summary": {
            "portfolios": len(portfolios),
            "invested": invested,
            "entry_fees": entry_fees,
            "exit_fees": exit_fees,
            "total_fees": _round_kes(entry_fees + exit_fees),
            "gross_returns": gross_returns,
            "net_proceeds": net_proceeds,
            "net_pnl": net_pnl,
            "roi_pct": roi_pct,
            "status_label": "NET PROFITABLE" if net_pnl > 0 else "NET DOWN",
            "is_profitable": net_pnl > 0,
        },
        "portfolios": portfolios,
        "chart": {
            "dates": iso_dates,
            "series": series,
            "total": total,
        },
    }
