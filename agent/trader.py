"""XM Trading Agent - executes EUR/USD strategy with mandatory stop loss."""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from agent.config import ensure_example_config, load_config
from agent.mt5_client import MT5Client
from agent.position_manager import PositionManager
from analysis.signals import build_full_analysis
from data.calendar import calendar_risk_assessment, fetch_calendar
from data.fetcher import fetch_ohlc
from data.news import fetch_news, news_sentiment_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT / "agent.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("trader")


def run_cycle(client: MT5Client, manager: PositionManager, config: dict, dry_run: bool) -> None:
    df_1h = fetch_ohlc("1h")
    df_4h = fetch_ohlc("4h")
    news_sent = news_sentiment_summary(fetch_news(8))
    cal_risk = calendar_risk_assessment(fetch_calendar(3))

    full = build_full_analysis(df_1h, df_4h, news_sent, cal_risk)
    technical = full["technical"]
    trade_plan = full["trade_plan"]
    exit_check = full["exit_check"]

    signal = technical["signal"]
    confidence = technical["confidence"]
    price = trade_plan.get("current_price") or full["analysis_1h"]["indicators"]["price"]

    account = client.get_account()
    positions = client.get_positions()

    logger.info(
        "Signal=%s Confidence=%.1f%% Price=%.5f Positions=%d",
        signal, confidence, price, len(positions),
    )

    # Manage existing positions first
    actions = manager.manage_positions(trade_plan, exit_check, price, signal, dry_run)
    for act in actions:
        logger.info("Position action: %s", act)

    # Check for new entry
    can_open, reason = manager.should_open_new(signal, confidence, cal_risk)
    if can_open:
        logger.info("Opening trade: %s (SL=%s)", signal, trade_plan.get("stop_loss"))
        result = manager.open_trade(trade_plan, dry_run)
        logger.info("Open result: %s", result)
    else:
        manager.save_state({
            "status": "watching" if not positions else "managing",
            "last_signal": signal,
            "last_action": reason,
            "current_analysis": {
                "signal": signal,
                "confidence": confidence,
                "entry": trade_plan.get("entry"),
                "stop_loss": trade_plan.get("stop_loss"),
                "tp1": trade_plan.get("take_profit_1"),
                "tp2": trade_plan.get("take_profit_2"),
                "tp3": trade_plan.get("take_profit_3"),
                "price": price,
            },
            "account": account,
            "open_positions": positions,
            "dry_run": dry_run,
        })
        logger.info("No entry: %s", reason)


def main() -> None:
    ensure_example_config()
    config = load_config()

    print("\n" + "=" * 60)
    print("  EUR/USD XM Trading Agent")
    print("=" * 60)

    if not config.get("enabled"):
        print("\n  Agent is DISABLED in config.json")
        print("  Copy config.example.json -> config.json and set enabled=true")
        print("=" * 60 + "\n")
        return

    dry_run = config.get("dry_run", True)
    if not config.get("allow_live_trading"):
        dry_run = True
        print("  Mode: DRY RUN (safe simulation)")
        print("  Set allow_live_trading=true in config for real trades")
    else:
        print("  Mode: LIVE TRADING")

    print(f"  Min confidence: {config.get('min_confidence')}%")
    print(f"  Risk per trade: {config.get('risk_percent')}%")
    print(f"  Check interval: {config.get('check_interval_seconds')}s")
    print("  Stop loss: REQUIRED on every trade")
    print("=" * 60 + "\n")

    client = MT5Client(config)
    ok, msg = client.connect()

    if not ok:
        logger.warning("MT5 not connected: %s", msg)
        print(f"  MT5: {msg}")
        print("  Running in OFFLINE DRY-RUN mode (simulated trades only)")
        print("  To trade on XM:")
        print("    1. Install XM Global MT5 from xm.com")
        print("    2. Log in to your XM account")
        print("    3. Tools -> Options -> Expert Advisors -> Allow algo trading")
        print("    4. Fill in config.json with your login/server")
        print()
        dry_run = True
    else:
        print(f"  MT5: {msg}")
        account = client.get_account()
        if account and not account.get("is_demo") and not config.get("allow_live_trading"):
            print("  LIVE account detected but allow_live_trading=false - using dry run")
            dry_run = True

    manager = PositionManager(client, config)
    interval = config.get("check_interval_seconds", 60)

    manager.save_state({
        "status": "running",
        "dry_run": dry_run,
        "connected": ok,
        "started_at": datetime.now(timezone.utc).isoformat(),
    })

    try:
        while True:
            try:
                run_cycle(client, manager, config, dry_run)
            except Exception as exc:
                logger.exception("Cycle error: %s", exc)
                manager.save_state({"status": "error", "last_action": str(exc)})
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nAgent stopped.")
    finally:
        client.disconnect()
        manager.save_state({"status": "stopped"})


if __name__ == "__main__":
    main()