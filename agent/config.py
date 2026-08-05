"""Agent configuration loader."""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"
IS_CLOUD = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))

DEFAULT_CONFIG = {
    "broker": "XM",
    "mt5_path": "",
    # Desktop MT5 launches a window when initialize() runs — keep off for phone/Myfxbook users
    "mt5_enabled": False,
    "account_login": 0,
    "account_password": "",
    "investor_password": "",
    "account_server": "",
    "myfxbook_email": "",
    "myfxbook_password": "",
    "myfxbook_account_id": 0,
    "myfxbook_auto_sync": True,
    "ledger_sync_minutes": 2,
    "symbol": "EURUSD",
    "magic_number": 20250618,
    "enabled": False,
    "dry_run": True,
    "min_confidence": 62.0,
    "risk_percent": 1.0,
    "max_lot_size": 1.0,
    "min_lot_size": 0.01,
    "max_open_positions": 1,
    "check_interval_seconds": 60,
    "allow_live_trading": False,
    "partial_close_at_tp1": True,
    "tp1_close_percent": 50,
    "move_sl_to_breakeven_at_tp1": True,
    "close_on_signal_reversal": True,
    "skip_high_impact_events": True,
}


def _env(name: str, *aliases: str) -> str:
    """Read env var; strip whitespace and surrounding quotes (Render paste issues)."""
    for key in (name, *aliases):
        raw = os.environ.get(key)
        if raw is None:
            continue
        val = str(raw).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1].strip()
        if val:
            return val
    return ""


def _merge_env_config(cfg: dict) -> dict:
    """Env vars override config.json — required on Render (config.json is gitignored)."""
    email = _env("MYFXBOOK_EMAIL", "myfxbook_email")
    password = _env("MYFXBOOK_PASSWORD", "myfxbook_password")
    account_id = _env("MYFXBOOK_ACCOUNT_ID", "myfxbook_account_id")
    if email:
        cfg["myfxbook_email"] = email
    if password:
        cfg["myfxbook_password"] = password
    if account_id:
        try:
            cfg["myfxbook_account_id"] = int(account_id)
        except ValueError:
            logger.warning("MYFXBOOK_ACCOUNT_ID is not an integer: %r", account_id)
    auto = _env("MYFXBOOK_AUTO_SYNC")
    if auto:
        cfg["myfxbook_auto_sync"] = auto.lower() in ("1", "true", "yes", "on")
    return cfg


def load_config() -> dict:
    cfg = deepcopy(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                cfg.update(user)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read %s: %s", CONFIG_PATH, exc)
    return _merge_env_config(cfg)


def save_config(cfg: dict) -> None:
    """Write config.json (works on Render until next deploy; use env vars for permanent)."""
    # Do not write secrets that only live in env into the file if we're cloud-only
    to_save = {k: v for k, v in cfg.items() if k in DEFAULT_CONFIG or k.startswith("myfxbook")}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(to_save, f, indent=2)


def save_myfxbook_config(email: str, password: str, account_id: int | str = 0) -> dict:
    """Persist Myfxbook credentials to config.json (ephemeral on Render — also set env vars)."""
    cfg = load_config()
    cfg["myfxbook_email"] = (email or "").strip()
    if password:
        cfg["myfxbook_password"] = password
    if account_id:
        try:
            cfg["myfxbook_account_id"] = int(account_id)
        except (TypeError, ValueError):
            pass
    save_config(cfg)
    return cfg


def myfxbook_credentials_hint() -> str:
    """Human-readable setup help (local vs Render)."""
    if IS_CLOUD:
        return (
            "On Render: Dashboard → pro-trader → Environment → add "
            "MYFXBOOK_EMAIL, MYFXBOOK_PASSWORD, MYFXBOOK_ACCOUNT_ID (e.g. 12098095) "
            "→ Save → Manual Deploy. "
            "Or open /balance and use “Myfxbook connection” (lasts until next redeploy)."
        )
    return (
        f"Add myfxbook_email and myfxbook_password to {CONFIG_PATH}, "
        "or set MYFXBOOK_EMAIL / MYFXBOOK_PASSWORD env vars, then restart."
    )


def myfxbook_config_public() -> dict:
    """Return saved Myfxbook settings without exposing the password."""
    cfg = load_config()
    email = (cfg.get("myfxbook_email") or "").strip()
    has_password = bool(cfg.get("myfxbook_password"))
    account_id = cfg.get("myfxbook_account_id") or 0
    from_env = bool(_env("MYFXBOOK_EMAIL") or _env("MYFXBOOK_PASSWORD"))
    return {
        "email": email,
        "has_password": has_password,
        "account_id": account_id,
        # Email + password enough; account can auto-resolve
        "configured": bool(email and has_password),
        "saved_permanently": from_env or (CONFIG_PATH.exists() and email and has_password),
        "from_env": from_env,
        "is_cloud": IS_CLOUD,
        "config_path": str(CONFIG_PATH),
        "config_exists": CONFIG_PATH.exists(),
        "hint": myfxbook_credentials_hint() if not (email and has_password) else "",
    }


def ensure_example_config() -> None:
    if not EXAMPLE_PATH.exists():
        example = deepcopy(DEFAULT_CONFIG)
        example.update({
            "enabled": True,
            "dry_run": True,
            "mt5_path": r"C:\Program Files\XM Global MT5\terminal64.exe",
            "account_login": 12345678,
            "account_password": "YOUR_MAIN_PASSWORD",
            "investor_password": "YOUR_INVESTOR_PASSWORD_READ_ONLY",
            "account_server": "XMGlobal-MT5 3",
            "allow_live_trading": False,
        })
        with open(EXAMPLE_PATH, "w", encoding="utf-8") as f:
            json.dump(example, f, indent=2)