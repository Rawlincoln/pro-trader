"""Technical indicators including volume-based analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "histogram": histogram})


def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    mid = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    return pd.DataFrame({
        "upper": mid + std_dev * std,
        "middle": mid,
        "lower": mid - std_dev * std,
        "width": (mid + std_dev * std) - (mid - std_dev * std),
    })


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0, np.nan)
    d = k.rolling(d_period).mean()
    return pd.DataFrame({"k": k, "d": d})


def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma_tp = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_max = df["high"].rolling(period).max()
    low_min = df["low"].rolling(period).min()
    return -100 * (high_max - df["close"]) / (high_max - low_min).replace(0, np.nan)


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)

    tr = atr(df, period)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1 / period, adjust=False).mean()
    return pd.DataFrame({"adx": adx_val, "plus_di": plus_di, "minus_di": minus_di})


def mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    raw_money = tp * df["volume"]
    delta = tp.diff()
    pos_flow = raw_money.where(delta > 0, 0.0)
    neg_flow = raw_money.where(delta < 0, 0.0).abs()
    pos_sum = pos_flow.rolling(period).sum()
    neg_sum = neg_flow.rolling(period).sum()
    ratio = pos_sum / neg_sum.replace(0, np.nan)
    return 100 - (100 / (1 + ratio))


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    cum_tp_vol = (tp * df["volume"]).cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    high9 = df["high"].rolling(9).max()
    low9 = df["low"].rolling(9).min()
    tenkan = (high9 + low9) / 2

    high26 = df["high"].rolling(26).max()
    low26 = df["low"].rolling(26).min()
    kijun = (high26 + low26) / 2

    senkou_a = ((tenkan + kijun) / 2).shift(26)
    high52 = df["high"].rolling(52).max()
    low52 = df["low"].rolling(52).min()
    senkou_b = ((high52 + low52) / 2).shift(26)

    return pd.DataFrame({
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
    })


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]

    if "volume" not in out.columns:
        out["volume"] = (out["high"] - out["low"]).abs() * 1_000_000

    for period in (9, 20, 50, 200):
        out[f"sma_{period}"] = sma(close, period)
        out[f"ema_{period}"] = ema(close, period)

    out["rsi_14"] = rsi(close, 14)
    macd_df = macd(close)
    out["macd"] = macd_df["macd"]
    out["macd_signal"] = macd_df["signal"]
    out["macd_hist"] = macd_df["histogram"]

    bb = bollinger_bands(close)
    out["bb_upper"] = bb["upper"]
    out["bb_middle"] = bb["middle"]
    out["bb_lower"] = bb["lower"]
    out["bb_width"] = bb["width"]

    out["atr_14"] = atr(out, 14)
    stoch = stochastic(out)
    out["stoch_k"] = stoch["k"]
    out["stoch_d"] = stoch["d"]

    out["cci_20"] = cci(out, 20)
    out["williams_r"] = williams_r(out, 14)
    adx_df = adx(out, 14)
    out["adx"] = adx_df["adx"]
    out["plus_di"] = adx_df["plus_di"]
    out["minus_di"] = adx_df["minus_di"]

    out["mfi_14"] = mfi(out, 14)
    out["obv"] = obv(out)
    out["obv_ema"] = ema(out["obv"], 20)
    out["vwap"] = vwap(out)
    out["volume_sma_20"] = sma(out["volume"], 20)

    ichi = ichimoku(out)
    out["tenkan"] = ichi["tenkan"]
    out["kijun"] = ichi["kijun"]
    out["senkou_a"] = ichi["senkou_a"]
    out["senkou_b"] = ichi["senkou_b"]

    return out


def trend_from_emas(row: pd.Series) -> str:
    ema20 = row.get("ema_20")
    ema50 = row.get("ema_50")
    ema200 = row.get("ema_200")
    price = row.get("close")

    if pd.isna(ema20) or pd.isna(ema50) or pd.isna(ema200):
        return "neutral"

    if price > ema20 > ema50 > ema200:
        return "strong_bullish"
    if price > ema20 > ema50:
        return "bullish"
    if price < ema20 < ema50 < ema200:
        return "strong_bearish"
    if price < ema20 < ema50:
        return "bearish"
    return "neutral"


def _volume_signal(row: pd.Series, prev: pd.Series) -> dict:
    vol = float(row.get("volume", 0))
    vol_sma = float(row.get("volume_sma_20", 0))
    ratio = vol / vol_sma if vol_sma > 0 else 1.0

    obv = row.get("obv")
    obv_ema = row.get("obv_ema")
    obv_trend = "neutral"
    if not pd.isna(obv) and not pd.isna(obv_ema):
        if obv > obv_ema:
            obv_trend = "bullish"
        elif obv < obv_ema:
            obv_trend = "bearish"

    price_up = row["close"] > prev["close"]
    vol_confirm = "neutral"
    if ratio >= 1.2:
        vol_confirm = "bullish" if price_up else "bearish"
    elif ratio <= 0.8:
        vol_confirm = "weak"

    return {
        "volume": round(vol, 0),
        "volume_sma": round(vol_sma, 0),
        "volume_ratio": round(ratio, 2),
        "volume_signal": "high" if ratio >= 1.2 else "low" if ratio <= 0.8 else "normal",
        "volume_confirmation": vol_confirm,
        "obv_trend": obv_trend,
    }


def indicator_snapshot(df: pd.DataFrame, asset: dict | None = None) -> dict:
    if df.empty or len(df) < 2:
        return {}

    decimals = asset.get("decimals", 5) if asset else 5
    row = df.iloc[-1]
    prev = df.iloc[-2]

    rsi_val = float(row["rsi_14"]) if not pd.isna(row.get("rsi_14")) else None
    macd_cross = None
    if not pd.isna(row.get("macd")) and not pd.isna(prev.get("macd")):
        if prev["macd"] <= prev["macd_signal"] and row["macd"] > row["macd_signal"]:
            macd_cross = "bullish_cross"
        elif prev["macd"] >= prev["macd_signal"] and row["macd"] < row["macd_signal"]:
            macd_cross = "bearish_cross"

    ema_cross = None
    if not pd.isna(row.get("ema_20")) and not pd.isna(prev.get("ema_20")):
        if prev["ema_20"] <= prev["ema_50"] and row["ema_20"] > row["ema_50"]:
            ema_cross = "golden_cross"
        elif prev["ema_20"] >= prev["ema_50"] and row["ema_20"] < row["ema_50"]:
            ema_cross = "death_cross"

    rsi_signal = "neutral"
    if rsi_val is not None:
        if rsi_val >= 70:
            rsi_signal = "overbought"
        elif rsi_val <= 30:
            rsi_signal = "oversold"
        elif rsi_val > 50:
            rsi_signal = "bullish"
        else:
            rsi_signal = "bearish"

    cci_val = float(row["cci_20"]) if not pd.isna(row.get("cci_20")) else None
    cci_signal = "neutral"
    if cci_val is not None:
        if cci_val >= 100:
            cci_signal = "overbought"
        elif cci_val <= -100:
            cci_signal = "oversold"
        elif cci_val > 0:
            cci_signal = "bullish"
        else:
            cci_signal = "bearish"

    wr = float(row["williams_r"]) if not pd.isna(row.get("williams_r")) else None
    wr_signal = "neutral"
    if wr is not None:
        if wr >= -20:
            wr_signal = "overbought"
        elif wr <= -80:
            wr_signal = "oversold"
        elif wr > -50:
            wr_signal = "bullish"
        else:
            wr_signal = "bearish"

    mfi_val = float(row["mfi_14"]) if not pd.isna(row.get("mfi_14")) else None
    mfi_signal = "neutral"
    if mfi_val is not None:
        if mfi_val >= 80:
            mfi_signal = "overbought"
        elif mfi_val <= 20:
            mfi_signal = "oversold"
        elif mfi_val > 50:
            mfi_signal = "bullish"
        else:
            mfi_signal = "bearish"

    adx_val = float(row["adx"]) if not pd.isna(row.get("adx")) else None
    plus_di = float(row["plus_di"]) if not pd.isna(row.get("plus_di")) else None
    minus_di = float(row["minus_di"]) if not pd.isna(row.get("minus_di")) else None
    adx_signal = "no_trend"
    if adx_val is not None:
        if adx_val >= 25 and plus_di and minus_di:
            adx_signal = "strong_bullish" if plus_di > minus_di else "strong_bearish"
        elif adx_val >= 20 and plus_di and minus_di:
            adx_signal = "bullish" if plus_di > minus_di else "bearish"

    cloud_top = max(
        float(row["senkou_a"]) if not pd.isna(row.get("senkou_a")) else -np.inf,
        float(row["senkou_b"]) if not pd.isna(row.get("senkou_b")) else -np.inf,
    )
    cloud_bottom = min(
        float(row["senkou_a"]) if not pd.isna(row.get("senkou_a")) else np.inf,
        float(row["senkou_b"]) if not pd.isna(row.get("senkou_b")) else np.inf,
    )
    price = float(row["close"])
    ichimoku_signal = "neutral"
    if cloud_top > -np.inf and cloud_bottom < np.inf:
        if price > cloud_top:
            ichimoku_signal = "bullish"
        elif price < cloud_bottom:
            ichimoku_signal = "bearish"

    bb_squeeze = False
    if not pd.isna(row.get("bb_width")):
        width = float(row["bb_width"])
        avg_width = df["bb_width"].tail(50).mean()
        if not pd.isna(avg_width) and width < avg_width * 0.75:
            bb_squeeze = True

    vol_data = _volume_signal(row, prev)

    return {
        "price": round(price, decimals),
        "sma_20": _safe_round(row.get("sma_20"), decimals),
        "sma_50": _safe_round(row.get("sma_50"), decimals),
        "sma_200": _safe_round(row.get("sma_200"), decimals),
        "ema_20": _safe_round(row.get("ema_20"), decimals),
        "ema_50": _safe_round(row.get("ema_50"), decimals),
        "ema_200": _safe_round(row.get("ema_200"), decimals),
        "ema_cross": ema_cross,
        "rsi": round(rsi_val, 2) if rsi_val else None,
        "rsi_signal": rsi_signal,
        "macd": _safe_round(row.get("macd"), 6),
        "macd_signal_line": _safe_round(row.get("macd_signal"), 6),
        "macd_histogram": _safe_round(row.get("macd_hist"), 6),
        "macd_cross": macd_cross,
        "bb_upper": _safe_round(row.get("bb_upper"), decimals),
        "bb_lower": _safe_round(row.get("bb_lower"), decimals),
        "bb_squeeze": bb_squeeze,
        "atr": _safe_round(row.get("atr_14"), decimals),
        "stoch_k": _safe_round(row.get("stoch_k")),
        "stoch_d": _safe_round(row.get("stoch_d")),
        "cci": round(cci_val, 2) if cci_val else None,
        "cci_signal": cci_signal,
        "williams_r": round(wr, 2) if wr else None,
        "williams_signal": wr_signal,
        "adx": round(adx_val, 2) if adx_val else None,
        "adx_signal": adx_signal,
        "plus_di": round(plus_di, 2) if plus_di else None,
        "minus_di": round(minus_di, 2) if minus_di else None,
        "mfi": round(mfi_val, 2) if mfi_val else None,
        "mfi_signal": mfi_signal,
        "vwap": _safe_round(row.get("vwap"), decimals),
        "tenkan": _safe_round(row.get("tenkan"), decimals),
        "kijun": _safe_round(row.get("kijun"), decimals),
        "ichimoku_signal": ichimoku_signal,
        "trend": trend_from_emas(row),
        **vol_data,
    }


def _safe_round(val, decimals: int = 5):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    return round(float(val), decimals)


def indicators_to_series(df: pd.DataFrame, limit: int = 120) -> dict:
    subset = df.tail(limit)
    result = {
        "ema_20": [_safe_round(v) for v in subset["ema_20"]],
        "ema_50": [_safe_round(v) for v in subset["ema_50"]],
        "bb_upper": [_safe_round(v) for v in subset["bb_upper"]],
        "bb_lower": [_safe_round(v) for v in subset["bb_lower"]],
        "vwap": [_safe_round(v) for v in subset["vwap"]] if "vwap" in subset else [],
        "times": [t.isoformat() for t in subset.index],
    }
    if "volume" in subset.columns:
        result["volume"] = [round(float(v), 0) for v in subset["volume"]]
        result["volume_sma"] = [_safe_round(v, 0) for v in subset["volume_sma_20"]]
    return result