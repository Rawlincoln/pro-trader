"""
FTMO Standard 2-Step challenge guardrails mapped onto Pro Trader A+ alerts.

Default profile: $25,000 Standard 2-step (cTrader).
When enabled, only FTMO-legal A+ setups surface as TAKE; everything else → WAIT/SKIP.
"""

from __future__ import annotations

import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from analysis.coach import market_session, price_to_pips
from data.assets import get_asset

# --- $25k Standard 2-step (override via env) ---
DEFAULT_PROFILE: dict[str, Any] = {
    "name": "FTMO Standard 2-Step",
    "platform": "cTrader",
    "account_size": 25000.0,
    "phase": "challenge",  # challenge | verification
    "profit_target_pct": 10.0,       # phase 1; verification uses 5
    "verification_target_pct": 5.0,
    "max_daily_loss_pct": 5.0,
    "max_loss_pct": 10.0,
    "min_trading_days": 4,
    "risk_per_trade_pct": 0.45,      # 0.4–0.5% band
    "soft_daily_stop_pct": 2.0,      # stop trading day at -2%
    "soft_daily_target_pct": 2.0,    # bank day around +2%
    "max_trades_per_day": 2,
    "min_rr": 1.5,
    "min_grade": "B",
    "min_confidence": 72.0,
    "require_sfp": True,            # only SFP-confirmed A+ entries
    "require_aligned_bias": True,
    "allowed_assets": ["eurusd", "gold"],  # skip bitcoin on challenge
    "allowed_sessions": ["best", "high"],  # London / London-NY (skip thin)
    "skip_high_impact_news": True,
}


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def ftmo_enabled(config: dict[str, Any] | None = None) -> bool:
    if os.environ.get("FTMO_MODE") is not None:
        return _env_truthy("FTMO_MODE", False)
    if config and config.get("ftmo_mode") is not None:
        return bool(config.get("ftmo_mode"))
    # Default ON for challenge mapping unless explicitly disabled
    return _env_truthy("FTMO_MODE", True)


def load_ftmo_profile(config: dict[str, Any] | None = None) -> dict[str, Any]:
    p = deepcopy(DEFAULT_PROFILE)
    config = config or {}
    # Env overrides
    if os.environ.get("FTMO_ACCOUNT_SIZE"):
        try:
            p["account_size"] = float(os.environ["FTMO_ACCOUNT_SIZE"])
        except ValueError:
            pass
    if os.environ.get("FTMO_PHASE", "").strip().lower() in ("challenge", "verification", "phase1", "phase2"):
        phase = os.environ["FTMO_PHASE"].strip().lower()
        p["phase"] = "verification" if phase in ("verification", "phase2") else "challenge"
    if config.get("ftmo_phase") in ("challenge", "verification"):
        p["phase"] = config["ftmo_phase"]
    if config.get("ftmo_account_size"):
        try:
            p["account_size"] = float(config["ftmo_account_size"])
        except (TypeError, ValueError):
            pass
    size = float(p["account_size"])
    target_pct = (
        float(p["verification_target_pct"])
        if p["phase"] == "verification"
        else float(p["profit_target_pct"])
    )
    risk_pct = float(p["risk_per_trade_pct"])
    p["profit_target_usd"] = round(size * target_pct / 100.0, 2)
    p["max_daily_loss_usd"] = round(size * float(p["max_daily_loss_pct"]) / 100.0, 2)
    p["max_loss_usd"] = round(size * float(p["max_loss_pct"]) / 100.0, 2)
    p["equity_floor"] = round(size - p["max_loss_usd"], 2)
    p["risk_per_trade_usd"] = round(size * risk_pct / 100.0, 2)
    p["soft_daily_stop_usd"] = round(size * float(p["soft_daily_stop_pct"]) / 100.0, 2)
    p["soft_daily_target_usd"] = round(size * float(p["soft_daily_target_pct"]) / 100.0, 2)
    p["target_pct_active"] = target_pct
    return p


