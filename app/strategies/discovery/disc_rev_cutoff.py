"""disc-rev-cutoff: trend-v1 plus an ENTRY CUTOFF before the forced session-end close (review idea, research only).

Why: with close_at_session_end=true every position still open when the session window ends is closed at the next bar
open (app.backtest._simulate 'SESSION_END'; live: engine close at session end) whatever its state. A trade opened 15-45
minutes before that close can never travel to a 2.5xATR target (median gold M15 holding time ~45 min, TP 1-3 h), so
its expectancy is capped near zero minus the spread. This plugin runs trend-v1 unchanged (same cfg / risk / context,
bit-identical candidate levels) and appends ONE extra MARKET blocker when the decision time (ctx.now_ts, the bar
close) is inside the last `entry_cutoff_minutes` before `session_end_utc` (HH:MM, UTC — the research sessions are
UTC windows, see research/run_bt.py --session-json):
    0 <= session_end - now < entry_cutoff_minutes * 60   -> "Sfârșit de sesiune: ..."
entry_cutoff_minutes=0 disables the gate = pure trend-v1 (the matching baseline). The blocker text starts with
'Sfârșit de sesiune' so engine.shadow_stats would group it (blocker_type cuts at ':'). Research only (discovery
package): never offered to the live engine / UI. Live equivalent (not implemented here): the same test in
strategy.context_blockers using scheduler.session_status()['end'] when close_at_session_end is true."""
from __future__ import annotations

from ... import strategy
from .. import trend_v1

NAME = "disc-rev-cutoff"
VERSION = "0.1"
DESCRIPTION_RO = ("Cercetare (review): trend-v1 identic, plus blocarea intrărilor în ultimele entry_cutoff_minutes "
                  "înainte de session_end_utc (închiderea forțată la sfârșitul sesiunii).")
PARAMS = {**trend_v1.PARAMS, "entry_cutoff_minutes": 60, "session_end_utc": "17:00"}


def _param(cfg: dict, key: str):
    v = cfg.get(key)
    return PARAMS[key] if v is None else v


def _hm_seconds(s: str) -> int:
    h, m = str(s).strip().split(":")[:2]
    return int(h) * 3600 + int(m) * 60


def cutoff_blocker(now_ts: float | None, cfg: dict) -> str | None:
    cutoff = int(float(_param(cfg, "entry_cutoff_minutes") or 0)) * 60
    if cutoff <= 0 or now_ts is None:
        return None
    end_s = _hm_seconds(_param(cfg, "session_end_utc"))
    sod = int(now_ts) % 86400
    remaining = end_s - sod
    if 0 <= remaining < cutoff:
        return (f"Sfârșit de sesiune: mai sunt {remaining // 60} min până la închiderea forțată "
                f"({_param(cfg, 'session_end_utc')} UTC), sub pragul de {cutoff // 60} min — TP-ul nu are timp să fie atins.")
    return None


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    a = strategy.analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx)
    cand = a.get("candidate")
    if not cand or not quote:
        return a
    extra = cutoff_blocker(ctx.now_ts, cfg)
    if not extra:
        return a
    cand["blockers"].append(extra)
    if a["direction"] != "HOLD":
        comps = list(a["reasons"])
        a["direction"] = "HOLD"
        a["entry"] = a["stop_loss"] = a["take_profit"] = a["rr"] = None
        a["reasons"] = [f"Setup {cand['side']} detectat, dar blocat de filtre:", extra, *("(+) " + c for c in comps)]
    else:
        a["reasons"].append(extra)
    return a
