"""disc-rev-volgate: trend-v1 plus a VOLATILITY-REGIME gate on entries (review idea, research only).

Why: app.strategy.analyze computes a regime label (HIGH_VOL / TREND_UP / TREND_DOWN / RANGE) and the ATR14 series for
every analysis row, but neither has any effect on the decision. On gold the spread is roughly constant in USD, so a
LOW-ATR bar means a tight ATR-based stop and a high spread/SL ratio (cost ratio), while ATR14 > 2x its 100-bar median
(= the HIGH_VOL label) marks release spikes where the fixed-ATR stop sits inside the spike. This plugin runs trend-v1
unchanged (same cfg / risk / context, bit-identical candidate levels, same ATR window as analyze) and appends extra
MARKET blockers when
    ratio = ATR14 / median(ATR14 over the last 100 closed signal bars)
    ratio < min_atr_ratio   (0 disables)      -> "Volatilitate scăzută: ..."
    ratio > max_atr_ratio   (0 disables)      -> "Volatilitate ridicată: ..."
    a["regime"] in regime_gate (list, [] off) -> "Volatilitate/regim <REGIME> blocat: ..."
All blocker texts start with 'Volatilitate' so engine.shadow_stats groups them (blocker_type cuts at ':').
Defaults min 0.8 / max 2.0 / no regime gate; min_atr_ratio=0, max_atr_ratio=0, regime_gate=[] = pure trend-v1 (the
matching baseline: research/results/REVIEW/entry_gold150.json). Research only (discovery package): never offered to
the live engine / UI."""
from __future__ import annotations

from ... import indicators as ind
from ... import strategy
from .. import trend_v1

NAME = "disc-rev-volgate"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare (review): trend-v1 identic, plus un filtru de volatilitate — setupul este blocat când "
                  "ATR14 / mediana ATR14 (100 bare) este sub min_atr_ratio sau peste max_atr_ratio, sau când "
                  "regimul este în regime_gate.")
PARAMS = {**trend_v1.PARAMS, "min_atr_ratio": 0.8, "max_atr_ratio": 2.0, "regime_gate": []}


def _param(cfg: dict, key: str):
    v = cfg.get(key)
    return PARAMS[key] if v is None else v


def atr_ratio(sig_bars: list[dict]) -> tuple[float | None, float | None, float | None]:
    """(ATR14, median ATR14 over the last 100 bars, ratio) computed exactly like app.strategy.analyze (full window)."""
    closes = [b["c"] for b in sig_bars]
    atr_s = ind.atr([b["h"] for b in sig_bars], [b["l"] for b in sig_bars], closes, 14)
    atr_v = ind.last(atr_s)
    hist = [x for x in atr_s[-100:] if x is not None]
    med = ind.median(hist) or atr_v
    if atr_v is None or not med or med <= 0:
        return atr_v, med, None
    return atr_v, med, atr_v / med


def gate_blockers(a: dict, sig_bars: list[dict], cfg: dict) -> list[str]:
    out: list[str] = []
    lo = float(_param(cfg, "min_atr_ratio") or 0)
    hi = float(_param(cfg, "max_atr_ratio") or 0)
    gate = _param(cfg, "regime_gate") or []
    if isinstance(gate, str):
        gate = [g.strip() for g in gate.split(",") if g.strip()]
    if lo > 0 or hi > 0:
        atr_v, med, ratio = atr_ratio(sig_bars)
        if ratio is not None:
            if lo > 0 and ratio < lo:
                out.append(f"Volatilitate scăzută: ATR14 {atr_v:.2f} = {ratio:.2f}× mediana pe 100 bare ({med:.2f}), "
                           f"sub minimul {lo:g}× — SL strâns, cost relativ ridicat.")
            if hi > 0 and ratio > hi:
                out.append(f"Volatilitate ridicată: ATR14 {atr_v:.2f} = {ratio:.2f}× mediana pe 100 bare ({med:.2f}), "
                           f"peste maximul {hi:g}× — vârf de volatilitate (release spike).")
    regime = a.get("regime")
    if regime and regime in {str(g).upper() for g in gate}:
        out.append(f"Volatilitate/regim {regime} blocat: regimul este în lista regime_gate.")
    return out


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = strategy.analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
    cand = a.get("candidate")
    if not cand or not quote:
        return a
    extra = gate_blockers(a, sig_bars, cfg)
    if not extra:
        return a
    cand["blockers"].extend(extra)
    if a["direction"] != "HOLD":
        comps = list(a["reasons"])
        a["direction"] = "HOLD"
        a["entry"] = a["stop_loss"] = a["take_profit"] = a["rr"] = None
        a["reasons"] = [f"Setup {cand['side']} detectat, dar blocat de filtre:", *extra, *("(+) " + c for c in comps)]
    else:
        a["reasons"].extend(extra)
    return a
