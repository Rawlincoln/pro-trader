"""
Signal accuracy journal + daily audit + self-improvement weights.

1. Log every actionable BUY/SELL when the signal changes (deduped).
2. Score outcomes later from live price vs entry / SL / TP1.
3. Produce a daily audit (win rate, by asset/source/grade).
4. Update component weights so future scores favor what has worked.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from data.assets import ASSETS, get_asset

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
JOURNAL_FILE = ROOT / "signal_audit_journal.json"
MAX_SIGNALS = 800
TIMEOUT_HOURS = 24
# How far price must move favorably (as fraction of SL distance) for soft WIN on timeout
TIMEOUT_WIN_FRAC = 0.35
MIN_WEIGHT = 0.55
MAX_WEIGHT = 1.55
WEIGHT_STEP = 0.04

COMPONENT_KEYS = (
    "trend",
    "rsi",
    "macd",
    "stochastic",
    "cci",
    "williams_r",
    "mfi",
    "adx",
    "ichimoku",
    "ema_cross",
    "patterns",
    "volume",
    "support_resistance",
)

_lock = threading.Lock()
_last_daily_sent: str = ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _utc_now()).isoformat()


def _default_weights() -> dict[str, float]:
    return {k: 1.0 for k in COMPONENT_KEYS}


def _empty_journal() -> dict[str, Any]:
    return {
        "signals": [],
        "weights": _default_weights(),
        "source_stats": {},
        "grade_stats": {},
        "asset_stats": {},
        "daily_audits": [],
        "last_keys": {},  # asset_id -> session key of last logged signal
        "learning": {
            "updates": 0,
            "last_update": None,
            "notes": [],
        },
    }


def _load() -> dict[str, Any]:
    if not JOURNAL_FILE.exists():
        return _empty_journal()
    try:
        with open(JOURNAL_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_journal()
        base = _empty_journal()
        base.update(data)
        base["weights"] = {**_default_weights(), **(data.get("weights") or {})}
        base.setdefault("signals", [])
        base.setdefault("last_keys", {})
        base.setdefault("daily_audits", [])
        base.setdefault("learning", {"updates": 0, "last_update": None, "notes": []})
        return base
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("signal audit journal read failed: %s", exc)
        return _empty_journal()


def _save(data: dict[str, Any]) -> None:
    try:
        JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = JOURNAL_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(JOURNAL_FILE)
    except OSError as exc:
        logger.error("signal audit journal write failed: %s", exc)


def get_component_weights() -> dict[str, float]:
    with _lock:
        j = _load()
        return dict(j.get("weights") or _default_weights())


def get_confidence_calibration() -> dict[str, Any]:
    """
    Return multipliers / min conf floors derived from recent outcomes.
    Used to scale final confidence and tighten weak sources.
    """
    with _lock:
        j = _load()
        closed = [s for s in j.get("signals", []) if s.get("status") in ("win", "loss", "timeout")]
        closed = closed[-80:]  # recent window
        if len(closed) < 5:
            return {
                "ready": False,
                "sample": len(closed),
                "win_rate": None,
                "conf_scale": 1.0,
                "source_scale": {},
                "min_conf_boost": 0.0,
                "note": "Need ≥5 closed signals before calibration adjusts scores",
            }

        wins = sum(1 for s in closed if s.get("status") == "win")
        losses = sum(1 for s in closed if s.get("status") == "loss")
        timeouts = sum(1 for s in closed if s.get("status") == "timeout")
        # Treat timeout as half-loss for conf scale
        scored = wins + losses + timeouts * 0.5
        win_rate = wins / max(1, wins + losses + timeouts * 0.5)

        # conf_scale: if win_rate 0.5 → 1.0; 0.7 → ~1.08; 0.3 → ~0.88
        conf_scale = max(0.82, min(1.12, 0.85 + win_rate * 0.3))

        # If recent accuracy is poor, require slightly higher conf for alerts
        min_conf_boost = 0.0
        if win_rate < 0.42 and len(closed) >= 10:
            min_conf_boost = 5.0
        elif win_rate < 0.48 and len(closed) >= 8:
            min_conf_boost = 3.0

        source_scale: dict[str, float] = {}
        by_src: dict[str, list] = {}
        for s in closed:
            src = s.get("signal_source") or "technical"
            by_src.setdefault(src, []).append(s)
        for src, rows in by_src.items():
            if len(rows) < 3:
                continue
            w = sum(1 for r in rows if r.get("status") == "win")
            n = len(rows)
            wr = w / n
            source_scale[src] = max(0.85, min(1.15, 0.88 + wr * 0.28))

        return {
            "ready": True,
            "sample": len(closed),
            "wins": wins,
            "losses": losses,
            "timeouts": timeouts,
            "win_rate": round(win_rate * 100, 1),
            "conf_scale": round(conf_scale, 3),
            "source_scale": {k: round(v, 3) for k, v in source_scale.items()},
            "min_conf_boost": min_conf_boost,
            "note": (
                f"Calibration from last {len(closed)} signals: "
                f"{win_rate * 100:.0f}% accuracy → conf×{conf_scale:.2f}"
            ),
        }


def apply_accuracy_to_analysis(
    signal: str,
    confidence: float,
    signal_source: str = "technical",
) -> tuple[str, float, list[str]]:
    """Scale confidence from historical accuracy. May demote weak signals to WAIT."""
    notes: list[str] = []
    cal = get_confidence_calibration()
    if not cal.get("ready"):
        return signal, confidence, notes

    conf = float(confidence or 0)
    scale = float(cal.get("conf_scale") or 1.0)
    src_scale = (cal.get("source_scale") or {}).get(signal_source or "technical", 1.0)
    conf = conf * scale * src_scale

    boost = float(cal.get("min_conf_boost") or 0)
    if boost and signal in ("BUY", "SELL") and conf < (72 + boost):
        notes.append(
            f"Accuracy filter: recent win rate {cal.get('win_rate')}% — "
            f"need higher conf (boost +{boost:.0f})"
        )
        # Soft demote: reduce conf; hard WAIT only if still weak after scale
        conf = max(30.0, conf - boost)
        if conf < 58:
            notes.append("Demoted to WAIT by accuracy filter")
            signal = "WAIT"

    conf = round(min(97.0, max(20.0, conf)), 1)
    if abs(scale - 1.0) > 0.02 or abs(src_scale - 1.0) > 0.02:
        notes.append(cal.get("note") or "Accuracy calibration applied")
    return signal, conf, notes


def _session_key(asset_id: str, signal: str, plan: dict) -> str:
    entry = plan.get("entry")
    sl = plan.get("stop_loss")
    raw = f"{asset_id}|{signal}|{entry}|{sl}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _signal_id(asset_id: str, signal: str, ts: str, key: str) -> str:
    raw = f"{asset_id}|{signal}|{ts}|{key}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def maybe_log_signal(analysis: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Log a new BUY/SELL when it differs from the last logged session for this asset.
    Call from run_analysis after final signal is set.
    """
    if not analysis or analysis.get("error"):
        return None
    signal = (analysis.get("signal") or "WAIT").upper()
    if signal not in ("BUY", "SELL"):
        # Clear last key so a later BUY/SELL can re-log even if plan matches
        with _lock:
            j = _load()
            aid = analysis.get("asset_id") or "eurusd"
            prev = (j.get("last_keys") or {}).get(aid, "")
            if prev and not str(prev).startswith("WAIT"):
                j.setdefault("last_keys", {})[aid] = f"WAIT|{_iso()}"
                _save(j)
        return None

    asset_id = analysis.get("asset_id") or "eurusd"
    plan = analysis.get("trade_plan") or {}
    if plan.get("position_status") == "SKIP_POOR_RR":
        return None

    coach = analysis.get("coach") or {}
    grade = (coach.get("grade") or {}).get("letter") or ""
    conf = float(analysis.get("confidence") or 0)
    # Only journal setups that are at least somewhat actionable
    if conf < 55:
        return None

    key = _session_key(asset_id, signal, plan)
    quote = analysis.get("quote") or {}
    price = float(quote.get("price") or plan.get("current_price") or plan.get("entry") or 0)
    if not price:
        return None

    breakdown_1h = ((analysis.get("analysis_1h") or {}).get("breakdown")) or {}
    breakdown_4h = ((analysis.get("analysis_4h") or {}).get("breakdown")) or {}
    session_name = (coach.get("session") or {}).get("name") or ""

    row = {
        "id": _signal_id(asset_id, signal, _iso(), key),
        "ts": _iso(),
        "asset_id": asset_id,
        "asset_name": analysis.get("asset_name") or get_asset(asset_id)["name"],
        "signal": signal,
        "confidence": conf,
        "grade": grade,
        "aligned": bool(analysis.get("timeframes_aligned")),
        "signal_source": analysis.get("signal_source") or "technical",
        "session": session_name,
        "confluence": analysis.get("confluence"),
        "combined_score": analysis.get("combined_score"),
        "entry": plan.get("entry"),
        "stop_loss": plan.get("stop_loss"),
        "take_profit_1": plan.get("take_profit_1"),
        "take_profit_2": plan.get("take_profit_2"),
        "take_profit_3": plan.get("take_profit_3"),
        "risk_reward": plan.get("risk_reward"),
        "position_status": plan.get("position_status"),
        "price_at_signal": price,
        "breakdown_1h": breakdown_1h,
        "breakdown_4h": breakdown_4h,
        "status": "open",
        "outcome": None,
        "evaluated_at": None,
        "session_key": key,
    }

    with _lock:
        j = _load()
        last = (j.get("last_keys") or {}).get(asset_id)
        if last == key:
            return None  # already journaling this setup
        j.setdefault("last_keys", {})[asset_id] = key
        j.setdefault("signals", []).insert(0, row)
        j["signals"] = j["signals"][:MAX_SIGNALS]
        _save(j)
        logger.info(
            "Signal audit logged %s %s conf=%.0f grade=%s id=%s",
            asset_id, signal, conf, grade, row["id"],
        )
        return row


