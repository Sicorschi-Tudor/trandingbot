"""Shared plumbing of pattern builder #2 plugins (app.patterns.p2). Not a plugin itself (the loader imports only
disc_*.py / pat_*.py). Research only: never offered to the live engine, the UI or the API.

Two plugin kinds per pattern id:
- standalone 'pat-<ID>' (file pat_<ID>.py): trades the pattern's own event on the closed signal bars (tail of
  TAIL bars); entry at the next open (backtest), SL/TP from the pattern definition, same SL floor / market filters /
  context blockers as every plugin (_common.finish).
- filter 'filt-<ID>' (file pat_<ID>_filt.py): the live trend-v1 (app.strategy.analyze, bit-identical) whose
  candidate is blocked (an extra blocker; direction HOLD) when the pattern filter disagrees. No RUN_DEFAULTS and no
  default_exit, so the backtest applies exactly the live exits (incl. RESCUE) as for trend-v1.
Pattern parameters come from cfg keys prefixed 'pat_' (pat_K, pat_n, ...), so they never collide with live keys.

Tick volume for the VWAP: bars may carry 'tv'; app.backtest.load_bars does not load it, so research scripts can
register it with set_volume({symbol: {t: tv}}) (in-process runs only); otherwise the VWAP weights are 1."""
from __future__ import annotations

from ... import strategy
from ...patterns import p2
from .. import _common as C
from .. import _htf as X

TAIL = 400
_VOLUME: dict[str, dict[int, float]] = {}


def set_volume(vol: dict[str, dict[int, float]] | None) -> None:
    _VOLUME.clear()
    for k, v in (vol or {}).items():
        _VOLUME[str(k).upper()] = dict(v)


def with_volume(symbol: str, bars: list[dict]) -> list[dict]:
    vol = _VOLUME.get(str(symbol).upper())
    if not vol or (bars and bars[-1].get("tv") is not None):
        return bars
    return [{**b, "tv": vol.get(b["t"], 0.0)} for b in bars]


def pattern_params(cfg: dict, defaults: dict) -> dict:
    out = {k[4:]: v for k, v in defaults.items() if k.startswith("pat_")}
    out.update({k[4:]: v for k, v in (cfg or {}).items() if str(k).startswith("pat_") and v is not None})
    return out


# ---- standalone ---------------------------------------------------------------------------------------------

def _levels(pid: str, ev: dict, side: str, entry: float, pp: dict):
    """(sl price, tp price or None, tp_r or None, blocker or None) of a pattern event."""
    d = 1 if side == "BUY" else -1
    if pid == "C3_INSIDEBAR":
        sl = (ev["ml"] if d > 0 else ev["mh"]) if str(pp.get("sl_mode", "extreme")) == "extreme" \
            else (ev["ml"] + ev["mh"]) / 2
        return sl, None, float(pp.get("R", 2.0)), None
    if pid == "P2_HS":
        risk = d * (entry - ev["sl"])
        tp = ev["target"]
        if risk > 0 and d * (tp - entry) > 4 * risk:
            return ev["sl"], None, 4.0, None
        return ev["sl"], tp, None, None
    if pid == "L1_SR":
        risk = d * (entry - ev["sl"])
        nxt = ev.get("next")
        if nxt is None or risk <= 0 or d * (nxt - entry) < risk:
            return ev["sl"], None, 2.0, None
        return ev["sl"], nxt, None, None
    if pid == "L4_FIB618":
        return ev["sl"], ev["tp"], None, None
    if pid == "S3_OB":
        if str(pp.get("tp_mode", "2R")) == "impulse":
            return ev["sl"], ev["ext"], None, None
        return ev["sl"], None, 2.0, None
    if pid == "I3_SUPERTREND":
        return ev["line"], None, float(pp.get("tp_r", 10.0)), None
    if pid == "I6_VWAP":
        adx = ev.get("adx")
        if adx is None or adx >= float(pp.get("adx_max", 25)):
            return None, None, None, f"ADX {adx if adx is None else round(adx, 1)} ≥ {pp.get('adx_max', 25)} (trend)"
        a = ev["atr"] or 0.0
        if d > 0:
            sl = min(ev["s_lo"], entry - a)
            sl = max(sl, entry - 2 * a)
        else:
            sl = max(ev["s_hi"], entry + a)
            sl = min(sl, entry + 2 * a)
        return sl, ev["vwap"], None, None
    raise KeyError(pid)


def standalone_analyze(pid: str, defaults: dict, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, 100)
    if bars is None:
        return a
    bars = with_volume(symbol, bars[-TAIL:])
    pp = pattern_params(cfg, defaults)
    digits = int(quote.get("digits") or 5)
    atr_v = X.atr_series(bars, 14)[-1]
    a["indicators"] = X.indicators(digits, atr_v, X.htf_trend(htf_bars))
    ev = p2.event_at(pid, bars, len(bars) - 1, pp)
    if not ev or not ev.get("dir"):
        return C.hold(a, f"Niciun eveniment {pid} pe ultima bară închisă.")
    side = "BUY" if ev["dir"] > 0 else "SELL"
    entry = float(quote["ask"]) if side == "BUY" else float(quote["bid"])
    sl, tp, tp_r, block = _levels(pid, ev, side, entry, pp)
    if block:
        return C.hold(a, f"Eveniment {pid} {side} ignorat: {block}.")
    if (side == "BUY" and sl >= entry) or (side == "SELL" and sl <= entry):
        return C.hold(a, f"Eveniment {pid} {side}: prețul a depășit deja SL-ul structural.")
    a["regime"] = X.regime(X.atr_series(bars, 14), ev["dir"])
    comps = [f"Pattern {pid} {side}"]
    return C.finish(a, symbol, side, sl, tp, quote, cfg, risk, ctx, comps, 1, tp_r=tp_r)


# ---- filter over trend-v1 -----------------------------------------------------------------------------------

def filter_analyze(pid: str, defaults: dict, symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = strategy.analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
    cand = a.get("candidate")
    if not cand:
        return a
    bars = with_volume(symbol, list(sig_bars[-TAIL:]))
    pp = pattern_params(cfg, defaults)
    prepare, verdict = p2.FILTERS[pid]
    keep, why = verdict(prepare(bars, pp), len(bars) - 1, cand["side"], float(cand["entry"]),
                        float(cand["stop_loss"]), pp)
    if keep:
        return a
    blocker = f"Filtru {pid}: {why}."
    cand["blockers"].append(blocker)
    if a["direction"] != "HOLD":
        a["direction"] = "HOLD"
        a["entry"] = a["stop_loss"] = a["take_profit"] = a["rr"] = None
        a["reasons"].insert(0, f"Setup {cand['side']} detectat, dar blocat de filtre:")
        a["reasons"].insert(1, blocker)
    else:
        a["reasons"].append(blocker)
    return a
