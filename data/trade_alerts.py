"""
Real-time trade alerts for all symbols — BUY, SELL, ENTRY, EXIT.

Server scans on every analysis refresh + fast price watch loop.
Pushes via WebSocket and optional Telegram (24/7, no tab needed).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

from data.assets import ASSETS, get_asset

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
STATE_FILE = ROOT / "trade_alerts_state.json"
CONFIG_FILE = ROOT / "trade_alerts_config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "browser_alerts": True,
    "telegram_enabled": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    # Precision mode: fewer, higher-quality alerts
    "precision_mode": True,
    "min_confidence_signal": 72,
    "min_confidence_entry": 70,
    "min_grade": "B",  # only A/B coach grades (A > B > C > D > F)
    "require_aligned": True,
    "require_enter_status": True,  # ENTRY only when plan is ENTER_LONG/SHORT
    "alert_buy": True,
    "alert_sell": True,
    "alert_entry": True,
    "alert_exit": True,
    "alert_exit_partial": False,  # less noise — only hard exits by default
    "alert_news": True,
    "cooldown_signal_sec": 3600,
    "cooldown_entry_sec": 14400,
    "cooldown_exit_sec": 1800,
    "symbols": {aid: True for aid in ASSETS},
}

_GRADE_RANK = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1, "—": 0, "-": 0, "": 0}

_store_lock = threading.Lock()


def _env_truthy(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _strip_env(val: str) -> str:
    v = (val or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1].strip()
    return v


def _merge_env_config(cfg: dict[str, Any]) -> dict[str, Any]:
    env_locked: dict[str, bool] = {}
    token_env = _strip_env(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    chat_env = _strip_env(os.environ.get("TELEGRAM_CHAT_ID", ""))
    if token_env:
        cfg["telegram_bot_token"] = token_env
        env_locked["telegram_token"] = True
    if chat_env:
        cfg["telegram_chat_id"] = chat_env
        env_locked["telegram_chat"] = True
    if env_locked:
        cfg["telegram_enabled"] = _env_truthy("TELEGRAM_ENABLED", True)
        cfg["enabled"] = _env_truthy("TRADE_ALERTS_ENABLED", True)
    if os.environ.get("ALERT_PRECISION_MODE") is not None:
        cfg["precision_mode"] = _env_truthy("ALERT_PRECISION_MODE", True)
    if os.environ.get("ALERT_MIN_GRADE"):
        cfg["min_grade"] = os.environ["ALERT_MIN_GRADE"].strip().upper()[:1] or "B"
    token = (cfg.get("telegram_bot_token") or "").strip()
    chat = str(cfg.get("telegram_chat_id") or "").strip()
    cfg["telegram_configured"] = bool(token and chat)
    cfg["alerts_permanent"] = bool(env_locked)
    cfg["env_locked"] = env_locked
    cfg["server_push_ready"] = bool(
        cfg.get("enabled") and cfg.get("telegram_enabled") and cfg["telegram_configured"]
    )
    cfg["is_cloud"] = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))
    return cfg


def _grade_ok(analysis: dict[str, Any], config: dict[str, Any]) -> tuple[bool, str]:
    """Precision gate using coach grade when present."""
    if not config.get("precision_mode", True):
        return True, ""
    coach = analysis.get("coach") or {}
    grade = (coach.get("grade") or {}).get("letter") or analysis.get("grade_letter") or ""
    min_g = str(config.get("min_grade") or "B").upper()[:1]
    if not grade or grade in ("—", "-"):
        # No coach grade — fall back to confidence only
        return True, "no_grade"
    ok = _GRADE_RANK.get(grade, 0) >= _GRADE_RANK.get(min_g, 4)
    return ok, grade


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                stored = json.load(f)
            cfg.update({k: v for k, v in stored.items() if k in DEFAULT_CONFIG or k.startswith("telegram")})
            if "symbols" in stored:
                cfg["symbols"] = {**cfg["symbols"], **stored["symbols"]}
        except (json.JSONDecodeError, OSError):
            pass
    return _merge_env_config(cfg)


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    with _store_lock:
        current = _merge_env_config(load_config())
        env_locked = current.get("env_locked") or {}
        cfg = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    cfg = json.load(f)
            except (json.JSONDecodeError, OSError):
                cfg = {}

        allowed = set(DEFAULT_CONFIG) | {"telegram_bot_token", "telegram_chat_id"}
        skip_locked = {
            "telegram_bot_token": "telegram_token",
            "telegram_chat_id": "telegram_chat",
        }
        for key, val in updates.items():
            if key not in allowed:
                continue
            lock = skip_locked.get(key)
            if lock and env_locked.get(lock):
                continue
            if key in ("telegram_bot_token", "telegram_chat_id") and not str(val).strip():
                continue
            if key == "symbols" and isinstance(val, dict):
                cfg["symbols"] = {**(cfg.get("symbols") or DEFAULT_CONFIG["symbols"]), **val}
            else:
                cfg[key] = val

        token = (cfg.get("telegram_bot_token") or current.get("telegram_bot_token") or "").strip()
        chat = str(cfg.get("telegram_chat_id") or current.get("telegram_chat_id") or "").strip()
        if token and chat and updates.get("telegram_enabled") is not False:
            cfg["telegram_enabled"] = True

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    return load_config()


def _safe_config(cfg: dict[str, Any]) -> dict[str, Any]:
    safe = {k: v for k, v in cfg.items() if k not in ("telegram_bot_token", "env_locked")}
    token_set = bool(cfg.get("telegram_bot_token"))
    chat_set = bool(str(cfg.get("telegram_chat_id") or "").strip())
    safe["telegram_token_set"] = token_set
    safe["telegram_chat_id"] = str(cfg.get("telegram_chat_id") or "")
    safe["telegram_configured"] = cfg.get("telegram_configured", False)
    safe["needs_chat_id"] = bool(cfg.get("telegram_bot_token") and not cfg.get("telegram_chat_id"))
    safe["needs_token"] = not bool(cfg.get("telegram_bot_token"))
    safe["server_push_ready"] = cfg.get("server_push_ready", False)
    safe["alerts_permanent"] = cfg.get("alerts_permanent", False) or (token_set and chat_set)
    safe["credentials_saved"] = token_set and chat_set
    safe["saved_permanently"] = CONFIG_FILE.exists() and token_set and chat_set
    safe["config_path"] = str(CONFIG_FILE)
    safe["env_locked"] = cfg.get("env_locked") or {}
    return safe


def _load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"assets": {}, "alert_log": [], "fired": {}}


def _save_state(state: dict[str, Any]) -> None:
    state["alert_log"] = state.get("alert_log", [])[:300]
    fired = state.get("fired", {})
    if len(fired) > 500:
        sorted_keys = sorted(fired, key=lambda k: fired[k], reverse=True)[:400]
        state["fired"] = {k: fired[k] for k in sorted_keys}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)


def _asset_state(state: dict, asset_id: str) -> dict[str, Any]:
    assets = state.setdefault("assets", {})
    if asset_id not in assets:
        assets[asset_id] = {
            "last_signal": "WAIT",
            "last_confidence": 0.0,
            "session_key": "",
            "exit_flags": {},
        }
    return assets[asset_id]


def _session_key(signal: str, trade_plan: dict, decimals: int = 5) -> str:
    """Stable session id — round levels so tiny recalcs don't re-fire ENTRY spam."""

    def _level(value) -> str:
        if value is None:
            return ""
        try:
            return f"{float(value):.{max(0, int(decimals))}f}"
        except (TypeError, ValueError):
            return str(value)

    entry = _level(trade_plan.get("entry"))
    sl = _level(trade_plan.get("stop_loss"))
    raw = f"{signal}|{entry}|{sl}"
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def _alert_id(asset_id: str, alert_type: str, key: str) -> str:
    return f"{asset_id}:{alert_type}:{key}"


