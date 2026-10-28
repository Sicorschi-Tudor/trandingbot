"""Shared helpers of discovery builder #3 (disc-h04, disc-h08, disc-h12, disc-h16, disc-h20). Not a plugin (the
loader only imports disc_*.py).

- Broker-day keys: broker midnight (the D1 boundary of the MetaQuotes-Demo feed) is 00:00 Europe/Athens (checked on
  the data: every D1 bar starts at 00:00 Athens time), so a bar belongs to the broker day date(Athens time of its start).
- Research data hooks (backtest/screening only; live passes nothing and the plugins fall back to the bars they get):
  set_history({symbol: bars}) lets a plugin extend its closed-bar window BACKWARDS (only bars older than the first bar
  it was given, so no lookahead is possible); set_universe({symbol: D1 bars}) gives the cross-sectional plugin the
  other pairs (it keeps only bars closed at the decision time).
- exit_deadline hooks: a plugin may expose exit_deadline(opened_t, cfg) -> UTC timestamp (or None). clock_exit()
  arms it for one backtest run: the position closes at the close of the first bar that ends at or after the deadline
  (reason 'CLOCK', bar-close price like the time stop). app.backtest has no clock-time exit, so the research script
  applies it by wrapping labeler.SimPosition.step for the duration of the run (nothing else changes)."""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ... import exits, indicators as ind, labeler

BROKER_TZ = ZoneInfo("Europe/Athens")
H = 3600
DAY = 86400

_HISTORY: dict[str, list[dict]] = {}
_UNIVERSE: dict[str, list[dict]] = {}


def h1_day(t: float):
    """Broker day of an H1 bar starting at t."""
    return datetime.fromtimestamp(t, BROKER_TZ).date()


def d1_day(t: float):
    """Broker day of a D1 bar starting at t (broker midnight; +1 h margin against a shifted boundary)."""
    return (datetime.fromtimestamp(t, BROKER_TZ) + timedelta(hours=1)).date()


def utc(t: float) -> datetime:
    return datetime.fromtimestamp(t, timezone.utc)


def atr_last(bars: list[dict], n: int) -> float | None:
    if len(bars) < n + 1:
        return None
    s = ind.atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars], int(n))
    return ind.last(s)


# ---- research data hooks ----------------------------------------------------------------------------------

def set_history(series: dict[str, list[dict]] | None) -> None:
    _HISTORY.clear()
    for k, v in (series or {}).items():
        _HISTORY[str(k).upper()] = sorted(v, key=lambda b: b["t"])


def set_universe(series: dict[str, list[dict]] | None) -> None:
    _UNIVERSE.clear()
    for k, v in (series or {}).items():
        _UNIVERSE[str(k).upper()] = sorted(v, key=lambda b: b["t"])


def universe() -> dict[str, list[dict]]:
    return _UNIVERSE


def extend_back(symbol: str, bars: list[dict], need: int) -> list[dict]:
    """bars preceded by up to `need` older bars of the history hook (strictly older than bars[0])."""
    hist = _HISTORY.get(symbol.upper())
    if not hist or not bars:
        return bars
    t0 = bars[0]["t"]
    lo, hi = 0, len(hist)
    while lo < hi:  # first index with t >= t0
        mid = (lo + hi) // 2
        if hist[mid]["t"] < t0:
            lo = mid + 1
        else:
            hi = mid
    return hist[max(0, lo - need):lo] + list(bars)


# ---- clock-time exit (research runs) ----------------------------------------------------------------------

@contextlib.contextmanager
def clock_exit(deadline_fn, bar_sec: int):
    """Within the block every SimPosition closes ('CLOCK', at the bar close) at the first bar ending at or after
    deadline_fn(opened_t) (None = no deadline). SL/TP inside that bar keep priority (they are checked first)."""
    if deadline_fn is None:
        yield
        return
    orig = labeler.SimPosition.step
    orig_reasons = exits.AT_CLOSE_REASONS

    def step(self, bar, spread_px):
        hit = orig(self, bar, spread_px)
        if hit is not None:
            return hit
        dl = self.extra.get("_deadline", 0)
        if dl == 0:
            dl = self.extra["_deadline"] = deadline_fn(self.opened_t)
        if dl is not None and bar["t"] + bar_sec >= dl:
            return "CLOCK", self.close_price(bar["c"], spread_px)
        return None

    labeler.SimPosition.step = step
    exits.AT_CLOSE_REASONS = tuple(orig_reasons) + ("CLOCK",)
    try:
        yield
    finally:
        labeler.SimPosition.step = orig
        exits.AT_CLOSE_REASONS = orig_reasons


RUN_DEFAULTS_H1 = {"signal_timeframe": "H1", "htf_timeframe": "NONE", "max_hold_minutes": 0, "break_even_at_r": 0,
                   "signal_expiry_minutes": 60, "close_at_session_end": False, "session": None,
                   "swap_mode": "table"}