def _eval_one(sig: dict[str, Any], live_price: float) -> Optional[dict[str, Any]]:
    """Return outcome dict if closed, else None."""
    if sig.get("status") != "open":
        return None
    signal = sig.get("signal")
    entry = sig.get("entry") or sig.get("price_at_signal")
    sl = sig.get("stop_loss")
    tp1 = sig.get("take_profit_1")
    if entry is None or not live_price:
        return None

    entry = float(entry)
    live_price = float(live_price)
    sl_f = float(sl) if sl is not None else None
    tp1_f = float(tp1) if tp1 is not None else None

    hit_sl = False
    hit_tp = False
    if signal == "BUY":
        if sl_f is not None and live_price <= sl_f:
            hit_sl = True
        if tp1_f is not None and live_price >= tp1_f:
            hit_tp = True
    elif signal == "SELL":
        if sl_f is not None and live_price >= sl_f:
            hit_sl = True
        if tp1_f is not None and live_price <= tp1_f:
            hit_tp = True
    else:
        return None

    # Prefer first target if both somehow true (unlikely on one tick)
    if hit_tp and not hit_sl:
        move = abs(live_price - entry)
        return {
            "status": "win",
            "exit_price": live_price,
            "exit_reason": "TP1 reached",
            "pnl_price": move if signal == "BUY" else move,
            "direction_correct": True,
        }
    if hit_sl and not hit_tp:
        move = abs(live_price - entry)
        return {
            "status": "loss",
            "exit_price": live_price,
            "exit_reason": "Stop loss hit",
            "pnl_price": -move,
            "direction_correct": False,
        }
    if hit_sl and hit_tp:
        # Ambiguous — use mid rule: which is closer to entry was hit first? Prefer SL safety
        return {
            "status": "loss",
            "exit_price": live_price,
            "exit_reason": "SL and TP both crossed (counted as loss)",
            "pnl_price": -abs(live_price - entry),
            "direction_correct": False,
        }

    # Timeout
    try:
        ts = datetime.fromisoformat(sig["ts"].replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, KeyError):
        return None

    age_h = (_utc_now() - ts).total_seconds() / 3600.0
    if age_h < TIMEOUT_HOURS:
        return None

    # Directional resolution after timeout
    risk = abs(entry - sl_f) if sl_f is not None else abs(entry) * 0.002
    risk = max(risk, 1e-9)
    if signal == "BUY":
        pnl = live_price - entry
    else:
        pnl = entry - live_price
    frac = pnl / risk
    if frac >= TIMEOUT_WIN_FRAC:
        status = "win"
        reason = f"Timeout {TIMEOUT_HOURS}h — price moved favorably ({frac:.0%} of risk)"
        ok = True
    elif frac <= -TIMEOUT_WIN_FRAC:
        status = "loss"
        reason = f"Timeout {TIMEOUT_HOURS}h — price moved against ({frac:.0%} of risk)"
        ok = False
    else:
        status = "timeout"
        reason = f"Timeout {TIMEOUT_HOURS}h — flat / unclear ({frac:.0%} of risk)"
        ok = frac > 0

    return {
        "status": status,
        "exit_price": live_price,
        "exit_reason": reason,
        "pnl_price": pnl,
        "direction_correct": ok if status != "timeout" else frac > 0,
        "timeout_frac": round(frac, 3),
    }


