"""Feature schemas for the ML layer — ONE code path for backtest-time datasets and live scoring.

fs-v1 (entry): entry_features(sig_bars, htf_bars, quote, candidate) for a strategy candidate at a decision time.
  Uses ONLY the closed bars it is given (the last FS_WINDOW signal bars, the last FS_HTF_WINDOW HTF bars), the quote
  (spread) and the candidate levels. The value never depends on bars after sig_bars[-1] (they are not an input) and,
  given at least FS_WINDOW bars, not on bars older than the window either — so the live engine (last 400 closed bars)
  and the dataset builder (backtest windows of 400 bars) compute bit-identical features.
xs-v1 (exit/state): exit_features(sig_bars, pos) for an open position at the close of the last closed signal bar.

Every function returns a dict {name: float} with exactly the keys of the schema's FEATURE_NAMES (NaN when a value is
undefined: too little history, ATR 0, ...). Symbol is not a feature here: models encode it from TRAIN data only.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .. import indicators as ind

ENTRY_SCHEMA = "fs-v1"
EXIT_SCHEMA = "xs-v1"
FS_WINDOW = 300  # signal-TF bars used by fs-v1 (EMA50/MACD seeds converge well inside 300 bars)
FS_HTF_WINDOW = 200
XS_WINDOW = 120  # signal-TF bars used by xs-v1 (EMA20/RSI14/ATR14 only)
RET_LAGS = (1, 3, 6, 12, 24)
NAN = float("nan")

ENTRY_FEATURE_NAMES = [
    *(f"ret_{k}_atr" for k in RET_LAGS),
    "ema20_dist_atr", "ema50_dist_atr", "ema20_slope_atr", "ema50_slope_atr",
    "rsi14", "macd_hist_atr", "atr_rel", "atr_pct100", "bb_width_atr",
    "htf_slope_atr", "htf_dist_atr",
    "hour_sin", "hour_cos", "weekday",
    "spread_atr", "side", "score", "sl_dist_atr", "tp_rr",
    # side-oriented copies (+ = in favour of the candidate): trees split on them directly
    "ret_12_dir", "ema20_dist_dir", "macd_dir", "htf_slope_dir",
]
EXIT_FEATURE_NAMES = [
    "r_now", "mfe_r", "mae_r", "minutes_in_trade", "dr_3", "dr_6", "dist_sl_r", "dist_tp_r",
    "atr_ratio", "ema20_dist_dir", "rsi14_dir", "hour_sin", "hour_cos", "side",
]
FEATURE_NAMES = {ENTRY_SCHEMA: ENTRY_FEATURE_NAMES, EXIT_SCHEMA: EXIT_FEATURE_NAMES}


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return NAN
    return v if math.isfinite(v) else NAN


def _div(a, b) -> float:
    a, b = _f(a), _f(b)
    if math.isnan(a) or math.isnan(b) or b == 0:
        return NAN
    return a / b


def bar_seconds(bars: list[dict]) -> int:
    """Timeframe of a bar series = smallest spacing among the last bars (weekend gaps are larger)."""
    ts = [int(b["t"]) for b in bars[-20:]]
    d = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    return min(d) if d else 60


def _hour_feats(ts: float) -> tuple[float, float, float]:
    dt = datetime.fromtimestamp(ts, timezone.utc)
    h = dt.hour + dt.minute / 60.0
    return math.sin(2 * math.pi * h / 24), math.cos(2 * math.pi * h / 24), float(dt.weekday())


def _side_sign(side) -> int:
    return 1 if str(side).upper() == "BUY" else -1


def entry_features(sig_bars: list[dict], htf_bars: list[dict], quote: dict | None, candidate: dict,
                   bar_sec: int | None = None) -> dict[str, float]:
    """fs-v1. sig_bars/htf_bars: CLOSED bars (t,o,h,l,c), oldest first. quote: {bid, ask, point, spread_points}.
    candidate: {side, entry, stop_loss|sl, take_profit|tp, score}. Decision time = close of sig_bars[-1]."""
    out = {k: NAN for k in ENTRY_FEATURE_NAMES}
    bars = sig_bars[-FS_WINDOW:]
    if not bars:
        return out
    sec = int(bar_sec or bar_seconds(bars))
    closes = [float(b["c"]) for b in bars]
    highs = [float(b["h"]) for b in bars]
    lows = [float(b["l"]) for b in bars]
    c = closes[-1]
    atr_s = ind.atr(highs, lows, closes, 14)
    atr = ind.last(atr_s)
    e20, e50 = ind.ema(closes, 20), ind.ema(closes, 50)
    _, _, hist = ind.macd(closes, 12, 26, 9)
    rsi = ind.last(ind.rsi(closes, 14))
    sg = _side_sign(candidate.get("side"))
    for k in RET_LAGS:
        if len(closes) > k:
            out[f"ret_{k}_atr"] = _div(c - closes[-1 - k], atr)
    out["ema20_dist_atr"] = _div(c - (ind.last(e20) if ind.last(e20) is not None else NAN), atr)
    out["ema50_dist_atr"] = _div(c - (ind.last(e50) if ind.last(e50) is not None else NAN), atr)
    for name, s in (("ema20_slope_atr", e20), ("ema50_slope_atr", e50)):
        a, b = ind.last(s), ind.last(s, 3)
        out[name] = _div(None if a is None or b is None else a - b, atr)
    out["rsi14"] = _f(rsi)
    out["macd_hist_atr"] = _div(ind.last(hist), atr)
    out["atr_rel"] = _div(atr, c)
    hist_atr = [x for x in atr_s[-100:] if x is not None]
    if atr is not None and len(hist_atr) >= 20:
        out["atr_pct100"] = sum(1 for x in hist_atr if x <= atr) / len(hist_atr)
    if len(closes) >= 20:
        w = closes[-20:]
        m = sum(w) / 20
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / 20)
        out["bb_width_atr"] = _div(4 * sd, atr)  # (upper - lower) of Bollinger(20, 2) in ATR
    hb = htf_bars[-FS_HTF_WINDOW:] if htf_bars else []
    if len(hb) >= 55:
        hc = [float(b["c"]) for b in hb]
        h_e50 = ind.ema(hc, 50)
        h_atr = ind.last(ind.atr([float(b["h"]) for b in hb], [float(b["l"]) for b in hb], hc, 14))
        a, b = ind.last(h_e50), ind.last(h_e50, 3)
        out["htf_slope_atr"] = _div(None if a is None or b is None else a - b, h_atr)
        out["htf_dist_atr"] = _div(None if a is None else hc[-1] - a, h_atr)
    T = int(bars[-1]["t"]) + sec
    out["hour_sin"], out["hour_cos"], out["weekday"] = _hour_feats(T)
    q = quote or {}
    point = _f(q.get("point"))
    sp_pts = _f(q.get("spread_points"))
    if math.isnan(sp_pts) and q.get("bid") and q.get("ask") and point > 0:
        sp_pts = (float(q["ask"]) - float(q["bid"])) / point
    out["spread_atr"] = _div(sp_pts * point if not math.isnan(point) else NAN, atr)
    out["side"] = float(sg)
    out["score"] = _f(candidate.get("score"))
    entry = _f(candidate.get("entry"))
    sl = _f(candidate.get("stop_loss", candidate.get("sl")))
    tp = _f(candidate.get("take_profit", candidate.get("tp")))
    out["sl_dist_atr"] = _div(abs(entry - sl), atr)
    out["tp_rr"] = _div(abs(tp - entry), abs(entry - sl))
    out["ret_12_dir"] = out["ret_12_atr"] * sg
    out["ema20_dist_dir"] = out["ema20_dist_atr"] * sg
    out["macd_dir"] = out["macd_hist_atr"] * sg
    out["htf_slope_dir"] = out["htf_slope_atr"] * sg
    return {k: _f(out[k]) for k in ENTRY_FEATURE_NAMES}


def exit_features(sig_bars: list[dict], pos: dict, bar_sec: int | None = None) -> dict[str, float]:
    """xs-v1 at the close of sig_bars[-1] (closed signal-TF bars, oldest first) for an open position.
    pos: side, entry (fill price), sl0 (initial SL: defines 1R), sl (current SL), tp, opened_ts, mfe_r, mae_r
    (≥ 0, relative to the initial risk), optional price_now (closing price: bid for BUY / ask for SELL; default the
    last close + spread for SELL), spread_points + point (spread of the closing side), now_ts (default bar close)."""
    out = {k: NAN for k in EXIT_FEATURE_NAMES}
    bars = sig_bars[-XS_WINDOW:]
    sg = _side_sign(pos.get("side"))
    out["side"] = float(sg)
    entry, sl0 = _f(pos.get("entry")), _f(pos.get("sl0"))
    r_dist = (entry - sl0) * sg
    if not bars or not (r_dist > 0):
        return out
    sec = int(bar_sec or bar_seconds(bars))
    point = _f(pos.get("point"))
    sp_px = _f(pos.get("spread_points")) * point if not math.isnan(point) else NAN
    sp_px = 0.0 if math.isnan(sp_px) else sp_px

    def close_px(c: float) -> float:
        return c if sg > 0 else c + sp_px

    def r_of(px: float) -> float:
        return (px - entry) * sg / r_dist

    closes = [float(b["c"]) for b in bars]
    highs = [float(b["h"]) for b in bars]
    lows = [float(b["l"]) for b in bars]
    T = float(pos.get("now_ts") or (int(bars[-1]["t"]) + sec))
    px_now = _f(pos.get("price_now"))
    r_now = r_of(close_px(closes[-1]) if math.isnan(px_now) else px_now)
    out["r_now"] = r_now
    out["mfe_r"] = max(_f(pos.get("mfe_r")), r_now) if not math.isnan(_f(pos.get("mfe_r"))) else max(r_now, 0.0)
    out["mae_r"] = max(_f(pos.get("mae_r")), -r_now) if not math.isnan(_f(pos.get("mae_r"))) else max(-r_now, 0.0)
    opened = _f(pos.get("opened_ts"))
    out["minutes_in_trade"] = (T - opened) / 60.0 if not math.isnan(opened) else NAN
    for k in (3, 6):
        if len(bars) > k:
            b = bars[-1 - k]
            past = r_of(close_px(float(b["c"]))) if not math.isnan(opened) and b["t"] + sec > opened else 0.0
            out[f"dr_{k}"] = r_now - past
    sl = _f(pos.get("sl"))
    sl = sl0 if math.isnan(sl) else sl
    tp = _f(pos.get("tp"))
    out["dist_sl_r"] = r_now - r_of(sl)
    out["dist_tp_r"] = r_of(tp) - r_now if not math.isnan(tp) else NAN
    atr_s = ind.atr(highs, lows, closes, 14)
    atr = ind.last(atr_s)
    atr_entry = None
    if not math.isnan(opened):
        for j in range(len(bars) - 1, -1, -1):  # ATR of the last bar closed at/before the entry
            if bars[j]["t"] + sec <= opened:
                atr_entry = atr_s[j]
                break
    out["atr_ratio"] = _div(atr, atr_entry) if atr_entry else (1.0 if atr else NAN)
    e20 = ind.last(ind.ema(closes, 20))
    out["ema20_dist_dir"] = _div(None if e20 is None else (closes[-1] - e20) * sg, atr)
    rsi = ind.last(ind.rsi(closes, 14))
    out["rsi14_dir"] = NAN if rsi is None else (rsi - 50.0) * sg
    out["hour_sin"], out["hour_cos"], _ = _hour_feats(T)
    return {k: _f(out[k]) for k in EXIT_FEATURE_NAMES}


def vector(features: dict, names: list[str]) -> list[float]:
    """Ordered feature vector (missing keys -> NaN)."""
    return [_f(features.get(k)) for k in names]


def features_hash(features: dict, names: list[str] | None = None) -> str:
    """Stable short hash of a feature dict (ml_decisions.features_hash)."""
    import hashlib
    import json
    names = names or sorted(features)
    vals = [None if math.isnan(v) else round(v, 8) for v in vector(features, names)]
    return hashlib.sha1(json.dumps([names, vals]).encode()).hexdigest()[:16]
