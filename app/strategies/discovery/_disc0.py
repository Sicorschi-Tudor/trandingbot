"""Shared helpers of discovery builder #0 (disc-H01, disc-H05, disc-H09, disc-H13, disc-H17). Not a plugin (the
loader only imports disc_*.py).

- Bar timestamps in the feed are UTC epoch seconds (bar open). Broker midnight (the D1 boundary) is 00:00
  Europe/Athens (EET with EU DST rules): checked on the data (e.g. D1 bars at 22:00 UTC from 2019-03-10 to
  2019-03-28 — US DST already on, EU not yet — and at 21:00 UTC from 2019-03-31), so the broker day of a bar
  starting at t is the Athens calendar date of t.
- Time exits that app.backtest cannot express (clock deadlines such as "close of the 15:00 UTC bar", "end of the next
  broker day", "Friday close", or a hold counted in bars over weekends): a plugin exposes
  time_exit(opened_t, bar_t, bar_sec, n_bars, cfg) -> 'open' | 'close' | None. time_exits(plugin, cfg, sec) arms it
  for one backtest run by wrapping labeler.SimPosition.step (research scripts only; nothing else changes):
  'open' closes at the bar open before the bar is processed (deadline passed during a gap), 'close' closes at the
  bar close after the normal SL/TP processing of the bar (reason 'TIMEX', stamped at the bar close).
- Cross-sectional universe (disc-H13): set_universe({symbol: D1 bars}) or cfg 'xs_db' (path of a bot.db copy, read
  only); a decision only uses bars whose open time is <= the decision bar's open time (closed bars only)."""
from __future__ import annotations

import contextlib
import sqlite3
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from ... import exits, indicators as ind, labeler

BROKER_TZ = ZoneInfo("Europe/Athens")
H = 3600
DAY = 86400
TIMEX = "TIMEX"
TIMEX_OPEN = "TIMEX_OPEN"


def utc(t: float) -> datetime:
    return datetime.fromtimestamp(t, timezone.utc)


def broker_date(t: float) -> date:
    return datetime.fromtimestamp(t, BROKER_TZ).date()


def broker_midnight(d: date) -> int:
    """UTC timestamp of 00:00 broker time on day d."""
    return int(datetime.combine(d, dtime(0), BROKER_TZ).timestamp())


def next_trading_day(d: date) -> date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def week_key(d: date) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return (y, w)


def week_end(d: date) -> int:
    """UTC timestamp of the broker Friday close (= Saturday 00:00 broker) of the week of d."""
    sat = d + timedelta(days=5 - d.weekday())
    return broker_midnight(sat)


def atr_last(bars: list[dict], n: int) -> float | None:
    if len(bars) < n + 1:
        return None
    return ind.last(ind.atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars], int(n)))


# ---- time exits (research backtests) ---------------------------------------------------------------------

@contextlib.contextmanager
def time_exits(plugin, cfg: dict, sec: int):
    """Arm plugin.time_exit for the positions simulated inside the `with` block (no-op without the hook)."""
    fn = getattr(plugin, "time_exit", None)
    if not callable(fn):
        yield
        return
    orig = labeler.SimPosition.step
    old_reasons = exits.AT_CLOSE_REASONS

    def step(self, bar, spread_px):
        n = getattr(self, "_dx0_n", 0)
        if n > 0 and fn(self.opened_t, bar["t"], sec, n, cfg) == "open" and bar["t"] >= self.opened_t:
            return TIMEX_OPEN, self.close_price(bar["o"], spread_px)
        hit = orig(self, bar, spread_px)
        n += 1
        self._dx0_n = n
        if hit is None and fn(self.opened_t, bar["t"], sec, n, cfg) is not None:
            return TIMEX, self.close_price(bar["c"], spread_px)
        return hit

    labeler.SimPosition.step = step
    if TIMEX not in exits.AT_CLOSE_REASONS:
        exits.AT_CLOSE_REASONS = tuple(exits.AT_CLOSE_REASONS) + (TIMEX,)
    try:
        yield
    finally:
        labeler.SimPosition.step = orig
        exits.AT_CLOSE_REASONS = old_reasons


def deadline_rule(deadline: int, bar_t: int, bar_sec: int) -> str | None:
    """'open' when the bar starts at/after the deadline (gap), 'close' when it ends at/after it, else None."""
    if bar_t >= deadline:
        return "open"
    if bar_t + bar_sec >= deadline:
        return "close"
    return None


# ---- cross-sectional universe ----------------------------------------------------------------------------

_UNIVERSE: dict[str, list[dict]] = {}
_DB_CACHE: dict[tuple, dict[str, list[dict]]] = {}


def set_universe(series: dict[str, list[dict]] | None) -> None:
    _UNIVERSE.clear()
    for k, v in (series or {}).items():
        _UNIVERSE[str(k).upper()] = sorted(v, key=lambda b: b["t"])


def universe(cfg: dict, symbols: list[str], tf: str = "D1") -> dict[str, list[dict]]:
    if _UNIVERSE:
        return _UNIVERSE
    path = cfg.get("xs_db")
    if not path:
        return {}
    key = (str(path), tuple(symbols), tf)
    if key not in _DB_CACHE:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            out = {}
            for s in symbols:
                rows = con.execute("SELECT t,o,h,l,c FROM bars_archive WHERE symbol=? AND tf=? ORDER BY t",
                                   (s, tf)).fetchall()
                out[s] = [{"t": int(t), "o": o, "h": h, "l": l, "c": c} for t, o, h, l, c in rows if o is not None]
        finally:
            con.close()
        _DB_CACHE[key] = out
    return _DB_CACHE[key]