def _update_stats(j: dict[str, Any], sig: dict[str, Any]) -> None:
    status = sig.get("status")
    if status not in ("win", "loss", "timeout"):
        return

    def bump(bucket: dict, key: str) -> None:
        b = bucket.setdefault(key, {"wins": 0, "losses": 0, "timeouts": 0, "total": 0})
        b["total"] = int(b.get("total") or 0) + 1
        if status == "win":
            b["wins"] = int(b.get("wins") or 0) + 1
        elif status == "loss":
            b["losses"] = int(b.get("losses") or 0) + 1
        else:
            b["timeouts"] = int(b.get("timeouts") or 0) + 1

    bump(j.setdefault("asset_stats", {}), sig.get("asset_id") or "unknown")
    bump(j.setdefault("source_stats", {}), sig.get("signal_source") or "technical")
    grade = sig.get("grade") or "—"
    bump(j.setdefault("grade_stats", {}), grade)


def _learn_from_outcome(j: dict[str, Any], sig: dict[str, Any]) -> None:
    """Nudge component weights based on whether that component agreed with the call."""
    status = sig.get("status")
    if status not in ("win", "loss"):
        return  # skip pure timeouts for learning

    signal = sig.get("signal")
    direction = 1 if signal == "BUY" else -1
    delta = WEIGHT_STEP if status == "win" else -WEIGHT_STEP

    weights = j.setdefault("weights", _default_weights())
    for frame_key in ("breakdown_4h", "breakdown_1h"):
        br = sig.get(frame_key) or {}
        # 4H counts more
        frame_mult = 1.0 if frame_key == "breakdown_4h" else 0.6
        for comp in COMPONENT_KEYS:
            raw = br.get(comp)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            # Component "agreed" if its score sign matches trade direction
            if val == 0:
                continue
            agreed = (val > 0 and direction > 0) or (val < 0 and direction < 0)
            if not agreed:
                # Disagreeing component: invert the learning nudge
                nudge = -delta * frame_mult * 0.5
            else:
                nudge = delta * frame_mult
            w = float(weights.get(comp, 1.0)) + nudge
            weights[comp] = round(max(MIN_WEIGHT, min(MAX_WEIGHT, w)), 3)

    learn = j.setdefault("learning", {"updates": 0, "last_update": None, "notes": []})
    learn["updates"] = int(learn.get("updates") or 0) + 1
    learn["last_update"] = _iso()
    note = (
        f"{sig.get('asset_id')} {signal} → {status}: "
        f"weights nudged (sample update #{learn['updates']})"
    )
    notes = list(learn.get("notes") or [])
    notes.insert(0, note)
    learn["notes"] = notes[:40]