def _lot_suggestion(
    asset_id: str,
    entry: float | None,
    stop: float | None,
    risk_usd: float,
) -> dict[str, Any]:
    asset = get_asset(asset_id)
    if not entry or not stop or entry == stop or risk_usd <= 0:
        return {"lots": None, "sl_pips": None, "note": "Need entry + stop for lot size"}
    sl_dist = abs(float(entry) - float(stop))
    sl_pips = price_to_pips(sl_dist, asset_id)
    lots = None
    note = ""
    if asset_id == "eurusd" and sl_pips and sl_pips > 0:
        # ~$10/pip per 1.0 lot
        lots = round(risk_usd / (sl_pips * 10.0), 2)
        lots = max(0.01, lots)
        note = f"~{lots} lots EURUSD for ${risk_usd:.0f} risk ({sl_pips} pip SL)"
    elif asset_id == "gold" and sl_pips and sl_pips > 0:
        lots = round(risk_usd / max(sl_pips * 1.0, 1e-9), 2)
        lots = max(0.01, lots)
        note = f"~{lots} lots Gold (approx) for ${risk_usd:.0f} risk — verify on cTrader"
    else:
        note = "Instrument not sized for FTMO challenge profile"
    return {
        "lots": lots,
        "sl_pips": sl_pips,
        "sl_distance": round(sl_dist, asset["decimals"]),
        "risk_usd": risk_usd,
        "note": note,
    }


