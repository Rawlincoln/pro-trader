"""Agent configuration loader."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"

DEFAULT_CONFIG = {
    "broker": "XM",
    "mt5_path": "",
    "account_login": 0,
    "account_password": "",
    "investor_password": "",
    "account_server": "",
    "myfxbook_email": "",
    "myfxbook_password": "",
    "myfxbook_account_id": 0,
    "ledger_sync_minutes": 5,
    "symbol": "EURUSD",
    "magic_number": 20250618,
    "enabled": False,
    "dry_run": True,
    "min_confidence": 55.0,
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


def _merge_env_config(cfg: dict) -> dict:
    """Env vars override config.json — needed on Render where config.json is not deployed."""
    email = os.environ.get("MYFXBOOK_EMAIL", "").strip()
    password = os.environ.get("MYFXBOOK_PASSWORD", "").strip()
    account_id = os.environ.get("MYFXBOOK_ACCOUNT_ID", "").strip()
    if email:
        cfg["myfxbook_email"] = email
    if password:
        cfg["myfxbook_password"] = password
    if account_id:
        try:
            cfg["myfxbook_account_id"] = int(account_id)
        except ValueError:
            pass
    return cfg


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            user = json.load(f)
        cfg = deepcopy(DEFAULT_CONFIG)
        cfg.update(user)
        return _merge_env_config(cfg)
    return _merge_env_config(deepcopy(DEFAULT_CONFIG))


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def save_myfxbook_config(email: str, password: str, account_id: int | str = 0) -> dict:
    """Persist Myfxbook credentials to config.json."""
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


def myfxbook_config_public() -> dict:
    """Return saved Myfxbook settings without exposing the password."""
    cfg = load_config()
    email = (cfg.get("myfxbook_email") or "").strip()
    has_password = bool(cfg.get("myfxbook_password"))
    account_id = cfg.get("myfxbook_account_id") or 0
    return {
        "email": email,
        "has_password": has_password,
        "account_id": account_id,
        "configured": bool(email and has_password and account_id),
        "saved_permanently": CONFIG_PATH.exists() and email and has_password,
        "config_path": str(CONFIG_PATH),
        "config_exists": CONFIG_PATH.exists(),
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