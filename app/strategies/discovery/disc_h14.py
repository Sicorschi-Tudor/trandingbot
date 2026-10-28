"""disc-h14: USD-factor residual reversion on H4 (Stage D discovery, hypothesis H14).

Rationale: the six USD pairs share a common dollar factor; one pair's idiosyncratic deviation from it (lagging or
leading the USD move) is mostly noise/liquidity and closes as the pair re-aligns with the factor.

Rules (closed bars only):
- USD-based log return of a pair per bar: u = +ln(c_t/c_{t-1}) for USDXXX (USDJPY, USDCAD), -ln(...) for XXXUSD
  (EURUSD, GBPUSD, AUDUSD, NZDUSD). Factor r_U = mean of u over the 6 pairs (bars common to all six).
- beta = OLS slope of u_pair on r_U over the last win=120 bars; residual e = u_pair - beta*r_U on that window;
  S = sum of e over the last N bars; z = S / (stdev(e over 120) × sqrt(N)).
- z >= z0: the pair's USD side is too strong vs the factor -> SELL USD in that pair (SELL USDXXX / BUY XXXUSD);
  z <= -z0: BUY USD. Enter at the next open. SL = sl_atr(2)×ATR14, TP = tp_atr(1.5)×ATR14.
Exit: default_exit() = signal_flip with flip_on_flat (bias() = trade direction while |z| >= z_exit 0.3, else 0):
closes when |z| < 0.3 (or the sign flips), plus time stop hold_hours = 48 h (12 H4 bars of wall-clock time).

Cross-symbol data: the plugin is called per symbol, so the six USD-pair closes come from a read-only SQLite file
(cfg 'factor_db': bars_archive ∪ bars, never written) or from set_factor_bars() (tests). Only bars with
t <= the last closed bar of the decision are used (and t < cfg 'factor_max_ts' when given), so no lookahead.
Non-USD symbols always HOLD."""
from __future__ import annotations

import bisect
import math
import sqlite3

from .. import _common as C
from .. import _htf as X

NAME = "disc-h14"
VERSION = "1.0"
DESCRIPTION_RO = ("Revenirea reziduului față de factorul USD (H4): dacă o pereche cu USD s-a abătut statistic de la "
                  "mișcarea comună a dolarului (z ≥ prag), intră pentru re-aliniere; SL 2×ATR, TP 1,5×ATR, ieșire când "
                  "|z| < 0,3 sau după 12 bare.")
PARAMS = {"N": 6, "z0": 2.0, "z_exit": 0.3, "win": 120, "atr_n": 14, "sl_atr": 2.0, "tp_atr": 1.5, "hold_hours": 48,
          "factor_db": None, "factor_max_ts": None}
RUN_DEFAULTS = {**X.RUN_DEFAULTS, "signal_timeframe": "H4", "signal_expiry_minutes": 4 * 1440}
USD_PAIRS = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1, "USDJPY": 1, "USDCAD": 1}

_FACTOR: dict = {}  # key -> {"times": [...], "u": {sym: [...]}, "ru": [...]} (returns at times[1:], aligned)


def default_exit(cfg: dict) -> dict:
    return {"mode": "signal_flip", "params": {"flip_on_flat": 1, "max_hold_hours": float(C.p(cfg, PARAMS, "hold_hours"))}}


def _build(closes: dict[str, dict[int, float]]) -> dict:
    common = sorted(set.intersection(*(set(d) for d in closes.values())))
    u = {s: [] for s in USD_PAIRS}
    for k in range(1, len(common)):
        t0, t1 = common[k - 1], common[k]
        for s, sg in USD_PAIRS.items():
            a, b = closes[s][t0], closes[s][t1]
            u[s].append(sg * math.log(b / a) if a > 0 and b > 0 else 0.0)
    ru = [sum(u[s][j] for s in USD_PAIRS) / len(USD_PAIRS) for j in range(len(common) - 1)]
    return {"times": common[1:], "u": u, "ru": ru}


def set_factor_bars(tf: str, bars: dict[str, list[dict]], key: str = "mem") -> None:
    """Inject the six USD pairs' bars (tests / callers without a DB): {symbol: [{t, c, ...}]}."""
    _FACTOR[(key, tf.upper(), None)] = _build({s: {int(b["t"]): float(b["c"]) for b in bars[s]} for s in USD_PAIRS})