def evaluate_open_signals(
    prices: dict[str, float] | None = None,
    analysis_by_asset: dict[str, dict] | None = None,
) -> list[dict[str, Any]]:
    """
    Score open journal rows.
    prices: asset_id -> live price
    analysis_by_asset: optional full analysis dicts (price taken from quote)
    """
    prices = dict(prices or {})
    if analysis_by_asset:
        for aid, data in analysis_by_asset.items():
            if not data or data.get("error"):
                continue
            q = data.get("quote") or {}
            p = q.get("price") or (data.get("trade_plan") or {}).get("current_price")
            if p:
                prices[aid] = float(p)

    closed_now: list[dict[str, Any]] = []
    with _lock:
        j = _load()
        changed = False
        for sig in j.get("signals", []):
            if sig.get("status") != "open":
                continue
            aid = sig.get("asset_id")
            px = prices.get(aid)
            if px is None:
                continue
            outcome = _eval_one(sig, float(px))
            if not outcome:
                continue
            sig["status"] = outcome["status"]
            sig["outcome"] = outcome
            sig["evaluated_at"] = _iso()
            _update_stats(j, sig)
            _learn_from_outcome(j, sig)
            closed_now.append(deepcopy(sig))
            changed = True
            logger.info(
                "Signal audit closed %s %s → %s (%s)",
                aid, sig.get("signal"), outcome["status"], outcome.get("exit_reason"),
            )
        if changed:
            _save(j)
    return closed_now


