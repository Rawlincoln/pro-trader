"""MetaTrader 5 client for XM and other MT5 brokers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None  # type: ignore


@dataclass
class OrderResult:
    success: bool
    ticket: int | None = None
    message: str = ""
    price: float | None = None
    volume: float | None = None


class MT5Client:
    def __init__(self, config: dict):
        self.config = config
        self.connected = False
        self.symbol = config.get("symbol", "EURUSD")
        self._resolved_symbol: str | None = None

    def _find_mt5_path(self) -> str | None:
        configured = self.config.get("mt5_path", "").strip()
        if configured and os.path.isfile(configured):
            return configured

        candidates = [
            r"C:\Program Files\XM Global MT5\terminal64.exe",
            r"C:\Program Files\XM\terminal64.exe",
            os.path.expandvars(r"%APPDATA%\XM Global MT5\terminal64.exe"),
            os.path.expandvars(r"%APPDATA%\MetaQuotes\Terminal\*\terminal64.exe"),
        ]
        # Auto-detect any MT5 in AppData
        appdata = os.path.expandvars(r"%APPDATA%")
        if os.path.isdir(appdata):
            for name in os.listdir(appdata):
                if "metatrader" in name.lower() or "mt5" in name.lower() or "xm" in name.lower():
                    path = os.path.join(appdata, name, "terminal64.exe")
                    if os.path.isfile(path):
                        candidates.append(path)

        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def connect(self) -> tuple[bool, str]:
        if mt5 is None:
            return False, "MetaTrader5 package not installed"

        path = self._find_mt5_path()
        init_kwargs: dict[str, Any] = {}
        if path:
            init_kwargs["path"] = path
            logger.info("Using MT5 path: %s", path)

        login = int(self.config.get("account_login") or 0)
        password = self.config.get("account_password", "")
        server = self.config.get("account_server", "")

        if login and password and server:
            ok = mt5.initialize(login=login, password=password, server=server, **init_kwargs)
        else:
            ok = mt5.initialize(**init_kwargs)

        if not ok:
            err = mt5.last_error()
            return False, f"MT5 init failed: {err}. Open XM MT5, log in, enable Algo Trading."

        self.connected = True
        self._resolved_symbol = self._resolve_symbol()
        info = mt5.account_info()
        if info:
            mode = "DEMO" if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "LIVE"
            return True, f"Connected to {info.server} ({mode}) balance={info.balance:.2f} {info.currency}"
        return True, "Connected to MT5"

    def disconnect(self) -> None:
        if mt5 and self.connected:
            mt5.shutdown()
        self.connected = False

    def _resolve_symbol(self) -> str:
        if not mt5:
            return self.symbol
        preferred = [self.symbol, "EURUSD", "EURUSDm", "EURUSD.", "EURUSD#", "EURUSD.r"]
        symbols = {s.name: s for s in (mt5.symbols_get() or [])}
        for name in preferred:
            if name in symbols:
                mt5.symbol_select(name, True)
                return name
        for name in symbols:
            if "EURUSD" in name.upper():
                mt5.symbol_select(name, True)
                return name
        return self.symbol

    def get_symbol_info(self) -> dict | None:
        if not mt5 or not self.connected:
            return None
        sym = self._resolved_symbol or self.symbol
        info = mt5.symbol_info(sym)
        if not info:
            return None
        return {
            "name": info.name,
            "bid": info.bid,
            "ask": info.ask,
            "point": info.point,
            "digits": info.digits,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "trade_contract_size": info.trade_contract_size,
        }

    def get_account(self) -> dict | None:
        if not mt5 or not self.connected:
            return None
        info = mt5.account_info()
        if not info:
            return None
        return {
            "login": info.login,
            "balance": info.balance,
            "equity": info.equity,
            "margin_free": info.margin_free,
            "currency": info.currency,
            "is_demo": info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
            "server": info.server,
        }

    def get_positions(self, magic: int | None = None) -> list[dict]:
        if not mt5 or not self.connected:
            return []
        sym = self._resolved_symbol or self.symbol
        magic = magic if magic is not None else self.config.get("magic_number", 0)
        positions = mt5.positions_get(symbol=sym) or []
        result = []
        for p in positions:
            if magic and p.magic != magic:
                continue
            result.append({
                "ticket": p.ticket,
                "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                "volume": p.volume,
                "price_open": p.price_open,
                "price_current": p.price_current,
                "sl": p.sl,
                "tp": p.tp,
                "profit": p.profit,
                "comment": p.comment,
                "time": datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
            })
        return result

    def _normalize_volume(self, volume: float) -> float:
        info = self.get_symbol_info()
        if not info:
            return round(volume, 2)
        step = info["volume_step"]
        min_v = max(info["volume_min"], self.config.get("min_lot_size", 0.01))
        max_v = min(info["volume_max"], self.config.get("max_lot_size", 1.0))
        volume = max(min_v, min(max_v, volume))
        steps = round(volume / step)
        return round(steps * step, 2)

    def calculate_lot_size(self, entry: float, stop_loss: float) -> float:
        account = self.get_account()
        if not account:
            return self.config.get("min_lot_size", 0.01)

        risk_pct = self.config.get("risk_percent", 1.0) / 100
        risk_amount = account["balance"] * risk_pct
        sl_distance = abs(entry - stop_loss)
        if sl_distance <= 0:
            return self.config.get("min_lot_size", 0.01)

        sym_info = self.get_symbol_info()
        if sym_info:
            # Approximate pip value for EURUSD
            pip_size = 0.0001
            sl_pips = sl_distance / pip_size
            pip_value_per_lot = 10.0  # standard for EURUSD on USD account
            if sl_pips > 0:
                lots = risk_amount / (sl_pips * pip_value_per_lot)
                return self._normalize_volume(lots)

        return self._normalize_volume(self.config.get("min_lot_size", 0.01))

    def _filling_mode(self) -> int:
        if not mt5:
            return 0
        sym = self._resolved_symbol or self.symbol
        info = mt5.symbol_info(sym)
        if not info:
            return mt5.ORDER_FILLING_IOC
        if info.filling_mode & mt5.ORDER_FILLING_IOC:
            return mt5.ORDER_FILLING_IOC
        if info.filling_mode & mt5.ORDER_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def open_trade(
        self,
        direction: str,
        volume: float,
        stop_loss: float,
        take_profit: float,
        comment: str = "EURUSD-Agent",
    ) -> OrderResult:
        if not mt5 or not self.connected:
            return OrderResult(False, message="Not connected to MT5")

        sym = self._resolved_symbol or self.symbol
        tick = mt5.symbol_info_tick(sym)
        if not tick:
            return OrderResult(False, message=f"No tick data for {sym}")

        volume = self._normalize_volume(volume)
        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick.ask if direction == "BUY" else tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": round(stop_loss, 5),
            "tp": round(take_profit, 5),
            "deviation": 20,
            "magic": self.config.get("magic_number", 20250618),
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(),
        }

        result = mt5.order_send(request)
        if result is None:
            return OrderResult(False, message=f"order_send failed: {mt5.last_error()}")
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return OrderResult(False, message=f"Order rejected: {result.retcode} - {result.comment}")

        return OrderResult(
            success=True,
            ticket=result.order,
            message=result.comment,
            price=result.price,
            volume=volume,
        )

    def close_position(self, ticket: int, volume: float | None = None) -> OrderResult:
        if not mt5 or not self.connected:
            return OrderResult(False, message="Not connected")

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return OrderResult(False, message=f"Position {ticket} not found")

        pos = positions[0]
        sym = pos.symbol
        tick = mt5.symbol_info_tick(sym)
        if not tick:
            return OrderResult(False, message="No tick data")

        close_volume = volume or pos.volume
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "position": ticket,
            "volume": close_volume,
            "type": close_type,
            "price": price,
            "deviation": 20,
            "magic": pos.magic,
            "comment": "EURUSD-Agent-Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(),
        }

        result = mt5.order_send(request)
        if result is None:
            return OrderResult(False, message=str(mt5.last_error()))
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return OrderResult(False, message=f"Close failed: {result.comment}")
        return OrderResult(True, ticket=ticket, message="Closed", price=result.price, volume=close_volume)

    def modify_position(self, ticket: int, sl: float | None = None, tp: float | None = None) -> OrderResult:
        if not mt5 or not self.connected:
            return OrderResult(False, message="Not connected")

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return OrderResult(False, message="Position not found")

        pos = positions[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": pos.symbol,
            "sl": round(sl, 5) if sl is not None else pos.sl,
            "tp": round(tp, 5) if tp is not None else pos.tp,
            "magic": pos.magic,
        }

        result = mt5.order_send(request)
        if result is None:
            return OrderResult(False, message=str(mt5.last_error()))
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return OrderResult(False, message=f"Modify failed: {result.comment}")
        return OrderResult(True, ticket=ticket, message="SL/TP updated")