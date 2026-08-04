"""User-drawn chart levels (S/R from 4H/1H drawings) — persisted server-side for trading."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
LEVELS_PATH = ROOT / "user_levels.json"
_lock = threading.RLock()

# Drawings that define price levels we can trade against
_LEVEL_TYPES = frozenset({"hline", "rect", "trend", "extended"})


def _empty_asset() -> dict[str, Any]:
    return {
        "chart-1h": [],
        "chart-4h": [],
        "updated_at": None,
    }


def _load_all() -> dict[str, Any]:
    if not LEVELS_PATH.exists():
        return {}
    try:
        with open(LEVELS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load user levels: %s", exc)
        return {}


def _save_all(data: dict[str, Any]) -> None:
    with open(LEVELS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def _snapshot(asset_id: str, asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "chart-1h": list(asset.get("chart-1h") or []),
        "chart-4h": list(asset.get("chart-4h") or []),
        "updated_at": asset.get("updated_at"),
        "saved": LEVELS_PATH.exists(),
    }


def get_drawings(asset_id: str) -> dict[str, Any]:
    with _lock:
        all_data = _load_all()
        asset = all_data.get(asset_id) or _empty_asset()
        return _snapshot(asset_id, asset)


def save_drawings(
    asset_id: str,
    *,
    chart_1h: list | None = None,
    chart_4h: list | None = None,
    drawings: dict | None = None,
) -> dict[str, Any]:
    """Persist drawings for an asset. Accepts explicit lists or a drawings map."""
    with _lock:
        all_data = _load_all()
        asset = all_data.get(asset_id) or _empty_asset()

        if drawings:
            if "chart-1h" in drawings:
                asset["chart-1h"] = list(drawings.get("chart-1h") or [])
            if "chart-4h" in drawings:
                asset["chart-4h"] = list(drawings.get("chart-4h") or [])
        if chart_1h is not None:
            asset["chart-1h"] = list(chart_1h)
        if chart_4h is not None:
            asset["chart-4h"] = list(chart_4h)

        asset["updated_at"] = datetime.now(timezone.utc).isoformat()
        all_data[asset_id] = asset
        _save_all(all_data)
        return _snapshot(asset_id, asset)


def _parse_time(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Plotly sometimes uses ms epoch
        t = float(value)
        if t > 1e12:
            t /= 1000.0
        return t
    try:
        s = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def _trend_price_at(drawing: dict, at_ts: float | None = None) -> float | None:
    """Project a trend/ray line to a timestamp (default: now)."""
    t0 = _parse_time(drawing.get("x0"))
    t1 = _parse_time(drawing.get("x1"))
    try:
        y0 = float(drawing["y0"])
        y1 = float(drawing["y1"])
    except (KeyError, TypeError, ValueError):
        return None
    if t0 is None or t1 is None or t0 == t1:
        return (y0 + y1) / 2.0
    target = at_ts if at_ts is not None else datetime.now(timezone.utc).timestamp()
    slope = (y1 - y0) / (t1 - t0)
    return y0 + slope * (target - t0)


def extract_price_levels(
    drawings: list[dict],
    *,
    price: float,
    timeframe: str = "4h",
    decimals: int = 5,
) -> list[dict[str, Any]]:
    """Convert drawings into tradable level objects relative to current price."""
    levels: list[dict[str, Any]] = []
    now_ts = datetime.now(timezone.utc).timestamp()

    for d in drawings or []:
        dtype = d.get("type")
        if dtype not in _LEVEL_TYPES:
            continue
        color = d.get("color") or "#fbbf24"
        did = d.get("id") or ""

        if dtype == "hline":
            try:
                y = float(d["y"])
            except (KeyError, TypeError, ValueError):
                continue
            role = "support" if y < price else "resistance" if y > price else "at_price"
            levels.append({
                "price": round(y, decimals),
                "role": role,
                "kind": "hline",
                "timeframe": timeframe,
                "source": "user",
                "drawing_id": did,
                "strength": "user_marked",
                "label": f"Your {timeframe.upper()} H-line",
            })

        elif dtype == "rect":
            try:
                y0 = float(d["y0"])
                y1 = float(d["y1"])
            except (KeyError, TypeError, ValueError):
                continue
            lo, hi = (y0, y1) if y0 <= y1 else (y1, y0)
            mid = (lo + hi) / 2.0
            if hi < price:
                role = "support"
            elif lo > price:
                role = "resistance"
            else:
                role = "zone_contains_price"
            levels.append({
                "price": round(mid, decimals),
                "zone_low": round(lo, decimals),
                "zone_high": round(hi, decimals),
                "role": role,
                "kind": "zone",
                "timeframe": timeframe,
                "source": "user",
                "drawing_id": did,
                "strength": "user_zone",
                "label": f"Your {timeframe.upper()} zone",
            })

        elif dtype in ("trend", "extended"):
            y_now = _trend_price_at(d, now_ts)
            if y_now is None:
                continue
            role = "support" if y_now < price else "resistance" if y_now > price else "at_price"
            levels.append({
                "price": round(float(y_now), decimals),
                "role": role,
                "kind": "trendline",
                "timeframe": timeframe,
                "source": "user",
                "drawing_id": did,
                "strength": "user_trend",
                "label": f"Your {timeframe.upper()} trendline @ now",
            })

    # Dedupe near-identical prices
    levels.sort(key=lambda x: x["price"])
    deduped: list[dict[str, Any]] = []
    for lv in levels:
        if deduped and abs(lv["price"] - deduped[-1]["price"]) < 10 ** (-decimals):
            # Prefer zone / hline over trend projection if same price
            rank = {"zone": 3, "hline": 2, "trendline": 1}
            if rank.get(lv["kind"], 0) > rank.get(deduped[-1]["kind"], 0):
                deduped[-1] = lv
            continue
        deduped.append(lv)
    return deduped


def build_user_sr_snapshot(
    asset_id: str,
    price: float,
    *,
    decimals: int = 5,
    near_dist: float = 0.003,
) -> dict[str, Any]:
    """Full user S/R snapshot for trading + dashboard."""
    stored = get_drawings(asset_id)
    levels_1h = extract_price_levels(
        stored.get("chart-1h") or [], price=price, timeframe="1h", decimals=decimals
    )
    levels_4h = extract_price_levels(
        stored.get("chart-4h") or [], price=price, timeframe="4h", decimals=decimals
    )

    # Structural supports/resistances (zones: use edges, not double-count as both)
    supports: list[dict[str, Any]] = []
    resistances: list[dict[str, Any]] = []
    for lv in levels_4h + levels_1h:
        if lv["kind"] == "zone":
            supports.append({**lv, "price": lv["zone_low"], "role": "support"})
            resistances.append({**lv, "price": lv["zone_high"], "role": "resistance"})
        elif lv["role"] == "support":
            supports.append(lv)
        elif lv["role"] == "resistance":
            resistances.append(lv)

    # Nearest support at or below price
    nearest_support = None
    nearest_support_meta = None
    for lv in sorted(supports, key=lambda x: x["price"], reverse=True):
        if float(lv["price"]) <= price:
            nearest_support = float(lv["price"])
            nearest_support_meta = lv
            break

    nearest_resistance = None
    nearest_resistance_meta = None
    for lv in sorted(resistances, key=lambda x: x["price"]):
        if float(lv["price"]) >= price:
            nearest_resistance = float(lv["price"])
            nearest_resistance_meta = lv
            break

    near_user_support = bool(
        nearest_support is not None and 0 <= price - float(nearest_support) <= near_dist
    )
    near_user_resistance = bool(
        nearest_resistance is not None and 0 <= float(nearest_resistance) - price <= near_dist
    )

    return {
        "asset_id": asset_id,
        "has_drawings": bool(levels_1h or levels_4h),
        "count_1h": len(levels_1h),
        "count_4h": len(levels_4h),
        "levels_1h": levels_1h,
        "levels_4h": levels_4h,
        "all_levels": levels_4h + levels_1h,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "nearest_support_meta": nearest_support_meta,
        "nearest_resistance_meta": nearest_resistance_meta,
        "near_user_support": near_user_support,
        "near_user_resistance": near_user_resistance,
        "updated_at": stored.get("updated_at"),
        "priority": "user_4h_first",
    }


def merge_levels_with_user(
    auto_levels: dict[str, Any],
    user_sr: dict[str, Any],
    *,
    prefer_user: bool = True,
) -> dict[str, Any]:
    """Overlay user S/R onto auto-detected levels. User 4H wins for nearest levels."""
    merged = dict(auto_levels or {})
    supports = list(merged.get("support") or [])
    resistances = list(merged.get("resistance") or [])

    for lv in user_sr.get("levels_4h") or []:
        p = lv["price"]
        if lv["role"] in ("support", "zone_contains_price") and p not in supports:
            supports.append(p)
        if lv["role"] in ("resistance", "zone_contains_price") and p not in resistances:
            resistances.append(p)

    for lv in user_sr.get("levels_1h") or []:
        p = lv["price"]
        if lv["role"] == "support" and p not in supports:
            supports.append(p)
        if lv["role"] == "resistance" and p not in resistances:
            resistances.append(p)

    merged["support"] = sorted(set(supports))
    merged["resistance"] = sorted(set(resistances))
    merged["user_levels"] = user_sr.get("all_levels") or []
    merged["user_sr"] = {
        "near_user_support": user_sr.get("near_user_support"),
        "near_user_resistance": user_sr.get("near_user_resistance"),
        "count_4h": user_sr.get("count_4h"),
        "count_1h": user_sr.get("count_1h"),
        "has_drawings": user_sr.get("has_drawings"),
    }

    if prefer_user and user_sr.get("has_drawings"):
        if user_sr.get("nearest_support") is not None:
            merged["nearest_support"] = user_sr["nearest_support"]
            merged["support_strength"] = "user_marked"
            merged["support_source"] = "user_drawing"
            meta = user_sr.get("nearest_support_meta") or {}
            merged["support_label"] = meta.get("label", "Your drawn support")
        if user_sr.get("nearest_resistance") is not None:
            merged["nearest_resistance"] = user_sr["nearest_resistance"]
            merged["resistance_strength"] = "user_marked"
            merged["resistance_source"] = "user_drawing"
            meta = user_sr.get("nearest_resistance_meta") or {}
            merged["resistance_label"] = meta.get("label", "Your drawn resistance")

        # Price position from user levels
        if user_sr.get("near_user_support"):
            merged["price_position"] = "near_support"
        elif user_sr.get("near_user_resistance"):
            merged["price_position"] = "near_resistance"

    return merged
