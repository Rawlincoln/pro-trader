"""Manage open positions: partial TP, breakeven SL, signal-based exits."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.mt5_client import MT5Client, OrderResult

logger = logging.getLogger(__name__)

STATE_FILE = Path(__file__).parent.parent / "agent_state.json"


class PositionManager:
    def __init__(self, client: MT5Client, config: dict):
        self.client = client
        self.config = config
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "positions": {},
            "trade_log": [],
            "last_action": None,
            "last_signal": None,
            "status": "idle",
            "updated_at": None,
        }

    def save_state(self, extra: dict | None = None) -> None:
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
        if extra:
            self.state.update(extra)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, default=str)

    def log_trade(self, event: str, details: dict) -> None:
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **details,
        }
        self.state.setdefault("trade_log", []).insert(0, entry)
        self.state["trade_log"] = self.state["trade_log"][:100]
        logger.info("%s: %s", event, details)

    def track_position(self, ticket: int, trade_plan: dict, volume: float) -> None:
        self.state.setdefault("positions", {})[str(ticket)] = {
            "ticket": ticket,
            "direction": trade_plan["action"],
            "entry": trade_plan["entry"],
            "stop_loss": trade_plan["stop_loss"],
            "tp1": trade_plan["take_profit_1"],
            "tp2": trade_plan["take_profit_2"],
            "tp3": trade_plan["take_profit_3"],
            "original_volume": volume,
            "tp1_hit": False,
            "tp2_hit": False,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save_state({"status": "in_trade", "last_action": f"OPENED {trade_plan['action']}"})

    def manage_positions(
        self,
        trade_plan: dict,
        exit_check: dict,
        current_price: float,
        new_signal: str,
        dry_run: bool,
    ) -> list[dict]:
        actions: list[dict] = []
        positions = self.client.get_positions()
        tracked = self.state.get("positions", {})

        for pos in positions:
            ticket = pos["ticket"]
            key = str(ticket)
            meta = tracked.get(key, {})
            direction = pos["type"]

            # Signal reversal exit
            if self.config.get("close_on_signal_reversal") and new_signal in ("BUY", "SELL"):
                if (direction == "BUY" and new_signal == "SELL") or (direction == "SELL" and new_signal == "BUY"):
                    result = self._close(pos, pos["volume"], dry_run, "Signal reversal")
                    actions.append({"action": "close", "reason": "signal_reversal", "result": result})
                    tracked.pop(key, None)
                    continue

            # Strategy exit check
            if exit_check.get("should_exit") and exit_check.get("urgency") == "immediate":
                result = self._close(pos, pos["volume"], dry_run, exit_check.get("reason", "Exit signal"))
                actions.append({"action": "close", "reason": exit_check.get("reason"), "result": result})
                tracked.pop(key, None)
                continue

            # TP1 partial close + breakeven
            tp1 = meta.get("tp1")
            if tp1 and not meta.get("tp1_hit") and self.config.get("partial_close_at_tp1"):
                hit = (direction == "BUY" and current_price >= tp1) or (direction == "SELL" and current_price <= tp1)
                if hit:
                    pct = self.config.get("tp1_close_percent", 50) / 100
                    close_vol = round(pos["volume"] * pct, 2)
                    close_vol = max(close_vol, self.config.get("min_lot_size", 0.01))
                    if close_vol < pos["volume"]:
                        result = self._close(pos, close_vol, dry_run, "TP1 partial")
                        actions.append({"action": "partial_close", "volume": close_vol, "result": result})
                    meta["tp1_hit"] = True

                    if self.config.get("move_sl_to_breakeven_at_tp1"):
                        entry = meta.get("entry") or pos["price_open"]
                        be_result = self._modify_sl(ticket, entry, dry_run)
                        actions.append({"action": "breakeven_sl", "sl": entry, "result": be_result})

                    tracked[key] = meta

            # TP2 partial
            tp2 = meta.get("tp2")
            if tp2 and meta.get("tp1_hit") and not meta.get("tp2_hit"):
                hit = (direction == "BUY" and current_price >= tp2) or (direction == "SELL" and current_price <= tp2)
                if hit:
                    remaining = pos["volume"]
                    close_vol = round(remaining * 0.6, 2)  # close 60% of remaining
                    if close_vol >= self.config.get("min_lot_size", 0.01) and close_vol < remaining:
                        result = self._close(pos, close_vol, dry_run, "TP2 partial")
                        actions.append({"action": "partial_close", "volume": close_vol, "result": result})
                    meta["tp2_hit"] = True
                    tracked[key] = meta

        self.state["positions"] = tracked
        if not positions and not dry_run:
            self.save_state({"status": "watching"})
        return actions

    def _close(self, pos: dict, volume: float, dry_run: bool, reason: str) -> dict:
        if dry_run:
            self.log_trade("DRY_CLOSE", {"ticket": pos["ticket"], "volume": volume, "reason": reason})
            return {"success": True, "dry_run": True, "message": reason}
        result = self.client.close_position(pos["ticket"], volume)
        self.log_trade("CLOSE", {
            "ticket": pos["ticket"], "volume": volume, "reason": reason,
            "success": result.success, "message": result.message,
        })
        return {"success": result.success, "message": result.message}

    def _modify_sl(self, ticket: int, sl: float, dry_run: bool) -> dict:
        if dry_run:
            self.log_trade("DRY_MODIFY_SL", {"ticket": ticket, "sl": sl})
            return {"success": True, "dry_run": True}
        result = self.client.modify_position(ticket, sl=sl)
        self.log_trade("MODIFY_SL", {"ticket": ticket, "sl": sl, "success": result.success})
        return {"success": result.success, "message": result.message}

    def should_open_new(self, signal: str, confidence: float, calendar_risk: dict) -> tuple[bool, str]:
        if signal == "WAIT":
            return False, "Signal is WAIT"
        if confidence < self.config.get("min_confidence", 55):
            return False, f"Confidence {confidence:.1f}% below minimum {self.config['min_confidence']}%"

        if self.config.get("skip_high_impact_events") and calendar_risk.get("risk_level") == "high":
            return False, "High-impact economic events - skipping entry"

        positions = self.client.get_positions()
        max_pos = self.config.get("max_open_positions", 1)
        if len(positions) >= max_pos:
            return False, f"Already have {len(positions)} open position(s)"

        for pos in positions:
            if pos["type"] == signal:
                return False, f"Already in {signal} position"
            if pos["type"] != signal:
                return False, f"Opposite position open - close first"

        return True, "Ready to enter"

    def open_trade(self, trade_plan: dict, dry_run: bool) -> OrderResult | dict:
        direction = trade_plan["action"]
        entry = trade_plan.get("entry") or trade_plan.get("current_price")
        sl = trade_plan.get("stop_loss")
        tp = trade_plan.get("take_profit_2") or trade_plan.get("take_profit_1")

        if not sl:
            return OrderResult(False, message="No stop loss defined - trade blocked for safety")

        if not tp:
            tp = entry + (0.002 if direction == "BUY" else -0.002)

        volume = self.client.calculate_lot_size(entry, sl)

        if dry_run:
            self.log_trade("DRY_OPEN", {
                "direction": direction, "volume": volume,
                "entry": entry, "sl": sl, "tp": tp,
                "confidence": trade_plan.get("confidence"),
            })
            self.save_state({
                "status": "dry_run",
                "last_action": f"DRY RUN: Would {direction} {volume} lots SL={sl} TP={tp}",
                "last_signal": direction,
            })
            return {"success": True, "dry_run": True, "direction": direction, "volume": volume, "sl": sl, "tp": tp}

        result = self.client.open_trade(direction, volume, sl, tp)
        if result.success and result.ticket:
            self.track_position(result.ticket, trade_plan, volume)
            self.log_trade("OPEN", {
                "ticket": result.ticket, "direction": direction,
                "volume": volume, "sl": sl, "tp": tp, "price": result.price,
            })
            self.save_state({"last_signal": direction, "last_action": f"OPENED {direction} #{result.ticket}"})
        else:
            self.log_trade("OPEN_FAILED", {"direction": direction, "message": result.message})
            self.save_state({"last_action": f"FAILED: {result.message}"})

        return result