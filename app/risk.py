"""Deterministic Risk Engine (CONTRACT §6). Independent of the LLM."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from dataclasses import dataclass, field


def currency_legs(symbol: str, side: str) -> list[tuple[str, int]]:
    """EURUSD BUY -> [(EUR, +1), (USD, -1)]. Broker suffixes (EURUSD.a, EURUSDm) are ignored."""
    letters = "".join(ch for ch in symbol.upper() if ch.isalpha())
    if len(letters) < 6:
        return []
    sign = 1 if side.upper() == "BUY" else -1
    return [(letters[:3], sign), (letters[3:6], -sign)]


def currency_conflict(symbol: str, side: str, exposures: list[tuple[str, str]], limit: int) -> str | None:
    """Return a description of the first currency already held `limit` times in the same direction."""
    if limit <= 0:
        return None
    held: dict[tuple[str, int], int] = {}
    for sym, sd in exposures:
        for leg in currency_legs(sym, sd):
            held[leg] = held.get(leg, 0) + 1
    for ccy, sgn in currency_legs(symbol, side):
        if held.get((ccy, sgn), 0) >= limit:
            return f"{ccy} {'long' if sgn > 0 else 'short'} deja deschis de {held[(ccy, sgn)]}×"
    return None


def symbol_currencies(symbol: str) -> list[str]:
    return [c for c, _ in currency_legs(symbol, "BUY")]


def news_blocker(symbol: str, events: list, now_ts: float, minutes: int, min_imp: int) -> str | None:
    """Reason text for the nearest event (imp >= min_imp, one of the symbol's currencies) within ±minutes, else None."""
    if minutes <= 0 or not events:
        return None
    ccys = set(symbol_currencies(symbol))
    best = None
    for ev in events:
        try:
            t, imp, ccy = float(ev["t"]), int(ev.get("imp") or 0), str(ev.get("ccy") or "").upper()
        except (KeyError, TypeError, ValueError):
            continue
        if imp < min_imp or ccy not in ccys or abs(t - now_ts) > minutes * 60:
            continue
        if best is None or abs(t - now_ts) < abs(best[0] - now_ts):
            best = (t, ccy, imp, str(ev.get("name") or "?"))
    if best is None:
        return None
    t, ccy, imp, name = best
    mins = round(abs(t - now_ts) / 60)
    when = f"peste {mins} min" if t >= now_ts else f"acum {mins} min"
    return f"Știre {name} ({ccy}, impact {imp}) {when} — blocare ±{minutes} min"


def position_risk_money(side: str, price_open: float, sl: float | None, volume: float, info: dict) -> float | None:
    """Money lost if SL is hit; 0 when SL is at/after break-even; None when not computable."""
    if not sl:
        return None
    sign = 1 if side == "BUY" else -1
    if (float(sl) - float(price_open)) * sign >= 0:
        return 0.0
    tick_size = float(info.get("tick_size") or info.get("point") or 0)
    tick_value = float(info.get("tick_value") or 0)
    if tick_size <= 0 or tick_value <= 0 or not volume:
        return None
    return risk_money(float(volume), abs(float(price_open) - float(sl)), tick_size, tick_value)


@dataclass
class RiskContext:
    mode: str
    execution_enabled: bool
    entries_paused: bool
    side: str
    entry: float | None
    sl: float | None
    tp: float | None
    symbol_info: dict  # quote + symbol spec (bid, ask, point, digits, volume_*, tick_value, tick_size, stops_level)
    account: dict | None  # account used for risk (paper or MT5)
    risk: dict  # risk settings (lower_snake_case)
    bridge_connected: bool = False
    terminal: dict | None = None  # {"trade_allowed": bool}
    mt5_account: dict | None = None  # raw MT5 account (DEMO checks)
    tick_age: float | None = None
    open_bot_positions: int = 0
    ea_magic_ok: bool = True
    has_bot_position_on_symbol: bool = False
    symbol: str = ""
    open_exposures: list = field(default_factory=list)  # [(symbol, side)] of bot positions + in-flight opens
    trades_today: int = 0
    day_start_equity: float | None = None
    peak_equity: float | None = None
    consecutive_losses: int = 0
    loss_pause_until: float | None = None  # ts until which the loss-streak pause blocks entries
    open_risk_money: float = 0.0  # risk money of open bot positions + in-flight opens (BE positions = 0)
    news_block: str | None = None  # reason when inside a news blackout window
    news_note: str = ""  # detail when not blocked (e.g. calendar unavailable)
    test_order: bool = False
    account_settling: bool = False  # MT5 login switched moments ago: balance/equity may be the old account's
    checks: list = field(default_factory=list)


def floor_to_step(vol: float, step: float) -> float:
    if step <= 0:
        return vol
    n = math.floor(vol / step + 1e-9)
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(n * step, decimals + 2)


def risk_money(volume: float, sl_dist: float, tick_size: float, tick_value: float) -> float:
    if tick_size <= 0 or tick_value <= 0:
        return float("inf")
    return sl_dist / tick_size * tick_value * volume


def calc_volume(equity: float, risk_pct: float, sl_dist: float, info: dict) -> float:
    tick_size = float(info.get("tick_size") or info.get("point") or 0)
    tick_value = float(info.get("tick_value") or 0)
    if sl_dist <= 0 or tick_size <= 0 or tick_value <= 0:
        return 0.0
    loss_per_lot = sl_dist / tick_size * tick_value
    raw = equity * risk_pct / 100.0 / loss_per_lot
    vol = floor_to_step(raw, float(info.get("volume_step") or 0.01))
    vmax = float(info.get("volume_max") or 0)
    if vmax and vol > vmax:
        vol = floor_to_step(vmax, float(info.get("volume_step") or 0.01))
    return vol


def evaluate(ctx: RiskContext) -> dict:
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    r = ctx.risk
    info = ctx.symbol_info or {}
    acc = ctx.account or {}
    mode = (ctx.mode or "").upper()
    point = float(info.get("point") or 0.00001)
    digits = int(info.get("digits") or 5)
    equity = float(acc.get("equity") or 0)

    add("mode_allows_execution", mode in ("PAPER", "DEMO") and ctx.execution_enabled,
        f"mod={mode}, execuție={'armată' if ctx.execution_enabled else 'dezarmată'}"
        + (" (LIVE este dezactivat în v1)" if mode == "LIVE" else ""))
    add("entries_not_paused", not ctx.entries_paused, "intrări în pauză" if ctx.entries_paused else "intrări active")
    if mode == "DEMO":
        add("bridge_connected", ctx.bridge_connected, "bridge MT5 conectat" if ctx.bridge_connected else "bridge MT5 deconectat")
        tm = (ctx.mt5_account or {}).get("trade_mode")
        add("account_is_demo", tm == 0, f"trade_mode={tm} (0 = DEMO)")
        term_ok = bool((ctx.terminal or {}).get("trade_allowed")) and bool((ctx.mt5_account or {}).get("trade_expert"))
        add("terminal_trade_allowed", term_ok,
            "AutoTrading permis" if term_ok else "AutoTrading dezactivat în terminal sau trade_expert=false")
        add("ea_magic_matches", ctx.ea_magic_ok,
            "magic EA = MT5_MAGIC" if ctx.ea_magic_ok else "magic EA diferit de MT5_MAGIC")
        add("account_settled", not ctx.account_settling,
            "cont MT5 schimbat recent — aștept datele noului cont" if ctx.account_settling else "cont MT5 stabil")

    max_age = float(r.get("max_tick_age_seconds", 10))
    fresh = ctx.tick_age is not None and ctx.tick_age <= max_age
    add("quote_fresh", fresh, f"vârsta tick {None if ctx.tick_age is None else round(ctx.tick_age, 1)} s (max {max_age:g})")

    bid, ask = info.get("bid"), info.get("ask")
    spread_pts = info.get("spread_points")
    if spread_pts is None and bid and ask:
        spread_pts = round((ask - bid) / point)
    max_spread = float(r.get("max_spread_points", 30))
    crossed = bool(bid and ask and bid > ask)
    add("quote_not_crossed", not crossed, f"bid {bid} / ask {ask}" + (" — cotație inversată" if crossed else ""))
    add("spread_ok", spread_pts is not None and 0 <= spread_pts <= max_spread, f"spread {spread_pts} / max {max_spread:g} puncte")

    has_sl_tp = bool(ctx.sl) and bool(ctx.tp)
    side_ok = True
    if has_sl_tp and ctx.entry:
        if ctx.side == "BUY":
            side_ok = ctx.sl < ctx.entry < ctx.tp
        else:
            side_ok = ctx.tp < ctx.entry < ctx.sl
    add("sl_tp_present", has_sl_tp and side_ok,
        f"SL={ctx.sl}, TP={ctx.tp}" + ("" if side_ok else " (pe partea greșită a prețului)"))

    sl_dist = abs((ctx.entry or 0) - (ctx.sl or 0)) if has_sl_tp and ctx.entry else 0.0
    stops_level = float(info.get("stops_level") or 0)
    min_dist = stops_level * point
    add("sl_distance_ok", sl_dist > 0 and sl_dist >= min_dist and abs((ctx.tp or 0) - (ctx.entry or 0)) >= min_dist,
        f"distanță SL {sl_dist / point:.0f} puncte (minim broker {stops_level:g})")

    vmin = float(info.get("volume_min") or 0.01)
    tick_size = float(info.get("tick_size") or point)
    tick_value = float(info.get("tick_value") or 0)
    max_risk_pct = float(r.get("max_risk_per_trade_pct", 0.25))
    volume = 0.0
    new_risk = 0.0
    if ctx.test_order:
        volume = vmin
        rm = risk_money(volume, sl_dist, tick_size, tick_value) if sl_dist else float("inf")
        limit = equity * 2 * max_risk_pct / 100.0
        if tick_value <= 0 or tick_size <= 0:
            add("volume_ok", False, "tick_value/tick_size indisponibil — riscul nu poate fi calculat")
        else:
            new_risk = rm
            add("volume_ok", sl_dist > 0 and equity > 0 and rm <= limit,
                f"test: volum minim {vmin} → risc {rm:.2f} (limită 2×{max_risk_pct:g}% = {limit:.2f})")
    else:
        volume = calc_volume(equity, max_risk_pct, sl_dist, info) if equity > 0 else 0.0
        vcap = float(r.get("max_volume_lots") or 0)
        if vcap and volume > vcap:
            volume = floor_to_step(vcap, float(info.get("volume_step") or 0.01))
        if volume < vmin:
            add("volume_ok", False, f"risc prea mic pentru lotul minim (calculat {volume}, minim {vmin})")
        else:
            rm = risk_money(volume, sl_dist, tick_size, tick_value)
            new_risk = rm
            add("volume_ok", True, f"volum {volume} → risc {rm:.2f} ({rm / equity * 100:.2f}% din {equity:.2f}, "
                                   f"țintă {max_risk_pct:g}%)")

    max_open = int(r.get("max_open_positions", 1))
    add("max_open_positions", ctx.open_bot_positions < max_open, f"{ctx.open_bot_positions}/{max_open} poziții deschise")
    max_tr = int(r.get("max_trades_per_day", 5))
    add("max_trades_per_day", ctx.trades_today < max_tr, f"{ctx.trades_today}/{max_tr} tranzacții azi")

    dse = ctx.day_start_equity
    if dse and dse > 0:
        loss_pct = (dse - equity) / dse * 100
        lim = float(r.get("max_daily_loss_pct", 1.0))
        add("daily_loss_ok", loss_pct < lim, f"pierdere azi {max(loss_pct, 0):.2f}% / max {lim:g}%")
    else:
        add("daily_loss_ok", equity > 0, "fără referință equity de început de zi")
    peak = ctx.peak_equity
    if peak and peak > 0:
        dd = (peak - equity) / peak * 100
        lim = float(r.get("max_equity_drawdown_pct", 3.0))
        add("drawdown_ok", dd < lim, f"drawdown {max(dd, 0):.2f}% / max {lim:g}%")
    else:
        add("drawdown_ok", equity > 0, "fără referință equity maxim")
    max_cl = int(r.get("max_consecutive_losses", 3))
    pause_min = int(r.get("loss_streak_pause_minutes", 90))
    if ctx.consecutive_losses >= max_cl:
        until = (f"pauză până la {datetime.fromtimestamp(ctx.loss_pause_until, timezone.utc):%H:%M} UTC"
                 if ctx.loss_pause_until else "pauză până la sfârșitul zilei")
        cl_detail = f"{ctx.consecutive_losses}/{max_cl} pierderi consecutive — {until}"
    else:
        cl_detail = (f"{ctx.consecutive_losses}/{max_cl} pierderi consecutive (pauză "
                     + (f"{pause_min} min" if pause_min > 0 else "restul zilei") + " la atingere)")
    add("consecutive_losses_ok", ctx.consecutive_losses < max_cl, cl_detail)
    max_tor = float(r.get("max_total_open_risk_pct", 2.0))
    total_risk = ctx.open_risk_money + (new_risk if math.isfinite(new_risk) else 0.0)
    tor_pct = total_risk / equity * 100 if equity > 0 else float("inf")
    add("total_open_risk_ok", equity > 0 and math.isfinite(new_risk) and tor_pct <= max_tor + 1e-9,
        f"risc deschis {ctx.open_risk_money:.2f} + nou {new_risk:.2f} = {tor_pct:.2f}% / max {max_tor:g}%"
        if equity > 0 else "equity indisponibil")
    add("news_blackout_ok", ctx.news_block is None, ctx.news_block or ctx.news_note or "fără știri importante în fereastră")
    mf = float(acc.get("margin_free") or 0)
    fm_pct = (mf / equity * 100) if equity > 0 else 0.0
    min_fm = float(r.get("min_free_margin_pct", 50))
    add("free_margin_ok", fm_pct >= min_fm, f"marjă liberă {fm_pct:.1f}% / minim {min_fm:g}%")
    corr_limit = int(r.get("max_same_currency_direction", 1))
    conflict = currency_conflict(ctx.symbol or ctx.symbol_info.get("symbol") or "", ctx.side, ctx.open_exposures, corr_limit)
    add("currency_exposure_ok", conflict is None,
        f"corelație: {conflict} (max {corr_limit})" if conflict else f"nicio expunere duplicată pe monedă (max {corr_limit})")
    add("no_existing_bot_position_on_symbol", not ctx.has_bot_position_on_symbol,
        "există deja o poziție a botului pe simbol" if ctx.has_bot_position_on_symbol else "nicio poziție a botului pe simbol")

    passed = all(c["ok"] for c in checks)
    return {"passed": passed, "checks": checks, "volume": volume}


def failed_summary(rc: dict) -> str:
    bad = [f"{c['name']}: {c['detail']}" for c in rc.get("checks", []) if not c["ok"]]
    return "; ".join(bad) if bad else "OK"