def _already_fired(state: dict, alert_id: str, cooldown_sec: int = 3600) -> bool:
    fired = state.setdefault("fired", {})
    ts = fired.get(alert_id)
    if ts is None:
        return False
    try:
        age = time.time() - float(ts)
        return age < cooldown_sec
    except (TypeError, ValueError):
        return False


def _mark_fired(state: dict, alert_id: str) -> None:
    state.setdefault("fired", {})[alert_id] = time.time()


def _fmt_price(value: float | None, decimals: int) -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f}"


def _build_alert(
    asset_id: str,
    alert_type: str,
    signal: str,
    message: str,
    *,
    urgency: str = "normal",
    confidence: float = 0,
    price: float | None = None,
    trade_plan: dict | None = None,
    exit_check: dict | None = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    asset = get_asset(asset_id)
    plan = trade_plan or {}
    return {
        "id": "",
        "asset_id": asset_id,
        "asset_name": asset["name"],
        "asset_route": asset["route"],
        "type": alert_type,
        "signal": signal,
        "message": message,
        "urgency": urgency,
        "confidence": round(confidence, 1),
        "price": price,
        "entry": plan.get("entry"),
        "stop_loss": plan.get("stop_loss"),
        "take_profit_1": plan.get("take_profit_1"),
        "take_profit_2": plan.get("take_profit_2"),
        "take_profit_3": plan.get("take_profit_3"),
        "entry_trigger": plan.get("entry_trigger"),
        "exit_trigger": plan.get("exit_trigger"),
        "exit_check": exit_check,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }


def _price_near_entry(price: float, entry: float | None, tolerance: float) -> bool:
    if entry is None or not price:
        return False
    return abs(price - entry) <= tolerance


def _price_hit_level(price: float, level: float | None, direction: str) -> bool:
    """direction: 'above' for long TP, 'below' for long SL, etc."""
    if level is None:
        return False
    if direction == "above":
        return price >= level
    if direction == "below":
        return price <= level
    if direction == "below_sl_long":
        return price <= level
    if direction == "above_sl_short":
        return price >= level
    return False


def detect_trade_alerts(
    asset_id: str,
    analysis: dict[str, Any],
    state: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Detect new BUY/SELL/ENTRY/EXIT alerts from analysis snapshot."""
    config = config or load_config()
    state = state or _load_state()

    if not config.get("enabled"):
        return [], state
    if not config.get("symbols", {}).get(asset_id, True):
        return [], state

    if analysis.get("error"):
        return [], state

    asset = get_asset(asset_id)
    decimals = asset["decimals"]
    tolerance = asset["price_tolerance"]

    signal = analysis.get("signal", "WAIT")
    confidence = float(analysis.get("confidence", 0))
    trade_plan = analysis.get("trade_plan") or {}
    exit_check = analysis.get("exit_check") or {}
    quote = analysis.get("quote") or {}
    price = float(quote.get("price") or trade_plan.get("current_price") or 0)
    coach = analysis.get("coach") or {}
    grade_letter = (coach.get("grade") or {}).get("letter") or ""
    session_name = (coach.get("session") or {}).get("name") or ""
    aligned = bool(analysis.get("timeframes_aligned"))
    plan_status = trade_plan.get("position_status") or ""

    ast = _asset_state(state, asset_id)
    alerts: list[dict[str, Any]] = []
    session = _session_key(signal, trade_plan, decimals)

    min_sig = float(config.get("min_confidence_signal", 72))
    min_entry = float(config.get("min_confidence_entry", 70))
    # Raise bar when recent signal accuracy is weak
    try:
        from data.signal_audit import get_confidence_calibration
        cal = get_confidence_calibration()
        if cal.get("ready") and cal.get("min_conf_boost"):
            min_sig += float(cal["min_conf_boost"])
            min_entry += float(cal["min_conf_boost"]) * 0.5
    except Exception:
        pass
    cd_sig = int(config.get("cooldown_signal_sec", 3600))
    cd_entry = int(config.get("cooldown_entry_sec", 14400))
    grade_ok, grade = _grade_ok(analysis, config)

    # Precision: skip weak / misaligned directional alerts
    precision = config.get("precision_mode", True)
    require_aligned = config.get("require_aligned", True) if precision else False
    quality_ok = (
        confidence >= min_sig
        and grade_ok
        and (aligned or not require_aligned)
        and plan_status not in ("SKIP_POOR_RR",)
    )

    # --- Signal change: BUY / SELL ---
    prev_signal = ast.get("last_signal", "WAIT")
    if signal != prev_signal and quality_ok:
        if signal == "BUY" and config.get("alert_buy"):
            aid = _alert_id(asset_id, "buy", session)
            if not _already_fired(state, aid, cd_sig):
                alert = _build_alert(
                    asset_id, "buy", "BUY",
                    (
                        f"{asset['name']} BUY · {confidence:.0f}% conf"
                        f"{f' · Grade {grade}' if grade and grade not in ('—', 'no_grade') else ''}"
                        f"{f' · {session_name}' if session_name else ''}"
                    ),
                    urgency="high", confidence=confidence, price=price,
                    trade_plan=trade_plan,
                    extra={
                        "reason": f"Signal {prev_signal} → BUY",
                        "grade": grade_letter,
                        "session": session_name,
                        "plan_status": plan_status,
                        "aligned": aligned,
                    },
                )
                alert["id"] = aid
                alerts.append(alert)

        elif signal == "SELL" and config.get("alert_sell"):
            aid = _alert_id(asset_id, "sell", session)
            if not _already_fired(state, aid, cd_sig):
                alert = _build_alert(
                    asset_id, "sell", "SELL",
                    (
                        f"{asset['name']} SELL · {confidence:.0f}% conf"
                        f"{f' · Grade {grade}' if grade and grade not in ('—', 'no_grade') else ''}"
                        f"{f' · {session_name}' if session_name else ''}"
                    ),
                    urgency="high", confidence=confidence, price=price,
                    trade_plan=trade_plan,
                    extra={
                        "reason": f"Signal {prev_signal} → SELL",
                        "grade": grade_letter,
                        "session": session_name,
                        "plan_status": plan_status,
                        "aligned": aligned,
                    },
                )
                alert["id"] = aid
                alerts.append(alert)

    # --- Entry alert (precise: only when price is at entry / ENTER status) ---
    if (
        config.get("alert_entry")
        and signal in ("BUY", "SELL")
        and confidence >= min_entry
        and grade_ok
        and trade_plan.get("entry") is not None
        and plan_status not in ("SKIP_POOR_RR", "NO_POSITION", "WAITING_FOR_ENTRY")
    ):
        entry = float(trade_plan["entry"])
        status_enter = plan_status in ("ENTER_LONG", "ENTER_SHORT")
        at_price = _price_near_entry(price, entry, tolerance)
        # Precision: require ENTER status (not just "near-ish")
        if config.get("require_enter_status", True):
            at_entry = status_enter and at_price
        else:
            at_entry = at_price or status_enter
        if require_aligned and not aligned:
            at_entry = False
        direction_aid = _alert_id(asset_id, "entry", signal)
        if at_entry and ast.get("session_key") != session:
            aid = _alert_id(asset_id, "entry", session)
            if not _already_fired(state, direction_aid, cd_entry) and not _already_fired(
                state, aid, cd_entry
            ):
                sl = trade_plan.get("stop_loss")
                tp1 = trade_plan.get("take_profit_1")
                rr = trade_plan.get("risk_reward")
                alert = _build_alert(
                    asset_id, "entry", signal,
                    (
                        f"ENTRY {signal} {asset['name']} NOW\n"
                        f"Entry {_fmt_price(entry, decimals)} · now {_fmt_price(price, decimals)}\n"
                        f"SL {_fmt_price(sl, decimals)} · TP1 {_fmt_price(tp1, decimals)}"
                        f"{f' · R:R 1:{rr}' if rr else ''}"
                    ),
                    urgency="immediate", confidence=confidence, price=price,
                    trade_plan=trade_plan,
                    extra={
                        "reason": trade_plan.get("entry_trigger") or "At entry — execute",
                        "grade": grade_letter,
                        "session": session_name,
                        "plan_status": plan_status,
                    },
                )
                alert["id"] = aid
                alerts.append(alert)
                _mark_fired(state, direction_aid)
                ast["session_key"] = session

    # Reset session tracking when signal goes WAIT
    if signal == "WAIT":
        ast["session_key"] = ""
        ast["exit_flags"] = {}

    # --- Exit alerts from exit_check ---
    if config.get("alert_exit") or config.get("alert_exit_partial"):
        flags = ast.setdefault("exit_flags", {})
        action = trade_plan.get("action", signal)

        if exit_check.get("should_exit") and exit_check.get("urgency") == "immediate":
            if config.get("alert_exit"):
                reason = exit_check.get("reason", "Exit now")
                flag_key = f"exit:{reason}"
                aid = _alert_id(asset_id, "exit", flag_key)
                if not _already_fired(state, aid, 900):
                    alert = _build_alert(
                        asset_id, "exit", "EXIT",
                        f"EXIT NOW — {asset['name']}: {reason} @ {_fmt_price(price, decimals)}",
                        urgency="immediate", confidence=confidence, price=price,
                        trade_plan=trade_plan, exit_check=exit_check,
                    )
                    alert["id"] = aid
                    alerts.append(alert)

        elif exit_check.get("reason") and exit_check.get("urgency") == "consider":
            if config.get("alert_exit_partial"):
                reason = exit_check.get("reason", "")
                aid = _alert_id(asset_id, "exit_partial", reason)
                if not _already_fired(state, aid, 1800):
                    alert = _build_alert(
                        asset_id, "exit_partial", "EXIT",
                        f"PARTIAL EXIT — {asset['name']}: {reason} @ {_fmt_price(price, decimals)}",
                        urgency="consider", confidence=confidence, price=price,
                        trade_plan=trade_plan, exit_check=exit_check,
                    )
                    alert["id"] = aid
                    alerts.append(alert)

        # Price-level exit checks (SL / TP)
        if action == "BUY" and price:
            sl = trade_plan.get("stop_loss")
            for tp_key, level, dirn, label in (
                ("sl", sl, "below", "Stop loss hit"),
                ("tp1", trade_plan.get("take_profit_1"), "above", "TP1 reached — take 50%"),
                ("tp2", trade_plan.get("take_profit_2"), "above", "TP2 reached — trail stop"),
                ("tp3", trade_plan.get("take_profit_3"), "above", "TP3 reached — close remaining"),
            ):
                if level is None or flags.get(tp_key):
                    continue
                hit = price <= level if tp_key == "sl" else price >= level
                if hit:
                    flags[tp_key] = True
                    alert_type = "exit" if tp_key in ("sl", "tp3") else "exit_partial"
                    if (alert_type == "exit" and not config.get("alert_exit")) or (
                        alert_type == "exit_partial" and not config.get("alert_exit_partial")
                    ):
                        continue
                    aid = _alert_id(asset_id, alert_type, f"{tp_key}:{session}")
                    if not _already_fired(state, aid, 3600):
                        alert = _build_alert(
                            asset_id, alert_type,
                            "EXIT" if alert_type == "exit" else "PARTIAL",
                            f"{'EXIT' if alert_type == 'exit' else 'PARTIAL'} — {asset['name']}: {label} @ {_fmt_price(price, decimals)}",
                            urgency="immediate" if tp_key in ("sl", "tp3") else "consider",
                            confidence=confidence, price=price, trade_plan=trade_plan,
                        )
                        alert["id"] = aid
                        alerts.append(alert)

        elif action == "SELL" and price:
            sl = trade_plan.get("stop_loss")
            for tp_key, level, label in (
                ("sl", sl, "Stop loss hit"),
                ("tp1", trade_plan.get("take_profit_1"), "TP1 reached — take 50%"),
                ("tp2", trade_plan.get("take_profit_2"), "TP2 reached — trail stop"),
                ("tp3", trade_plan.get("take_profit_3"), "TP3 reached — close remaining"),
            ):
                if level is None or flags.get(tp_key):
                    continue
                hit = price >= level if tp_key == "sl" else price <= level
                if hit:
                    flags[tp_key] = True
                    alert_type = "exit" if tp_key in ("sl", "tp3") else "exit_partial"
                    if (alert_type == "exit" and not config.get("alert_exit")) or (
                        alert_type == "exit_partial" and not config.get("alert_exit_partial")
                    ):
                        continue
                    aid = _alert_id(asset_id, alert_type, f"{tp_key}:{session}")
                    if not _already_fired(state, aid, 3600):
                        alert = _build_alert(
                            asset_id, alert_type,
                            "EXIT" if alert_type == "exit" else "PARTIAL",
                            f"{'EXIT' if alert_type == 'exit' else 'PARTIAL'} — {asset['name']}: {label} @ {_fmt_price(price, decimals)}",
                            urgency="immediate" if tp_key in ("sl", "tp3") else "consider",
                            confidence=confidence, price=price, trade_plan=trade_plan,
                        )
                        alert["id"] = aid
                        alerts.append(alert)

    ast["last_signal"] = signal
    ast["last_confidence"] = confidence

    for alert in alerts:
        _mark_fired(state, alert["id"])
        entry = {
            "id": alert["id"],
            "asset_id": asset_id,
            "type": alert["type"],
            "signal": alert["signal"],
            "message": alert["message"],
            "urgency": alert["urgency"],
            "timestamp": alert["timestamp"],
        }
        state.setdefault("alert_log", []).insert(0, entry)

    if alerts:
        _save_state(state)
    elif ast.get("last_signal") != prev_signal:
        _save_state(state)

    return alerts, state


def detect_price_alerts(
    asset_id: str,
    cached_analysis: dict[str, Any],
    live_price: float,
    state: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fast price-only check between full analysis cycles."""
    if not cached_analysis or cached_analysis.get("error"):
        return [], state or _load_state()

    patched = dict(cached_analysis)
    patched["quote"] = {**(cached_analysis.get("quote") or {}), "price": live_price}
    return detect_trade_alerts(asset_id, patched, state, config)


def _format_telegram(alert: dict[str, Any]) -> str:
    asset = alert.get("asset_name", "")
    typ = (alert.get("type") or "").upper()
    sig = alert.get("signal") or ""
    conf = alert.get("confidence")
    lines = [
        f"Pro Trader · {typ}" + (f" {sig}" if sig and sig not in typ else ""),
        f"{asset}",
        alert.get("message", ""),
    ]
    if conf is not None:
        lines.append(f"Confidence: {conf:.0f}%")
    if alert.get("grade"):
        lines.append(f"Grade: {alert['grade']}")
    if alert.get("session"):
        lines.append(f"Session: {alert['session']}")
    if alert.get("entry") is not None:
        lines.append(f"Entry: {alert['entry']}")
    if alert.get("stop_loss") is not None:
        lines.append(f"Stop: {alert['stop_loss']}")
    if alert.get("take_profit_1") is not None:
        lines.append(f"TP1: {alert['take_profit_1']}")
    if alert.get("take_profit_2") is not None:
        lines.append(f"TP2: {alert['take_profit_2']}")
    if alert.get("price") is not None:
        lines.append(f"Live: {alert['price']}")
    if alert.get("reason"):
        lines.append(f"Why: {alert['reason']}")
    lines.append("— precision alerts · 24/7 server")
    return "\n".join(str(x) for x in lines if x is not None and str(x).strip())


def _telegram_post(token: str, method: str, payload: dict) -> tuple[bool, str, Optional[dict]]:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload,
            timeout=15,
        )
        data = r.json() if r.content else {}
        if r.ok and data.get("ok"):
            return True, "ok", data.get("result")
        err = data.get("description") or r.text or f"HTTP {r.status_code}"
        return False, str(err), None
    except requests.RequestException as exc:
        return False, str(exc), None


def discover_telegram_chats(token: str) -> dict[str, Any]:
    """Return chat IDs from recent messages (user must /start the bot first)."""
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "Bot token required"}

    ok, err, result = _telegram_post(token, "getUpdates", {"limit": 25, "timeout": 0})
    if not ok:
        return {"ok": False, "error": err}

    chats: dict[str, dict] = {}
    for item in result or []:
        msg = item.get("message") or item.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        key = str(cid)
        chats[key] = {
            "chat_id": key,
            "type": chat.get("type", ""),
            "title": chat.get("title") or chat.get("username") or "",
            "name": " ".join(
                x for x in [chat.get("first_name"), chat.get("last_name")] if x
            ).strip() or chat.get("username", ""),
            "username": chat.get("username", ""),
        }

    chat_list = list(chats.values())
    if not chat_list:
        return {
            "ok": False,
            "error": (
                "No messages found. Open your bot in Telegram → tap Start → send any message, "
                "then click Find chat ID again."
            ),
            "chats": [],
        }
    return {"ok": True, "chats": chat_list}