def evaluate_ftmo_setup(
    *,
    asset_id: str,
    signal: str,
    confidence: float,
    grade_letter: str | None,
    trade_plan: dict[str, Any] | None,
    a_plus: dict[str, Any] | None,
    calendar_risk: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Return FTMO verdict for the current setup.
    take=True only when all challenge-safe gates pass.
    """
    profile = profile or load_ftmo_profile()
    signal = (signal or "WAIT").upper()
    conf = float(confidence or 0)
    plan = trade_plan or {}
    ap = a_plus or {}
    sfp = ap.get("sfp") or {}
    bias = ap.get("bias") or {}
    session = market_session()
    cal = calendar_risk or {}

    reasons_ok: list[str] = []
    reasons_block: list[str] = []

    # Asset allowlist
    if asset_id not in (profile.get("allowed_assets") or []):
        reasons_block.append(f"{asset_id} not on FTMO challenge allowlist (use EURUSD/Gold)")
    else:
        reasons_ok.append(f"Asset OK ({asset_id})")

    # Session quality
    sq = session.get("quality") or "low"
    if sq not in (profile.get("allowed_sessions") or ["best", "high"]):
        reasons_block.append(f"Session '{session.get('name')}' is thin — skip for FTMO")
    else:
        reasons_ok.append(f"Session OK ({session.get('name')})")

    # News
    if profile.get("skip_high_impact_news") and cal.get("risk_level") == "high":
        reasons_block.append("High-impact calendar — flat for FTMO day")

    # Directional signal
    if signal not in ("BUY", "SELL"):
        reasons_block.append("No BUY/SELL — WAIT")
    else:
        reasons_ok.append(f"Signal {signal}")

    # Grade / confidence
    min_g = str(profile.get("min_grade") or "B").upper()
    rank = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1, "—": 0, "": 0}
    g = (grade_letter or "").upper()[:1]
    if g and g not in ("—", "-") and rank.get(g, 0) < rank.get(min_g, 4):
        reasons_block.append(f"Grade {g} < FTMO min {min_g}")
    elif g in ("A", "B"):
        reasons_ok.append(f"Grade {g}")
    elif not g or g in ("—", "-"):
        # no grade yet — require stronger conf
        if conf < float(profile.get("min_confidence") or 72) + 5:
            reasons_block.append("No coach grade + confidence too low for FTMO")

    if conf < float(profile.get("min_confidence") or 72):
        reasons_block.append(f"Confidence {conf:.0f}% < {profile['min_confidence']:.0f}%")
    else:
        reasons_ok.append(f"Confidence {conf:.0f}%")

    # A+ SFP required
    if profile.get("require_sfp"):
        if not (sfp.get("detected") and sfp.get("aligned_with_bias") and sfp.get("quality") in ("A", "B")):
            reasons_block.append("Need A+ SFP (A/B) at liquidity aligned with bias")
        else:
            reasons_ok.append(f"SFP {sfp.get('quality')} @ {sfp.get('level_kind')}")

    if profile.get("require_aligned_bias"):
        bdir = (bias.get("bias") or "neutral").lower()
        if signal == "BUY" and bdir == "bearish":
            reasons_block.append("BUY fights bearish HTF bias")
        elif signal == "SELL" and bdir == "bullish":
            reasons_block.append("SELL fights bullish HTF bias")
        elif bdir in ("bullish", "bearish", "neutral"):
            reasons_ok.append(f"Bias {bdir}/{bias.get('strength', '')}")

    # Target already taken → skip or size caution (we skip for challenge safety)
    if bias.get("target_ready") is False and signal in ("BUY", "SELL"):
        reasons_block.append("Primary daily liquidity already taken — skip")

    # R:R
    rr = plan.get("risk_reward")
    min_rr = float(profile.get("min_rr") or 1.5)
    if rr is not None:
        try:
            if float(rr) < min_rr:
                reasons_block.append(f"R:R 1:{rr} < 1:{min_rr}")
            else:
                reasons_ok.append(f"R:R 1:{rr}")
        except (TypeError, ValueError):
            pass

    # Poor plan status
    status = plan.get("position_status") or ""
    if status == "SKIP_POOR_RR":
        reasons_block.append("Plan marked SKIP_POOR_RR")

    entry = plan.get("entry") or sfp.get("entry")
    stop = plan.get("stop_loss") or sfp.get("stop")
    sizing = _lot_suggestion(asset_id, entry, stop, float(profile["risk_per_trade_usd"]))

    take = signal in ("BUY", "SELL") and len(reasons_block) == 0
    verdict = "TAKE" if take else "SKIP"
    if signal == "WAIT" and not reasons_block:
        verdict = "WAIT"

    phase_label = "Phase 1 Challenge" if profile["phase"] == "challenge" else "Phase 2 Verification"

    return {
        "enabled": True,
        "verdict": verdict,
        "take": take,
        "profile": {
            "name": profile["name"],
            "platform": profile["platform"],
            "account_size": profile["account_size"],
            "phase": profile["phase"],
            "phase_label": phase_label,
            "profit_target_usd": profile["profit_target_usd"],
            "profit_target_pct": profile["target_pct_active"],
            "max_daily_loss_usd": profile["max_daily_loss_usd"],
            "max_loss_usd": profile["max_loss_usd"],
            "equity_floor": profile["equity_floor"],
            "risk_per_trade_usd": profile["risk_per_trade_usd"],
            "risk_per_trade_pct": profile["risk_per_trade_pct"],
            "soft_daily_stop_usd": profile["soft_daily_stop_usd"],
            "soft_daily_target_usd": profile["soft_daily_target_usd"],
            "min_trading_days": profile["min_trading_days"],
            "max_trades_per_day": profile["max_trades_per_day"],
        },
        "session": session,
        "reasons_ok": reasons_ok,
        "reasons_block": reasons_block,
        "sizing": sizing,
        "rules_card": [
            f"{phase_label}: +${profile['profit_target_usd']:.0f} target",
            f"Hard daily −${profile['max_daily_loss_usd']:.0f} · overall floor ${profile['equity_floor']:.0f}",
            f"Risk/trade ${profile['risk_per_trade_usd']:.0f} ({profile['risk_per_trade_pct']}%)",
            f"Soft day stop −${profile['soft_daily_stop_usd']:.0f} · soft day goal +${profile['soft_daily_target_usd']:.0f}",
            f"Max {profile['max_trades_per_day']} trades/day · ≥{profile['min_trading_days']} trading days",
            "cTrader: set SL first, then size to risk $",
        ],
        "checklist": [
            {"label": "Asset allowlist", "ok": asset_id in (profile.get("allowed_assets") or [])},
            {"label": "London / overlap session", "ok": sq in (profile.get("allowed_sessions") or [])},
            {"label": "Grade B+", "ok": rank.get(g, 0) >= rank.get(min_g, 4) or (not g and conf >= 77)},
            {"label": f"Conf ≥{profile['min_confidence']:.0f}%", "ok": conf >= float(profile["min_confidence"])},
            {
                "label": "A+ SFP A/B aligned",
                "ok": bool(
                    sfp.get("detected")
                    and sfp.get("aligned_with_bias")
                    and sfp.get("quality") in ("A", "B")
                ),
            },
            {
                "label": "R:R ≥ 1.5",
                "ok": True if rr is None else float(rr) >= min_rr,
            },
            {
                "label": "No high-impact news block",
                "ok": not (
                    profile.get("skip_high_impact_news") and cal.get("risk_level") == "high"
                ),
            },
        ],
    }


def apply_ftmo_gate(
    asset_id: str,
    signal: str,
    confidence: float,
    trade_plan: dict[str, Any],
    a_plus: dict[str, Any] | None,
    coach: dict[str, Any] | None = None,
    calendar_risk: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[str, float, dict[str, Any], list[str]]:
    """
    If FTMO mode on: force WAIT when not TAKE-legal; attach ftmo blob for UI/Telegram.
    Returns (signal, confidence, ftmo_eval, notes).
    """
    notes: list[str] = []
    if not ftmo_enabled(config):
        return signal, confidence, {"enabled": False}, notes

    profile = load_ftmo_profile(config)
    grade = ((coach or {}).get("grade") or {}).get("letter")
    evaluation = evaluate_ftmo_setup(
        asset_id=asset_id,
        signal=signal,
        confidence=confidence,
        grade_letter=grade,
        trade_plan=trade_plan,
        a_plus=a_plus,
        calendar_risk=calendar_risk,
        profile=profile,
    )

    if evaluation["take"]:
        notes.append(
            f"FTMO TAKE · risk ${profile['risk_per_trade_usd']:.0f} · "
            f"{(evaluation.get('sizing') or {}).get('note', '')}"
        )
        return signal, confidence, evaluation, notes

    # Not legal → WAIT
    if signal in ("BUY", "SELL"):
        notes.append("FTMO SKIP → WAIT: " + "; ".join(evaluation["reasons_block"][:3]))
    else:
        notes.append("FTMO mode: waiting for legal A+ setup")
    evaluation["verdict"] = "SKIP" if signal in ("BUY", "SELL") else "WAIT"
    evaluation["take"] = False
    return "WAIT", min(float(confidence or 0), 45.0), evaluation, notes


def ftmo_telegram_suffix(ftmo: dict[str, Any] | None) -> str:
    if not ftmo or not ftmo.get("enabled"):
        return ""
    if not ftmo.get("take"):
        return ""
    p = ftmo.get("profile") or {}
    s = ftmo.get("sizing") or {}
    lines = [
        "",
        "— FTMO $25k guard —",
        f"TAKE · risk ${p.get('risk_per_trade_usd', 0):.0f}",
    ]
    if s.get("lots") is not None:
        lines.append(f"cTrader size ≈ {s['lots']} lots · SL {s.get('sl_pips')} pips")
    lines.append(
        f"Soft day −${p.get('soft_daily_stop_usd', 0):.0f} / +${p.get('soft_daily_target_usd', 0):.0f}"
    )
    lines.append("Educational only · respect hard −5% daily / −10% max")
    return "\n".join(lines)
