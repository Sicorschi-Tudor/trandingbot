"""SMC setup check (Smart Money Concepts), the five-step entry chain described by CLUB 81 (club81.up.railway.app):

1. higher-timeframe trend (30)   2. liquidity sweep against it (20)   3. structure shift (BOS) after the sweep (20)
4. return into the order block left by the shift (20)   5. room to the nearest opposing liquidity >= 1.5 ATR (10)

A step scores its full weight when met, half when partial. With the higher timeframes against the trade the score is
capped at 50 (max 95). Standard mode: ENTER at >= 50 with the trend not against and a sweep or a shift present; WAIT at
>= 35. Levels: entry at market inside the zone, else a limit at its near edge (<= 2 ATR away); SL beyond the farther of
the zone's far edge and the sweep extreme (if <= 3 ATR) + 0.1 ATR, else 1.5 ATR, never < 0.4 ATR; TP1 at 2R.
Market blocks: ATR >= 2.2x its 100-bar mean (news spike), < 0.45x (dead), rollover after 21:00 UTC on M1/M5.
Aggressive mode: ENTER at >= 45 with the trend not against (sweep / shift not required), WAIT at >= 25; the market
conditions become warnings (only missing data stops the check).
Hold: 2-3 bars of the signal timeframe. Closed bars only (keys t,o,h,l,c). The score is a measure of how complete the
picture is, not a probability of profit."""
from __future__ import annotations

from datetime import datetime, timezone

from . import indicators as ind

WEIGHTS = {"trend": 30, "sweep": 20, "shift": 20, "zone": 20, "room": 10}
NAMES = {"trend": "Trend timeframe-uri mari", "sweep": "Lichiditate ridicată", "shift": "Ruperea structurii (BOS)",
         "zone": "Revenire în zonă (order block)", "room": "Spațiu până la țintă"}
PIVOT = 2  # a swing high/low has PIVOT lower highs / higher lows on each side
LOOKBACK = 40  # bars searched for the sweep -> shift -> zone chain
HTF_OF = {"M1": ("M15", "H1"), "M5": ("M15", "H1"), "M15": ("H1", "H4"), "M30": ("H1", "H4"), "H1": ("H4", "D1")}
TF_MIN = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def swings(bars: list[dict], n: int = PIVOT) -> tuple[list[int], list[int]]:
    """Indexes of confirmed swing highs and swing lows (fractal of n bars on each side)."""
    hi, lo = [], []
    for i in range(n, len(bars) - n):
        h, l_ = bars[i]["h"], bars[i]["l"]
        if all(h > bars[i - k]["h"] and h >= bars[i + k]["h"] for k in range(1, n + 1)):
            hi.append(i)
        if all(l_ < bars[i - k]["l"] and l_ <= bars[i + k]["l"] for k in range(1, n + 1)):
            lo.append(i)
    return hi, lo


def structure_trend(bars: list[dict]) -> int:
    """+1 higher highs and higher lows, -1 lower highs and lower lows, else the side of the last close vs the last two
    swings' midpoint when they disagree with each other (0 = range)."""
    if len(bars) < 2 * PIVOT + 5:
        return 0
    hi, lo = swings(bars)
    if len(hi) < 2 or len(lo) < 2:
        return 0
    hh = bars[hi[-1]]["h"] > bars[hi[-2]]["h"]
    hl = bars[lo[-1]]["l"] > bars[lo[-2]]["l"]
    if hh and hl:
        return 1
    if not hh and not hl:
        return -1
    c = bars[-1]["c"]
    if c > bars[hi[-1]]["h"]:
        return 1  # broke the last swing high: structure turned up
    if c < bars[lo[-1]]["l"]:
        return -1
    return 0


def _atr(bars: list[dict]) -> tuple[float | None, float | None]:
    a = ind.atr([b["h"] for b in bars], [b["l"] for b in bars], [b["c"] for b in bars], 14)
    vals = [x for x in a[-100:] if x is not None]
    return (ind.last(a), sum(vals) / len(vals) if vals else None)