def _load_db(path: str, tf: str, max_ts) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        closes: dict[str, dict[int, float]] = {}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for s in USD_PAIRS:
            d: dict[int, float] = {}
            for table in ("bars_archive", "bars"):  # archive first (final values), like backtest.load_bars
                if table not in tables:
                    continue
                q = f"SELECT t, c FROM {table} WHERE symbol=? AND tf=?" + (" AND t<?" if max_ts else "")
                args = (s, tf) + ((int(max_ts),) if max_ts else ())
                for t, c in conn.execute(q, args):
                    if c is not None and int(t) not in d:
                        d[int(t)] = float(c)
            closes[s] = d
    finally:
        conn.close()
    return _build(closes)


def factor(cfg: dict, tf: str):
    path = C.p(cfg, PARAMS, "factor_db")
    max_ts = C.p(cfg, PARAMS, "factor_max_ts")
    if path:
        key = (str(path), tf, max_ts)
        if key not in _FACTOR:
            _FACTOR[key] = _load_db(str(path), tf, max_ts)
        return _FACTOR[key]
    return _FACTOR.get(("mem", tf, None))


def zscore(symbol: str, t_last: int, cfg: dict, tf: str):
    """z of the pair's cumulated residual over N bars at the bar starting t_last (None when not computable)."""
    if symbol not in USD_PAIRS:
        return None
    f = factor(cfg, tf)
    if not f:
        return None
    win, N = int(C.p(cfg, PARAMS, "win")), int(C.p(cfg, PARAMS, "N"))
    times = f["times"]
    k = bisect.bisect_right(times, int(t_last)) - 1
    if k < 0 or times[k] != int(t_last) or k + 1 < win or N > win:
        return None
    u = f["u"][symbol][k - win + 1:k + 1]
    r = f["ru"][k - win + 1:k + 1]
    mu, mr = sum(u) / win, sum(r) / win
    var_r = sum((x - mr) ** 2 for x in r)
    beta = sum((x - mu) * (y - mr) for x, y in zip(u, r)) / var_r if var_r > 0 else 0.0
    e = [x - beta * y for x, y in zip(u, r)]
    me = sum(e) / win
    sd = math.sqrt(sum((x - me) ** 2 for x in e) / (win - 1))
    if sd <= 0:
        return None
    return sum(e[-N:]) / (sd * math.sqrt(N))


def side_for(symbol: str, z: float, thr: float) -> int:
    """+1 BUY / -1 SELL of the pair for a residual z beyond thr (USD too strong -> sell USD), else 0."""
    if z is None or abs(z) < thr:
        return 0
    usd = -1 if z > 0 else 1  # direction for USD
    return usd * USD_PAIRS[symbol]


def _tf(cfg: dict) -> str:
    return str(cfg.get("signal_timeframe") or "H4").upper()


def bias(symbol, sig_bars, htf_bars, cfg) -> int:
    if not sig_bars:
        return 0
    z = zscore(symbol, sig_bars[-1]["t"], cfg, _tf(cfg))
    return side_for(symbol, z, float(C.p(cfg, PARAMS, "z_exit"))) if z is not None else 0


def analyze(symbol, sig_bars, htf_bars, quote, cfg, risk, ctx) -> dict:
    g = lambda k: C.p(cfg, PARAMS, k)  # noqa: E731
    a = C.skeleton(symbol, sig_bars, quote, cfg, ctx)
    if symbol not in USD_PAIRS:
        return C.hold(a, f"{symbol} nu este o pereche cu USD — strategia nu se aplică.")
    a, bars = X.start(symbol, sig_bars, htf_bars, quote, cfg, ctx, int(g("atr_n")) + 2)
    if bars is None:
        return a
    digits = int(quote.get("digits") or 5)
    atr_s = X.atr_series(bars, int(g("atr_n")))
    atr_v = atr_s[-1]
    z = zscore(symbol, bars[-1]["t"], cfg, _tf(cfg))
    a["indicators"] = X.indicators(digits, atr_v, "FLAT", usd_resid_z=C.r_(z, 3))
    if z is None or not atr_v:
        return C.hold(a, "Factor USD indisponibil pentru bara curentă (date comune lipsă) sau ATR indisponibil.")
    sgn = side_for(symbol, z, float(g("z0")))
    a["regime"] = X.regime(atr_s, sgn)
    if sgn == 0:
        return C.hold(a, f"Reziduu față de factorul USD z = {z:+.2f} (prag ±{float(g('z0')):g}).")
    side = "BUY" if sgn > 0 else "SELL"
    comps = [f"Reziduu cumulat pe {int(g('N'))} bare față de factorul USD: z = {z:+.2f}",
             f"USD {'prea puternic' if z > 0 else 'prea slab'} în {symbol} față de factor → re-aliniere"]
    return X.finish_atr(a, symbol, side, quote, cfg, risk, ctx, comps, atr_v, g("sl_atr"), g("tp_atr"),
                        note="ieșire când |z| < 0,3 sau după 12 bare")