def _win_rate(rows: list[dict]) -> Optional[float]:
    closed = [r for r in rows if r.get("status") in ("win", "loss", "timeout")]
    if not closed:
        return None
    wins = sum(1 for r in closed if r.get("status") == "win")
    # timeouts count as non-wins for rate denominator
    return round(100.0 * wins / len(closed), 1)


def build_daily_audit(day: date | None = None) -> dict[str, Any]:
    """Aggregate accuracy for a UTC calendar day (signals logged that day + outcomes)."""
    day = day or _utc_now().date()
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    with _lock:
        j = _load()
        signals = list(j.get("signals") or [])
        weights = dict(j.get("weights") or {})
        learning = dict(j.get("learning") or {})

    def in_day(ts_str: str | None) -> bool:
        if not ts_str:
            return False
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return day_start <= ts < day_end
        except ValueError:
            return False

    logged_today = [s for s in signals if in_day(s.get("ts"))]
    # Outcomes resolved today (may be from earlier signals)
    resolved_today = [s for s in signals if in_day(s.get("evaluated_at"))]
    open_rows = [s for s in signals if s.get("status") == "open"]

    def summarize(rows: list[dict]) -> dict[str, Any]:
        by_status = {"win": 0, "loss": 0, "timeout": 0, "open": 0}
        for r in rows:
            st = r.get("status") or "open"
            by_status[st] = by_status.get(st, 0) + 1
        items = []
        for r in rows[:40]:
            items.append({
                "id": r.get("id"),
                "ts": r.get("ts"),
                "asset": r.get("asset_name") or r.get("asset_id"),
                "signal": r.get("signal"),
                "confidence": r.get("confidence"),
                "grade": r.get("grade"),
                "source": r.get("signal_source"),
                "status": r.get("status"),
                "reason": (r.get("outcome") or {}).get("exit_reason"),
                "entry": r.get("entry"),
                "exit": (r.get("outcome") or {}).get("exit_price"),
            })
        return {
            "count": len(rows),
            "by_status": by_status,
            "win_rate": _win_rate(rows),
            "items": items,
        }

    # Overall recent (7d)
    week_ago = _utc_now() - timedelta(days=7)
    recent = []
    for s in signals:
        try:
            ts = datetime.fromisoformat(s["ts"].replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= week_ago:
                recent.append(s)
        except (ValueError, KeyError, TypeError):
            continue

    by_asset: dict[str, list] = {}
    by_source: dict[str, list] = {}
    by_grade: dict[str, list] = {}
    for s in recent:
        by_asset.setdefault(s.get("asset_id") or "?", []).append(s)
        by_source.setdefault(s.get("signal_source") or "?", []).append(s)
        by_grade.setdefault(s.get("grade") or "—", []).append(s)

    # Top/bottom components by weight deviation from 1.0
    ranked = sorted(
        ((k, float(v)) for k, v in weights.items() if k in COMPONENT_KEYS),
        key=lambda x: x[1],
        reverse=True,
    )
    strong = [{"component": k, "weight": v} for k, v in ranked[:5] if v >= 1.0]
    weak = [{"component": k, "weight": v} for k, v in reversed(ranked) if v < 1.0][:5]

    audit = {
        "date": day.isoformat(),
        "generated_at": _iso(),
        "logged_today": summarize(logged_today),
        "resolved_today": summarize(resolved_today),
        "still_open": len(open_rows),
        "last_7_days": {
            "count": len(recent),
            "win_rate": _win_rate(recent),
            "by_asset": {
                k: {"count": len(v), "win_rate": _win_rate(v)} for k, v in by_asset.items()
            },
            "by_source": {
                k: {"count": len(v), "win_rate": _win_rate(v)} for k, v in by_source.items()
            },
            "by_grade": {
                k: {"count": len(v), "win_rate": _win_rate(v)} for k, v in by_grade.items()
            },
        },
        "learning": {
            "updates": learning.get("updates", 0),
            "last_update": learning.get("last_update"),
            "strong_components": strong,
            "weak_components": weak,
            "weights": weights,
            "calibration": get_confidence_calibration(),
            "recent_notes": (learning.get("notes") or [])[:8],
        },
        "accurate": [
            i for i in summarize(resolved_today)["items"] if i.get("status") == "win"
        ],
        "inaccurate": [
            i for i in summarize(resolved_today)["items"] if i.get("status") == "loss"
        ],
        "unclear": [
            i for i in summarize(resolved_today)["items"] if i.get("status") == "timeout"
        ],
    }
    return audit


def format_daily_audit_telegram(audit: dict[str, Any]) -> str:
    d = audit.get("date", "")
    res = audit.get("resolved_today") or {}
    log = audit.get("logged_today") or {}
    week = audit.get("last_7_days") or {}
    learn = audit.get("learning") or {}
    cal = learn.get("calibration") or {}

    lines = [
        f"Pro Trader · Daily Signal Audit",
        f"Date (UTC): {d}",
        "",
        f"Resolved today: {res.get('count', 0)}",
        f"  Wins: {(res.get('by_status') or {}).get('win', 0)}",
        f"  Losses: {(res.get('by_status') or {}).get('loss', 0)}",
        f"  Timeout: {(res.get('by_status') or {}).get('timeout', 0)}",
        f"  Day win rate: {res.get('win_rate') if res.get('win_rate') is not None else 'n/a'}%",
        f"Logged today: {log.get('count', 0)} · Still open: {audit.get('still_open', 0)}",
        "",
        f"Last 7 days: {week.get('count', 0)} signals · "
        f"win rate {week.get('win_rate') if week.get('win_rate') is not None else 'n/a'}%",
    ]

    by_asset = week.get("by_asset") or {}
    if by_asset:
        lines.append("By asset:")
        for aid, st in by_asset.items():
            name = ASSETS.get(aid, {}).get("name", aid)
            lines.append(f"  {name}: {st.get('count')} · {st.get('win_rate')}%")

    by_src = week.get("by_source") or {}
    if by_src:
        lines.append("By source:")
        for src, st in by_src.items():
            lines.append(f"  {src}: {st.get('count')} · {st.get('win_rate')}%")

    accurate = audit.get("accurate") or []
    inaccurate = audit.get("inaccurate") or []
    if accurate:
        lines.append("")
        lines.append("Accurate today:")
        for i in accurate[:6]:
            lines.append(
                f"  ✓ {i.get('asset')} {i.get('signal')} "
                f"({i.get('confidence')}% {i.get('grade')}) — {i.get('reason') or 'win'}"
            )
    if inaccurate:
        lines.append("")
        lines.append("Missed today:")
        for i in inaccurate[:6]:
            lines.append(
                f"  ✗ {i.get('asset')} {i.get('signal')} "
                f"({i.get('confidence')}% {i.get('grade')}) — {i.get('reason') or 'loss'}"
            )

    strong = learn.get("strong_components") or []
    weak = learn.get("weak_components") or []
    if strong or weak:
        lines.append("")
        lines.append("Self-improve weights:")
        if strong:
            lines.append(
                "  Strong: " + ", ".join(f"{c['component']}×{c['weight']}" for c in strong[:4])
            )
        if weak:
            lines.append(
                "  Weak: " + ", ".join(f"{c['component']}×{c['weight']}" for c in weak[:4])
            )
    if cal.get("ready"):
        lines.append("")
        lines.append(f"Calibration: conf×{cal.get('conf_scale')} · sample {cal.get('sample')}")

    lines.append("")
    lines.append("— accuracy feedback loop active")
    return "\n".join(lines)


def store_daily_audit(audit: dict[str, Any], telegram_sent: bool = False) -> None:
    with _lock:
        j = _load()
        audits = j.setdefault("daily_audits", [])
        # replace same date
        audits = [a for a in audits if a.get("date") != audit.get("date")]
        audits.insert(0, {
            "date": audit.get("date"),
            "generated_at": audit.get("generated_at"),
            "telegram_sent": telegram_sent,
            "win_rate": (audit.get("resolved_today") or {}).get("win_rate"),
            "resolved_count": (audit.get("resolved_today") or {}).get("count"),
            "logged_count": (audit.get("logged_today") or {}).get("count"),
            "week_win_rate": (audit.get("last_7_days") or {}).get("win_rate"),
            "summary": {
                "accurate": len(audit.get("accurate") or []),
                "inaccurate": len(audit.get("inaccurate") or []),
                "unclear": len(audit.get("unclear") or []),
            },
        })
        j["daily_audits"] = audits[:60]
        _save(j)


def run_daily_audit_cycle(force: bool = False, send_telegram: bool = True) -> dict[str, Any]:
    """
    Evaluate opens, build today's audit, optionally Telegram once per UTC day.
    Safe to call every few minutes from background worker.
    """
    global _last_daily_sent
    # Always re-score opens when we have no prices — caller should pass prices via evaluate first
    audit = build_daily_audit()
    day = audit.get("date") or _utc_now().date().isoformat()

    # Send after 18:00 UTC (end of NY cash roughly) or force
    hour = _utc_now().hour
    should_send = force or (hour >= 18)
    already = False
    with _lock:
        j = _load()
        for a in j.get("daily_audits") or []:
            if a.get("date") == day and a.get("telegram_sent"):
                already = True
                break
        if _last_daily_sent == day:
            already = True

    telegram_ok = False
    if send_telegram and should_send and not already:
        try:
            from data.trade_alerts import dispatch_alerts, load_config
            cfg = load_config()
            if cfg.get("server_push_ready") or (
                cfg.get("telegram_enabled") and cfg.get("telegram_configured")
            ):
                text = format_daily_audit_telegram(audit)
                # dispatch via generic alert shape
                from data.trade_alerts import send_telegram_message
                ok, err = send_telegram_message(text, cfg)
                telegram_ok = ok
                if ok:
                    _last_daily_sent = day
                else:
                    logger.warning("Daily audit Telegram failed: %s", err)
        except Exception as exc:
            logger.warning("Daily audit Telegram error: %s", exc)

    store_daily_audit(audit, telegram_sent=telegram_ok or already)
    audit["telegram_sent"] = telegram_ok or already
    return audit


def get_audit_status() -> dict[str, Any]:
    with _lock:
        j = _load()
        signals = j.get("signals") or []
        open_n = sum(1 for s in signals if s.get("status") == "open")
        closed = [s for s in signals if s.get("status") in ("win", "loss", "timeout")]
        recent = signals[:25]
    return {
        "journal_path": str(JOURNAL_FILE),
        "total_logged": len(signals),
        "open": open_n,
        "closed": len(closed),
        "overall_win_rate": _win_rate(closed),
        "calibration": get_confidence_calibration(),
        "weights": get_component_weights(),
        "recent": [
            {
                "id": s.get("id"),
                "ts": s.get("ts"),
                "asset": s.get("asset_name") or s.get("asset_id"),
                "signal": s.get("signal"),
                "confidence": s.get("confidence"),
                "grade": s.get("grade"),
                "status": s.get("status"),
                "reason": (s.get("outcome") or {}).get("exit_reason"),
            }
            for s in recent
        ],
        "latest_daily": (j.get("daily_audits") or [None])[0],
    }


def weighted_breakdown_total(breakdown: dict[str, Any], weights: dict[str, float] | None = None) -> float:
    """Recompute total score from breakdown using learned weights (for optional re-score)."""
    weights = weights or get_component_weights()
    total = 0.0
    for k in COMPONENT_KEYS:
        try:
            total += float(breakdown.get(k) or 0) * float(weights.get(k, 1.0))
        except (TypeError, ValueError):
            continue
    return total