def merge_test_config(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Overlay unsaved form values onto stored config for test/discover."""
    cfg = load_config()
    body = body or {}
    for key in ("telegram_bot_token", "telegram_chat_id", "telegram_enabled"):
        if key in body and body[key] is not None:
            if key == "telegram_enabled":
                cfg[key] = bool(body[key])
            elif str(body[key]).strip():
                cfg[key] = str(body[key]).strip()
    token = (cfg.get("telegram_bot_token") or "").strip()
    chat = str(cfg.get("telegram_chat_id") or "").strip()
    cfg["telegram_configured"] = bool(token and chat)
    if body.get("telegram_enabled") is not False and token and chat:
        cfg["telegram_enabled"] = True
    return cfg


def _telegram_precheck(config: dict[str, Any]) -> tuple[bool, str]:
    if not config.get("telegram_enabled"):
        return False, "Telegram not enabled — check the box and click Save"
    token = (config.get("telegram_bot_token") or "").strip()
    chat_id = str(config.get("telegram_chat_id") or "").strip()
    if not token:
        return False, "Bot token missing — get one from @BotFather on Telegram"
    if not chat_id:
        return False, (
            "Chat ID missing — open your bot in Telegram, tap Start, send a message, "
            "then click Find chat ID"
        )
    return True, ""


def send_telegram_message(
    text: str,
    config: Optional[dict[str, Any]] = None,
) -> tuple[bool, str]:
    config = config or load_config()
    ok, err = _telegram_precheck(config)
    if not ok:
        return False, err
    token = (config.get("telegram_bot_token") or "").strip()
    chat_id = str(config.get("telegram_chat_id") or "").strip()
    ok, err, _ = _telegram_post(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text[:4000],
        "disable_web_page_preview": True,
    })
    if not ok:
        logger.warning("Telegram send failed: %s", err)
    return ok, err if not ok else "sent"


def send_telegram_alert(alert: dict[str, Any], config: dict[str, Any] | None = None) -> bool:
    ok, _ = send_telegram_message(_format_telegram(alert), config)
    return ok


def dispatch_alerts(alerts: list[dict[str, Any]], config: dict[str, Any] | None = None) -> int:
    config = config or load_config()
    if not config.get("telegram_enabled") or not config.get("telegram_configured"):
        return 0
    sent = 0
    for alert in alerts:
        if send_telegram_alert(alert, config):
            sent += 1
    return sent


def test_telegram(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    pre_ok, pre_err = _telegram_precheck(config)
    if not pre_ok:
        return {
            "ok": False,
            "telegram_configured": config.get("telegram_configured", False),
            "telegram_enabled": config.get("telegram_enabled", False),
            "needs_chat_id": bool(config.get("telegram_bot_token") and not config.get("telegram_chat_id")),
            "error": pre_err,
        }
    ok, detail = send_telegram_message(
        "Pro Trader Telegram OK\n\n"
        "Precision alerts (Grade A/B, high confidence):\n"
        "• BUY / SELL bias changes\n"
        "• ENTRY when price is at the level\n"
        "• EXIT on SL / TP hits\n\n"
        "Symbols: EUR/USD · Gold · Bitcoin\n"
        "Runs 24/7 on Render — PC can be off.",
        config,
    )
    return {
        "ok": ok,
        "telegram_configured": True,
        "telegram_enabled": config.get("telegram_enabled", False),
        "error": None if ok else detail,
    }


def get_alerts_status(scanner_running: bool = True) -> dict[str, Any]:
    cfg = load_config()
    symbols_on = [k for k, v in (cfg.get("symbols") or {}).items() if v]
    return {
        "enabled": cfg.get("enabled", True),
        "scanner_running": scanner_running,
        "server_push_ready": cfg.get("server_push_ready", False),
        "telegram_configured": cfg.get("telegram_configured", False),
        "telegram_enabled": cfg.get("telegram_enabled", False),
        "needs_chat_id": bool(cfg.get("telegram_bot_token") and not cfg.get("telegram_chat_id")),
        "needs_token": not bool(cfg.get("telegram_bot_token")),
        "symbols_monitored": symbols_on,
        "alert_types": {
            "buy": cfg.get("alert_buy", True),
            "sell": cfg.get("alert_sell", True),
            "entry": cfg.get("alert_entry", True),
            "exit": cfg.get("alert_exit", True),
            "exit_partial": cfg.get("alert_exit_partial", True),
        },
        "hint": (
            "Server monitors EUR/USD, Gold & Bitcoin 24/7 — Telegram pushes without opening the site."
            if cfg.get("server_push_ready")
            else "Enable Telegram in settings or set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in Render env."
        ),
    }


def get_alert_history(limit: int = 40) -> list[dict]:
    state = _load_state()
    return state.get("alert_log", [])[:limit]