def analyze(bars: list[dict], htf: dict[str, list[dict]], tf: str = "M1", spread: float = 0.0,
            now_ts: float | None = None, mode: str = "standard") -> dict:
    """bars: closed signal-timeframe bars (>= 60); htf: {tf: closed bars} of the two higher timeframes."""
    out = {"tf": tf, "htf": list(htf), "direction": 0, "score": 0, "verdict": "NONE", "title": "", "steps": [],
           "levels": None, "blocks": [], "warnings": [], "hold_bars": [2, 3],
           "hold_txt": f"{2 * TF_MIN.get(tf, 1)}–{3 * TF_MIN.get(tf, 1)} min", "notes": []}
    if len(bars) < 60:
        out["title"] = "Date insuficiente"
        return out
    atr, atr_mean = _atr(bars)
    if not atr or not atr_mean:
        out["title"] = "ATR indisponibil"
        return out
    ratio = atr / atr_mean
    out["atr"], out["atr_ratio"] = round(atr, 4), round(ratio, 2)
    if ratio >= 2.2:
        out["blocks"].append(f"Explozie de volatilitate (ATR {ratio:.1f}× media) — structura nu funcționează")
    elif ratio >= 1.6:
        out["warnings"].append(f"Volatilitate ridicată (ATR {ratio:.1f}× media)")
    if ratio < 0.45:
        out["blocks"].append(f"Piață moartă (ATR {ratio:.2f}× media) — mișcarea nu ajunge la țintă")
    ts = now_ts if now_ts is not None else bars[-1]["t"]
    hour = datetime.fromtimestamp(ts, timezone.utc).hour
    if tf in ("M1", "M5") and hour >= 21:
        out["blocks"].append("Rollover (după 21:00 UTC pe M1/M5) — spread larg")
    if hour < 6:
        out["warnings"].append("Sesiunea asiatică (înainte de 06:00 UTC) — spargeri false mai dese")

    # 1. higher-timeframe trend
    votes = {k: structure_trend(v) for k, v in htf.items()}
    out["htf_votes"] = votes
    vals = list(votes.values())
    up, dn = vals.count(1), vals.count(-1)
    if up and not dn:
        d, trend_state = 1, "pass" if up == len(vals) else "partial"
    elif dn and not up:
        d, trend_state = -1, "pass" if dn == len(vals) else "partial"
    else:
        d, trend_state = structure_trend(bars) or (1 if bars[-1]["c"] >= bars[-20]["c"] else -1), "fail"
    against = bool(up and dn) or (d == 1 and dn) or (d == -1 and up)
    side = "BUY" if d == 1 else "SELL"
    tf_txt = ", ".join(f"{k}: {'sus' if v == 1 else 'jos' if v == -1 else 'plat'}" for k, v in votes.items())
    steps = {"trend": (trend_state, f"{tf_txt}." + ("" if trend_state != "fail" else " Fără trend comun — scor maxim 50."))}

    # 2. sweep: a wick beyond a prior swing on the opposite side, closed back inside
    win = bars[-(LOOKBACK + 2 * PIVOT + 20):]
    off = len(bars) - len(win)
    hi, lo = swings(win)
    start = len(win) - LOOKBACK
    sweep_i, sweep_px, swept = None, None, None
    pool = hi if d == -1 else lo
    for i in range(len(win) - 1, start - 1, -1):
        b = win[i]
        for j in reversed(pool):
            if j >= i - PIVOT:
                continue
            lvl = win[j]["h"] if d == -1 else win[j]["l"]
            if (d == -1 and b["h"] > lvl and b["c"] < lvl) or (d == 1 and b["l"] < lvl and b["c"] > lvl):
                sweep_i, sweep_px, swept = i, (b["h"] if d == -1 else b["l"]), lvl
            break
        if sweep_i is not None:
            break
    if sweep_i is None:
        steps["sweep"] = ("fail", "Nicio ridicare de lichiditate în ultimele bare.")
    else:
        moved = (sweep_px - win[-1]["c"]) * (1 if d == -1 else -1)
        st = "pass" if moved >= 0.5 * atr else "partial"
        steps["sweep"] = (st, f"Bara {_hm(win[sweep_i]['t'])} a trecut de {'maximul' if d == -1 else 'minimul'} "
                              f"{swept:.2f} (umbra la {sweep_px:.2f}) și s-a închis înapoi.")

    # 3. structure shift after the sweep: close beyond the last opposite swing formed before the break
    shift_i, shift_lvl = None, None
    if sweep_i is not None:
        opp = lo if d == -1 else hi
        refs = [j for j in opp if j < sweep_i + 1]
        if refs:
            lvl = win[refs[-1]]["l"] if d == -1 else win[refs[-1]]["h"]
            for i in range(sweep_i + 1, len(win)):
                if (d == -1 and win[i]["c"] < lvl) or (d == 1 and win[i]["c"] > lvl):
                    shift_i, shift_lvl = i, lvl
                    break
    if shift_i is None:
        steps["shift"] = ("fail", "Structura nu s-a rupt încă în direcția trendului." if sweep_i is not None
                          else "Fără ridicare de lichiditate nu se caută ruperea.")
    else:
        steps["shift"] = ("pass", f"Bara {_hm(win[shift_i]['t'])} s-a închis {'sub' if d == -1 else 'peste'} "
                                  f"{shift_lvl:.2f} — structură ruptă.")

    # 4. order block: last opposite-colour candle before the displacement that made the shift
    zone = None
    if shift_i is not None:
        for i in range(shift_i, (sweep_i or shift_i) - 1, -1):
            b = win[i]
            if (d == -1 and b["c"] > b["o"]) or (d == 1 and b["c"] < b["o"]):
                zone = (min(b["o"], b["c"], b["l"]) if d == 1 else min(b["o"], b["c"]),
                        max(b["o"], b["c"]) if d == 1 else max(b["o"], b["c"], b["h"]))
                break
        if zone is None:
            b = win[sweep_i]
            zone = (b["l"], b["h"])
    price = bars[-1]["c"]
    if zone is None:
        steps["zone"] = ("fail", "Nu există zonă — întâi trebuie ruptă structura.")
    else:
        lo_z, hi_z = zone
        broken = any((d == -1 and x["c"] > hi_z) or (d == 1 and x["c"] < lo_z) for x in win[shift_i + 1:])
        inside = lo_z <= price <= hi_z
        dist = (lo_z - price) if d == -1 else (price - hi_z)
        if broken:
            steps["zone"] = ("fail", f"Zona {lo_z:.2f}–{hi_z:.2f} a fost spartă — nu mai e validă.")
            zone = None
        elif inside:
            steps["zone"] = ("pass", f"Prețul e în order block {lo_z:.2f}–{hi_z:.2f} — intrare la piață.")
        elif 0 < dist <= 2 * atr:
            steps["zone"] = ("partial", f"Order block {lo_z:.2f}–{hi_z:.2f}; aștept revenirea prețului (ordin limită).")
        else:
            steps["zone"] = ("fail", f"Order block {lo_z:.2f}–{hi_z:.2f} prea departe de preț.")
            zone = None

    # 5. room to the nearest opposing liquidity (swing on the target side)
    allhi, alllo = swings(bars[-200:])
    b200 = bars[-200:]
    targets = sorted([b200[j]["l"] for j in alllo if b200[j]["l"] < price - 1e-9], reverse=True) if d == -1 else \
        sorted([b200[j]["h"] for j in allhi if b200[j]["h"] > price + 1e-9])
    near = targets[0] if targets else None
    room = abs(price - near) / atr if near is not None else 3.0
    steps["room"] = ("pass" if room >= 1.5 else "partial" if room >= 0.75 else "fail",
                     (f"Nivelul opus {near:.2f} e la {room:.1f} ATR." if near is not None
                      else "Niciun nivel opus apropiat — spațiu liber."))

    score = 0.0
    for k, w in WEIGHTS.items():
        st = steps[k][0]
        score += w if st == "pass" else w / 2 if st == "partial" else 0
    if against or steps["trend"][0] == "fail":
        score = min(score, 50)
    score = int(round(score))
    out["direction"], out["score"] = d, score
    out["steps"] = [{"key": k, "name": NAMES[k], "weight": WEIGHTS[k], "status": steps[k][0], "text": steps[k][1]}
                    for k in WEIGHTS]
    reached = 0
    for k in WEIGHTS:
        if steps[k][0] == "fail":
            break
        reached += 1
    out["reached"] = reached

    # levels
    if zone is not None:
        entry = price if steps["zone"][0] == "pass" else (zone[0] if d == -1 else zone[1])
        etype = "market" if steps["zone"][0] == "pass" else "limit"
    else:
        entry, etype = price, "market"
    supports = []
    if zone is not None:
        supports.append(zone[1] if d == -1 else zone[0])
    if sweep_px is not None and abs(sweep_px - entry) <= 3 * atr:
        supports.append(sweep_px)
    if supports:
        far = max(supports) if d == -1 else min(supports)
        sl = far + d * -1 * 0.1 * atr
    else:
        sl = entry - d * 1.5 * atr
    if abs(entry - sl) < 0.4 * atr:
        sl = entry - d * 0.4 * atr
    risk = abs(entry - sl)
    out["levels"] = {"entry": round(entry, 2), "etype": etype, "sl": round(sl, 2), "tp1": round(entry + d * 2 * risk, 2),
                     "target": round(near, 2) if near is not None else None, "risk_atr": round(risk / atr, 2),
                     "room_atr": round(room, 2), "spread_vs_risk": round(spread / risk, 2) if risk else None}

    trace = steps["sweep"][0] != "fail" or steps["shift"][0] != "fail"
    aggressive = mode == "aggressive"
    out["mode"] = "aggressive" if aggressive else "standard"
    if aggressive:  # market conditions only warn; the sweep / shift trace is not required
        out["warnings"] = out["blocks"] + out["warnings"]
        out["blocks"] = []
        trace = True
    enter_at, wait_at = (45, 25) if aggressive else (50, 35)
    if out["blocks"]:
        out["verdict"], out["title"] = "STOP", "Așteaptă — piața nu e potrivită: " + "; ".join(out["blocks"])
    elif score >= enter_at and not against and trace:
        out["verdict"] = "ENTER"
        out["title"] = f"Intră {side} ({etype}, scor {score})"
    elif score >= wait_at:
        missing = [NAMES[k] for k in WEIGHTS if steps[k][0] == "fail"]
        out["verdict"] = "WAIT"
        out["title"] = f"Așteaptă {side} — lipsește: " + ", ".join(missing) if missing else f"Așteaptă {side}"
    else:
        out["verdict"], out["title"] = "NONE", f"Fără setup (scor {score})"
    return out


def _hm(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M")
