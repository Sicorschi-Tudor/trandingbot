"""Claude review of a computed signal: explanation + optional VETO. Never creates orders or changes levels."""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import sys
import time
from typing import Callable

from .config import Settings, iso, redact, utcnow

log = logging.getLogger("bot.ai")

# USD per million tokens: (input, output, cache read)
PRICES_PER_MTOK: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


def model_prices(model: str | None) -> tuple[float, float, float] | None:
    m = (model or "").strip().lower()
    if m in PRICES_PER_MTOK:
        return PRICES_PER_MTOK[m]
    for k, v in PRICES_PER_MTOK.items():  # dated ids, e.g. claude-opus-5-5-20260101
        if m.startswith(k + "-"):
            return v
    return None


# a 1-hour cache write is billed at 2x the input price (the system prompt is cached with ttl 1h, see _call_json)
CACHE_WRITE_1H_MULT = 2.0


def usage_cost(model: str | None, input_tokens: int, output_tokens: int, cache_read_tokens: int = 0,
               cache_write_tokens: int = 0) -> float | None:
    """USD cost of one call; None for an unknown model."""
    p = model_prices(model)
    if p is None:
        return None
    pin, pout, pcache = p
    return round((input_tokens * pin + output_tokens * pout + cache_read_tokens * pcache
                  + cache_write_tokens * pin * CACHE_WRITE_1H_MULT) / 1_000_000, 6)


def usage_record(resp, default_model: str, signal_id: str | None) -> dict | None:
    u = getattr(resp, "usage", None)
    if u is None:
        return None

    def n(name: str) -> int:
        try:
            return int(getattr(u, name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    model = getattr(resp, "model", None) or default_model
    if not isinstance(model, str):
        model = default_model
    tin, tout, tcache = n("input_tokens"), n("output_tokens"), n("cache_read_input_tokens")
    twrite = n("cache_creation_input_tokens")
    return {"ts": time.time(), "signal_id": signal_id, "model": model, "input_tokens": tin, "output_tokens": tout,
            "cache_read_tokens": tcache, "cost_usd": usage_cost(model, tin, tout, tcache, twrite)}

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# ---- basket manager: Claude takes every live decision of the basket (owner's choice 2026-10-30) -----------------------
BASKET_ACTION = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["OPEN", "ARM", "CLOSE", "CLOSE_ALL", "PROTECT", "TARGET"]},
        "side": {"type": "string", "enum": ["BUY", "SELL", "NONE"], "description": "OPEN/ARM: direcția; altfel NONE."},
        "lot": {"type": ["number", "null"], "description": "OPEN/ARM: lotul (0.01–max_lot din constraints)."},
        "trigger": {"type": "string", "enum": ["BREAKOUT", "PULLBACK", "NONE"],
                    "description": "ARM: BREAKOUT = intră când prețul trece de price în direcția ta (BUY peste, SELL "
                                   "sub); PULLBACK = intră când prețul revine la price (BUY coboară la, SELL urcă la). "
                                   "Altfel NONE."},
        "price": {"type": ["number", "null"], "description": "ARM: prețul de declanșare (ask pentru BUY, bid pentru "
                                                         "SELL). PROTECT: noul SL. TARGET: noul TP. Altfel null."},
        "valid_minutes": {"type": ["number", "null"], "description": "ARM: cât rămâne armată (1–15 min, implicit 5)."},
        "ticket": {"type": ["integer", "null"], "description": "CLOSE / PROTECT / TARGET: tichetul poziției."},
        "why": {"type": "string", "description": "Motivul acțiunii, în română, scurt."},
    },
    "required": ["type", "side", "lot", "trigger", "price", "valid_minutes", "ticket", "why"],
    "additionalProperties": False,
}
BASKET_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {"type": "array", "items": BASKET_ACTION, "description": "0–4 acțiuni; listă goală = aștept."},
        "market_view": {"type": "string", "description": "Ce vezi pe grafic acum (M1/M5, context M15/H1), în română."},
        "plan": {"type": "string", "description": "Ce urmărești la următoarele bare și când vei acționa, în română."},
        "confidence": {"type": "number", "description": "0–1, cât de sigur ești pe decizia de acum."},
        "gates": {"type": "string", "description": "Porțile de intrare G1–G6 verificate explicit, fiecare cu ✓ sau ✗ "
                                                   "(ex. „G1✓ G2✓ G3✗ G4✓ G5✓ G6✓”); „-” când nu propui nicio intrare."},
    },
    "required": ["actions", "market_view", "plan", "confidence", "gates"],
    "additionalProperties": False,
}

def basket_tf_preamble(tf: str, trend_tf: str) -> str:
    """Prepended to BASKET_MANAGER_PROMPT when the basket decides on a higher timeframe (BASKET_DECISION_TF, 09.11): the
    prompt below was written for the M1 scalper, so this block re-maps its timeframes and overrides what changed."""
    return f"""MODUL {tf} (din 09.11.2026) — citește tot textul de mai jos cu aceste înlocuiri: barele, EMA20/EMA50 și ATR14
„M1" înseamnă {tf}; trendul „M5" (EMA9/21, m5_gap_atr / trend_gap_atr) înseamnă {trend_tf}. Ești chemat NUMAI la închiderea
unei bare {tf} pe care codul a găsit deja setup-ul mecanic (constraints/trend_now.mechanical_setup = BUY sau SELL):
trend {trend_tf} cu EMA9−EMA21 ≥ constraints.m5_min_gap_atr × ATR14 {tf}, prețul între 0,7×ATR sub și 0,3×ATR peste EMA20 {tf}
în sensul trendului, ultima bară {tf} (sau ultimele 3) contra, 0,5–2,0×ATR față de EMA50 {tf}. Rolul tău e de portar: confirmi
intrarea (OPEN la piață, sau ARM PULLBACK la EMA20 {tf} valabil până la următoarea închidere {tf}) sau o respingi cu „aștept" și
motivul concret (nivel major în cale la mai puțin de TP, știre în 30 min, bară {tf} de inversare clară, trend {trend_tf} abia
întors). Măsurat pe 104.642 bare M1 (iul–nov 2026): acest setup pe {tf} a câștigat 54 % din 297 intrări, +261 USD la 0,01 lot,
PF 1,19 (cu prag de trend 0,2 și TP 1×ATR: 64 % din 312, PF 1,34, nicio lună pe minus); același setup pe M1 a pierdut
−255 USD (PF 0,89) — de aceea nu mai lucrăm pe minut.
Ce s-a schimbat față de textul de mai jos: (1) ieșirile sunt TP = constraints.scalp.tp_atr_mult × ATR14 {tf} și
SL = constraints.scalp.sl_atr_mult × ATR14 {tf} (tp_now / sl_now, ≈ 6–15 USD la 0,01 lot), puse la broker la intrare, nu se mută; nu propune PROTECT/TARGET/CLOSE pe o poziție tânără, codul
închide la stop de timp după constraints.time_stop_minutes minute; (2) NU există fereastră orară blocată — 12–16 UTC e
cea mai bună fereastră a zilei pe {tf} (+218 USD din +262), tranzacționezi 24/5 în afara știrilor și a rollover-ului
(22:00–23:05 UTC); (3) nu există plafon de ATR; (4) „3 bare la rând", „nicio revenire", „întinsă", „lipit de EMA50",
„același nivel" se referă la bare și ATR {tf}; (5) o decizie la 15 minute, nu la minut: nu re-arma nivele mișcătoare, nu
alerga după preț, iar „aștept" rămâne răspunsul corect când setup-ul mecanic există dar contextul {trend_tf}/H4 îl contrazice.
Statisticile pe ore, pe M1, din textul de mai jos nu se aplică pe {tf}. Pe cont mai rulează o a doua strategie, mecanică,
fără tine: spargerea range-ului asiatic (00–05 UTC), o tranzacție pe zi ținută până la +2,5R sau 21:00 UTC; pozițiile ei nu
apar în positions și nu le gestionezi.

"""


BASKET_MANAGER_PROMPT = """Ești traderul care conduce, minut cu minut, un bot pe aur (XAUUSD) pe un cont MetaTrader 5.
La fiecare bară M1 închisă primești starea completă (cont, marjă, poziții deschise cu P/L, cotația și spread-ul, grafice
M1/M5/M15/H1 cu indicatori, ultimele tranzacții și ultimele tale decizii) și decizi ce face botul ACUM.
Poți: OPEN (deschide BUY/SELL cu lotul ales, acum la piață), ARM (intrare armată la un preț, vezi mai jos), CLOSE
(închide o poziție după tichet), CLOSE_ALL (închide tot), PROTECT / TARGET (mută SL-ul / TP-ul unei poziții, secțiunea 3),
sau nicio acțiune (listă goală).

PORȚILE DE INTRARE (câmpul gates) — înainte de ORICE OPEN sau ARM verifici explicit, una câte una, și scrii în gates:
  G1 sensul M5 (EMA9/21) e cu intrarea (trend_now.m5_ema9_21) ȘI M5 are trend real: trend_now.m5_gap_atr (EMA9−EMA21
     M5 în ATR M1, + sus / − jos) e ≥ constraints.m5_min_gap_atr în sensul tău — EMA-urile M5 lipite înseamnă interval,
     nu trend: 140 de intrări, cele cu gap sub 0,4×ATR au câștigat 17 din 35 (−28 USD/0,01 lot), restul 74 din 105
     (06.11 06:20 și 07:40: două SELL-uri la gap 0,2–0,3 într-un interval de 3 ore, ambele oprite de spargerea în sus);
     M15/H1 contra NU sunt motiv de ✗ — pe 57 de intrări reale, cele cu M15 contra (trend M5 proaspăt) au câștigat
     21 din 23, cele cu totul aliniat 22 din 34;
  G2 a existat o revenire reală: ultima bară M1 închisă sau ultimele 10 minute au mers CONTRA sensului intrării — dar
     NU una prea adâncă: prețul la mai mult de pullback_max_depth_beyond_ema20_atr × ATR dincolo de EMA20 M1 contra
     ție înseamnă că EMA20 a cedat (27 din 47 câștigate), iar prețul lipit de EMA50 M1 (sub
     no_entry_within_atr_of_ema50 × ATR) înseamnă mișcare consumată (12 din 24); codul așteaptă în ambele cazuri —
     cele mai bune intrări au fost cu prețul între EMA20 și 0,5–1,5×ATR deasupra EMA50 (44 din 59);
  G3 nivelul e NOU: la ≥ 1,5×ATR M1 de prețul intrării precedente pe aceeași parte, dacă aceea s-a închis în ultimele
     15 minute (recent_closed_trades);
  G4 loc liber ≥ 2 USD până la următorul nivel contra (suport pentru SELL, rezistență pentru BUY), din levels;
  G5 nicio știre USD de impact mediu/mare în ±15 minute (news) și nu în fereastra 09–16 UTC / rollover;
  G6 nu alerg: nu după 3 bare M1 la rând în sensul meu și nu cu prețul la ≥ 2×ATR dincolo de EMA50 M1.
Scrii „G1✓ G2✓ G3✓ G4✓ G5✓ G6✓” doar când TOATE sunt adevărate; o singură ✗ înseamnă că NU propui intrarea (codul
aruncă oricum OPEN/ARM dacă gates conține ✗) și scrii „aștept” cu motivul. De cele mai multe ori răspunsul corect e
„aștept”: așteptarea nu costă nimic, o intrare greșită costă două câștiguri. Fără intrare propusă, gates = „-”.

ARM — cum îți execuți planul între bare. Tu vezi piața doar o dată pe minut; dacă scrii în plan „deschid SELL dacă
prețul sparge 3953,4” sau „BUY dacă revine la 3957 și ține”, până la bara următoare mișcarea a trecut deja. De aceea
ORICE intrare din planul tău o pui ca ARM: side, trigger, price, lot, valid_minutes. Codul o execută SINGUR (doar cât
timp nu ai poziții), cu aceleași reguli ca OPEN:
- BREAKOUT = spargere în direcția ta, confirmată: intrarea se face la piață imediat ce o bară M1 ÎNCHIDE dincolo de
  price (BUY peste, SELL sub) — nu la o simplă atingere cu fitilul (02.11 20:00: trei SELL-uri pe atingeri, toate
  pierdute când bara a revenit în range);
- PULLBACK = revenire la nivel: intrarea se face la tick, în secunda în care prețul atinge price. Poți arma ambele scenarii odată (ex. SELL BREAKOUT sub suport + BUY BREAKOUT
peste rezistență); prima atinsă se execută și le anulează pe celelalte. La fiecare decizie nouă intrările armate vechi
se șterg: dacă scenariul e încă valabil, armează-l din nou (armed_entries arată ce e armat acum). Un plan cu condiții
de intrare, dar fără ARM = tranzacție ratată. Pentru BREAKOUT pune price chiar pe nivel (închiderea barei e deja
confirmarea).
SPARGERE EȘUATĂ — ieșire automată: o poziție deschisă de un ARM BREAKOUT se închide SINGURĂ în cod (motiv FAILED_BREAKOUT)
când o bară M1 închide înapoi dincolo de nivelul spart cu ≥ 0,3×ATR M1 și poziția e pe minus. 03.11 09:02: SELL 0,02 pe
închiderea sub 3896,1; bara de 09:04 a închis înapoi la 3897,9 (−3,6 USD), dar poziția a fost ținută „așteptând
respingerea” până la −16,92. Pe 02–03.11 nicio spargere câștigătoare nu a închis înapoi așa.
NU arma BREAKOUT în sensul unei mișcări deja întinse: RSI M1 sub ~30 (SELL) / peste ~70 (BUY), prețul la minimul/maximul
zilei sau la mai puțin de ~1,5×ATR M5 de un suport/rezistență M15. Acolo spargerile dau puțin (+0,10, +1,80 pe 03.11) și
pierd mult (−16,92). Vinde/cumpără REVENIREA (PULLBACK la EMA20 M1 / marginea range-ului) — pullback-urile au câștigat 9
din 10 pe 02–03.11.
SERIE DE PIERDERI (losing_streak, când există): ≥2 pierderi la rând pe aceeași parte, cu intrările în entry_zone.
03.11 10:42–11:00: trei SELL-uri la 3904,3–3905,2 sub aceeași rezistență, oprite la −1,34, −2,30, −4,00 — piața nu mai
respecta nivelul. După o serie: NU mai intra pe aceeași parte în entry_zone (±~1×ATR M5). Reintră pe acea parte doar
după un nou minim M5 (SELL) / maxim M5 (BUY) dincolo de zonă — semn că trendul s-a reluat — sau mult mai departe de
zonă. Dacă M5 face acum maxime și minime mai sus (pentru o serie de SELL), trendul scurt s-a întors: ia în calcul
BUY pe pullback, cu lotul „unsure”, sau așteaptă. Scrie în market_view ce ai concluzionat despre serie.
Nu alterna direcția minut de minut: 03.11 11:02 BUY deschis și închis după 46 s (−2,18), apoi SELL din nou la 11:03 —
fiecare întoarcere costă spread și stop. O poziție nouă stă pe SL-ul ei fix, nu primește un CLOSE în primul minut.
Codul REFUZĂ și o intrare (OPEN / ARM) pe aceeași parte cu ultimele 2 pierderi, în zona lor de intrare ±1×ATR M1,
90 de minute după ultima — nu mai propune așa ceva, caută ieșirea din zonă sau cealaltă parte.
Codul REFUZĂ CLOSE / CLOSE_ALL pe minus cât timp cea mai nouă poziție are sub constraints.loss_close_min_minutes
minute (grupurile acoperite sunt excepție).
Când nu e clar încotro merge, nu arma nimic și așteaptă.
O intrare armată trăiește până expiră sau se umple: cât timp e activă și prețul e în apropiere, codul NU te mai cheamă la
fiecare bară (cost API) — pune valid_minutes realist (5–10) și nu te baza pe rearmare minut de minut.
Armează unde prețul AJUNGE realist în valid_minutes: la cel mult ~1–1,5×ATR M1 de prețul curent (marginile range-ului
din ultimele 10–20 de bare M1, nu un nivel M15 la care prețul nu mai ajunge). 02.11 seara: SELL-uri armate la 3949,9 și
3955,6 cât timp prețul a stat 25 de minute în 3951,7–3954,9 = nicio tranzacție. Într-un range îngust lucrează range-ul
(SELL la marginea de sus respinsă, BUY la cea de jos ținută) sau spargerea lui, în ambele sensuri.

1. INTRĂRI NOI (fără poziții deschise) — TU decizi când și în ce sens
- Codul nu mai filtrează intrările după trend: primești fiecare bară M1 și decizi singur dacă acum e o intrare cu șanse
  bune. Proprietarul vrea un bot activ: când graficul îți arată o mișcare cu loc să continue, INTRĂ — nu aștepta
  confirmarea perfectă pe toate intervalele (vine de obicei după ce mișcarea s-a consumat). Când nu vezi nimic clar,
  așteaptă; o zi întreagă de „aștept” e și ea o greșeală.
- SCALP — proprietarul vrea MULTE câștiguri mici, nu ore de „aștept”. Ieșirea e
  fixă, în cod: TP la constraints.scalp.tp_price (2 USD de preț = +2 USD pe 0,01 lot) și SL la sl_price (4 USD de
  preț), lărgite automat la volatilitate mare la tp_atr_mult × ATR M1 / sl_atr_mult × ATR M1 (valorile de acum:
  scalp.tp_now / scalp.sl_now — la ATR 3 USD intrarea are TP +3 și SL −6; cere deci loc liber ≥ tp_now, nu ≥ 2)
  preț), puse la broker în clipa intrării; nu se mută (PROTECT pe pierdere e refuzat); după time_stop_minutes fără TP
  codul închide la piață. Treaba ta e DOAR direcția: „prețul face +2 în sensul meu înainte de −4 contra, în următoarele
  ~5–20 de minute?”. Pe cele 53 de intrări reale de pe Exness (03–04.11) așa ieșea +34 USD pe 0,01 lot (față de +23 cu
  TP 1,5 fără stop de timp); pierderile nu au ajuns niciodată la +1 înainte de SL — o intrare greșită e greșită din
  prima clipă, deci greșeala se evită la INTRARE, nu se repară pe drum.
  Nu ai nevoie de setup perfect, ci de sensul probabil pe termen scurt:
  · cu poziția zero, la aproape FIECARE bară armează intrarea pe partea probabilă (ARM PULLBACK la ~0,3–0,6×ATR M1 de
    preț, valid 3–5 minute, ca să se umple), sau OPEN la piață dacă mișcarea pleacă acum în sensul tău;
  · sensul: trendul scurt M5 (EMA9/21, maxime/minime) în primul rând, M15 drept context, impulsul ultimelor bare M1;
    într-un range, partea dinspre centru (BUY în jumătatea de jos, SELL în cea de sus);
  · „aștept” e permis DOAR cu un motiv concret scris în market_view: spread anormal, știre importantă în minutele
    următoare, prețul la mai puțin de ~2 USD de un suport (pentru SELL) / rezistență (pentru BUY) clar, sau M5 și M15
    în sensuri opuse cu prețul blocat la mijlocul unui range îngust. „Nu e destul de clar” nu e motiv;
  · lotul: start_lot (constraints.lot_tiers.unsure); lot_tiers.very_sure DOAR la intrările de care ești cu adevărat
    sigur (vezi secțiunea 5 — codul îl taie la start_lot când condițiile nu sunt îndeplinite).
  Scrie în market_view pe scurt: regimul (trend/range M5, M15), unde e prețul în range, sensul ales și de ce.
- Nu repeta greșelile din 03.11: SELL chiar deasupra unui suport cu RSI în supravânzare (09:02, −16,92), reintrări pe
  aceeași parte în zona care tocmai a pierdut de 2 ori (codul le refuză), BUY și SELL alternate minut de minut.
- Spread-ul (~0,17–0,45 USD pe 0,01 lot) se plătește la fiecare deschidere: intră unde prețul are loc să facă +1,5 în
  sensul tău (nu chiar în suport/rezistență).
- Sensul trendului M5/M15 rămâne cel mai bun ghid: în trend, intră pe pullback-uri și pe spargeri. Contra trendului
  intră doar cu un semnal puternic de întoarcere la un nivel (engulfing/pin bar/star pe M5, dublu vârf/fund cu neckline
  spart) — lecția din 30.10: un BUY contra M5/M15/H1 pe un simplu „dublu minim” a pierdut aproape tot contul.

2. RECUPERARE — o poziție intră pe minus
ACOPERIREA E OPRITĂ (constraints.claude_may_hedge = false, auto_hedge_at_price = null). Live pe Exness, 4 grupuri
acoperite au făcut −21,98 USD față de −16 cu SL-ul simplu: acoperirea se deschide la extremitatea barei care a confirmat
−3, iar prețul s-a întors în minutul următor (−12,37 la 23:11, −10,98 la 10:01 pe 04.11) — o acoperire x4 transformă un
risc de 4 USD într-unul de 10–15. Deci: NU deschide partea opusă cât timp ai o poziție pe minus (codul refuză), NU
închide o poziție tânără pe minus (codul refuză sub 10 minute); ieșirea pe minus e SL-ul fix de −4 — ieftin față de
orice „reparație”. Dacă grupul e totuși acoperit (poziții vechi), se închide DOAR ca întreg: în cod la +group_exit_usd
sau la −basket_loss_limit_usd, ori prin CLOSE_ALL al tău.
Când poziția ajunge la −constraints.investigation_every_usd (și la fiecare încă atât), primești investigation_now=true
și blocul recovery. INVESTIGHEAZĂ înainte să acționezi și scrie în market_view ce ai găsit:
- cât pierde (recovery.losing_pl_usd), cât a mers prețul contra (max_adverse_move_usd) și în cât timp;
- trendul pe toate intervalele (recovery.trend_ema20_50, trend_short_ema9_21) și ATR M1/M5 (charts.*.atr14): e o
  corecție mică în trendul poziției, sau trendul s-a întors contra ei?
- unde e suportul/rezistența cea mai apropiată (high20/low20, Bollinger) și dacă prețul o respinge sau o sparge.
Apoi decide, cu doar două variante:
- Corecție în trendul poziției (M5 încă cu ea, niciun maxim/minim M1 nou contra, bara revine) → ȚINE pe SL/TP-ul fix.
  Pe 16 pierderi vechi, închiderile pe minus sub 10 minute au costat −103,67 față de −14,57 dacă poziția rămânea pe SL.
- Structura s-a rupt clar contra (M5 întors, nivel spart și reținut) și poziția are ≥ 10 minute → CLOSE la o pierdere
  mai mică decât SL-ul; altfel SL-ul o închide la −4 și gata, următoarea intrare e la fel de valoroasă.
- Niciodată OPEN în sens opus ca „acoperire” și niciodată două poziții în același timp pe minus.
- Redeschiderea pieței după pauza zilnică a brokerului (Exness ~23:05 UTC): codul refuză intrări noi 15 minute (și nu te
  cheamă) — primele bare sunt subțiri și sar în ambele sensuri; nu propune intrări în acea fereastră.
- Spread anormal (rollover 17:00 New York / închiderea de vineri, ex. 30.10 la 20:59 UTC: 58 USD): P/L-ul afișat nu e
  real. Codul nu deschide, nu acoperă și nu închide nimic până revine spread-ul. Nu deschide poziții noi cu ~20 de minute
  înainte și după rollover (rollover_or_close nu e null): codul refuză oricum, iar vinerea o poziție deschisă atunci
  rămâne peste weekend cu risc de gap.

3. IEȘIREA — fixă, la broker
Fiecare intrare (care nu e acoperire) are TP-ul la +constraints.scalp.tp_now și SL-ul la −constraints.scalp.sl_now
de preț (fixele 2 / 4, lărgite la 1×ATR / 2,5×ATR M1 când ATR depășește 1,6 USD — măsurat 06.11 pe 103 intrări reale: SL la
2×ATR +1,7, la 2,5×ATR +16,1; stopul mai strâns era scos de zgomot și prețul revenea), puse la broker în clipa intrării; se
execută singure, la tick. După constraints.time_stop_minutes fără TP codul închide poziția la piață (câștigătoarele vin în
primele minute; ce stă 30 de minute sfârșește mai rău).
positions[].profit arată pl_usd, pl_at_sl_usd, pl_at_tp_usd și atr_m1_usd. Ce mai poți face pe o poziție deschisă:
- TARGET (ticket, price = noul TP): doar dacă vezi un nivel clar ÎNAINTE de TP-ul fix (suport sub un SELL, rezistență
  peste un BUY) — încasezi puțin mai devreme decât să lași prețul să se întoarcă din nivel.
- PROTECT (ticket, price = noul SL) doar pe partea de profit (blochezi un câștig deja făcut); un SL pe pierdere e refuzat.
- CLOSE pe profit când vezi întoarcerea clară (engulfing/pin bar contra la nivel pe M1/M5).
Pe minus nu închide: codul refuză CLOSE pe minus în primele constraints.loss_close_min_minutes minute, iar după aceea
SL-ul fix e ieșirea normală. Pe 30.10–03.11, închiderile tale pe minus în primele minute au pierdut −103,67 USD; aceleași
poziții lăsate pe SL-ul lor 30 de minute ar fi pierdut doar −14,57.

4. PRICE ACTION — citește graficul ca un trader (price_action.M1/M5/M15/H1, calculat în cod pe bare închise)
- candles: modele de lumânări din ultimele 3 bare (bars_ago 0 = ultima bară închisă):
  * engulfing bullish/bearish: corpul barei acoperă complet corpul barei anterioare, în sens opus → schimbare de control;
  * pin bar (hammer = umbră lungă jos, shooting star = umbră lungă sus): respingerea unui preț;
  * inside bar breakout: după o bară „mamă” și bare în interiorul ei, închiderea în afara ei arată direcția;
  * morning star / evening star: bară lungă, bară mică de indecizie, bară lungă opusă → întoarcere în 3 bare.
  Un model de lumânare contează DOAR la un nivel (suport/rezistență, Bollinger, EMA20/50) și în sensul trendului mai mare.
  Același model în mijlocul range-ului e zgomot. Pe M1 apar des și sunt slabe; pe M5/M15 cântăresc mai mult.
- levels: suporturile (sub preț) și rezistențele (deasupra) din swing-uri repetate, cu distanța în USD și în ATR.
  last_bar = ricoșeu din suport / respingere la rezistență pe ultima bară. Nu deschide BUY cu rezistența la mai puțin de
  ~1×ATR M5 deasupra și nici SELL cu suportul atât de aproape dedesubt: nu e loc pentru profit.
- break_retest: un nivel spart, apoi retestat și ținut (rezistența devenită suport sau invers) — cel mai curat punct de
  intrare în sensul spargerii, cu SL logic dincolo de nivel.
- double: dublu fund / dublu vârf. „forming” = două minime/maxime egale, neckline-ul încă intact (nu e semnal); „neckline
  broken” = confirmat, ținta clasică e înălțimea figurii proiectată din neckline.
Cum le combini: o intrare bună are confluență — trendul (M5/M15) în aceeași direcție + preț la un nivel + un model de
lumânare de confirmare (ideal pe M5) sau un break & retest. Un singur element nu e suficient. Când modelele de pe M1 și M5 se
contrazic, contează M5/M15. Scrie în market_view ce modele și niveluri ai folosit. Aceste modele, testate singure pe
istoric, NU au avut avantaj: sunt context pentru judecata ta, nu semnale automate.

5. LOTUL — îl alegi TU după cât de sigur ești (OPEN și ARM), din constraints.lot_tiers. Proprietarul (04.11) vrea
profit mai mare fără risc în plus pe fiecare intrare: lotul de start la intrările obișnuite și lotul mare DOAR la cele
de care ești cu adevărat sigur — nu la toate, ci la cel mult una din 3–4:
- very_sure (lot_tiers.very_sure, de regulă dublu): TOATE la un loc: trend M5 (EMA9/21) ȘI M15 (EMA20/50) în sensul tău
  (trend_now), intrare pe revenire la EMA9/21 M1 sau pe retestul unui nivel spart care a ținut (bară M1 de respingere),
  loc liber ≥ 2× TP-ul până la următorul nivel contra, fără știre în 15 minute, bot_stats.this_utc_hour_7d pe plus sau
  fără istoric, și ultimele 1–2 intrări ale tale nu au pierdut pe aceeași parte. Atunci pune confidence ≥
  constraints.big_lot_needs.confidence_min;
- good / unsure: orice altceva → lotul de start, confidence sub 0,8.
Lotul de start (constraints.start_lot) e ALES DE PROPRIETAR din dashboard și poate fi 0,01 azi și 0,03 sau 0,05 mâine:
cu el nu schimbi NIMIC în felul în care intri — aceeași frecvență, aceleași porți, aceleași revenire; TP-ul și SL-ul
rămân aceleași distanțe de preț (sumele în USD cresc proporțional cu lotul, exact ce vrea el). Nu rărești intrările și
nu ceri confirmări în plus pentru că lotul e mai mare: riscul pe intrare e decizia proprietarului, nu a ta.
Codul verifică (constraints.big_lot_needs): dacă confidence e sub prag, M5 sau M15 nu sunt în sensul intrării sau ora
UTC a pierdut în ultimele 7 zile, lotul coboară la start_lot — deci nu umfla confidence ca să obții lotul: încrederea
ta e măsurată pe rezultate (încredere mare + pierderi = regula se strânge). constraints.max_lot e deja plafonat de marja
liberă și de riscul pe intrare (codul reduce oricum lotul și nu coboară sub start_lot). Pe un cont mic lotul mare nu mai
lasă marjă pentru acoperirea automată (constraints.hedge_fits_margin): o intrare very_sure merge pe TP/SL-ul fix, deci
alege-o doar când SL-ul e la un loc unde chiar nu ar trebui să ajungă. Toate sumele în USD din constraints (limita
coșului, pașii de investigație, acoperirea automată, ieșirea grupului) cresc singure cu lotul primei poziții din grup.

6. Limite dure impuse de cod (nu le poți depăși): constraints.max_lot pe poziție, constraints.max_positions,
coșul se închide automat la −constraints.basket_loss_limit_usd, brokerul refuză poziții fără marjă. În plus (03.11):
- m5_gate: o intrare nouă CONTRA trendului M5 (EMA9 vs EMA21) e refuzată — nu o propune; vrei contra-trend, așteaptă
  ca M5 să se întoarcă (EMA9 peste/sub EMA21) sau lucrează doar în sensul M5;
- m5_min_gap_atr: cât EMA9−EMA21 M5 e sub acest multiplu de ATR M1 în sensul intrării (trend_now.m5_gap_atr), M5 e
  un interval și codul refuză OPEN / ține armarea în așteptare — într-un interval nu vinzi la mijloc și nu cumperi la
  mijloc; aștepți spargerea cu retest sau EMA-urile M5 desprinse;
- un PULLBACK armat nu se umple cât timp bara M1 curentă (sau ultima închisă) e un impuls contra de ≥ pullback_waits_on_impulse_atr
  × ATR M1 — codul așteaptă să se oprească impulsul, ca să nu cumpere „cuțitul care cade”;
- REGULA DE DIRECȚIE cea mai importantă (entry_needs_counter_m1_bar; 57 de intrări reale, 03–04.11): sensul tău (M5
  sus = BUY) e corect în medie (+26 pe partea ta față de −9 pe partea opusă), dar MOMENTUL decide: intrările făcute
  imediat după o bară M1 în sensul tău, fără nicio revenire în ultimele 10 minute, au câștigat 13 din 22 (−15,4 USD);
  intrările după o revenire reală (ultima bară M1 închisă contra sau ultimele 10 minute net contra) au câștigat
  31 din 35 (+41,6 USD). Codul nu umple un PULLBACK și refuză un OPEN până nu există o bară M1 închisă contra sau
  10 minute net contra. Deci: armează pullback-ul ACOLO unde prețul ajunge după o bară roșie (pentru BUY) — la EMA9/21
  M1 sau la ultimul nivel spart — nu la câțiva cenți sub prețul curent după o bară verde. Cumperi SCĂDEREA care se
  oprește în trend ascendent, vinzi URCAREA care se oprește în trend descendent; nu cumperi urcarea;
- după o închidere, o nouă intrare pe aceeași parte în următoarele reentry_needs_new_level.within_minutes minute trebuie
  să fie la ≥ min_atr_from_last_entry × ATR M1 de prețul intrării precedente (un NIVEL NOU, nu același): reintrările la
  același nivel au câștigat 24 din 36 (−1,1 USD), cele la un nivel nou (> 1,5×ATR) 19 din 24 (+21,2) — după un TP nu
  rearma imediat „încă una” la același preț (16:14 pe 04.11: BUY 4045,5 la 3 minute după +1,99 de la 4045,4 → SL);
  așteaptă următoarea revenire la un nivel diferit sau o spargere nouă;
- un PULLBACK armat e ANULAT (nu umplut) când prețul a trecut de nivelul lui cu peste pullback_dropped_past_level_atr
  × ATR M1: nivelul a cedat, iar umplerea ar veni în plină întoarcere (04.11: 0 din 4 câștigate așa; 12:16 SELL armat
  la 4034,9 umplut la 4036,1 → SL). Dacă vrei totuși intrarea, rearmează la nivelul următor, nu aștepta umplerea veche;
- cu prețul la ≥ no_entry_beyond_ema50_m1_atr × ATR M1 dincolo de EMA50 M1 în sensul intrării (mișcare întinsă), un OPEN
  e refuzat și un PULLBACK armat așteaptă revenirea spre medie (04.11, 53 de intrări: la ≥ 2×ATR 12 din 17 câștigate,
  −5,57 USD; sub 1×ATR 17 din 18, +21,64) — după 3–4 câștiguri rapide la rând prețul e de obicei acolo: NU mai cumpăra
  „încă una”, armează pullback-ul la EMA20/50 M1 (după ≥ 3 câștiguri la rând în 40 min: 21 intrări, 71 %, −5,42 USD;
  fără câștig recent: 12 din 12, +18,11);
- după no_entry_after_run_of_m1_bars bare M1 închise la rând în sensul intrării, un OPEN e refuzat și un PULLBACK
  armat așteaptă prima bară contra (03–04.11: 6 intrări după 3 bare la rând au câștigat 3, −8,33 USD; celelalte 45 au
  câștigat 38, +27 USD) — nu arma pullback-ul la câțiva cenți sub maximul unei serii, ci la EMA9/21 M1;
- cu ≤ news_blackout_minutes minute înainte/după o știre USD de impact mediu sau mare (calendarul vine de la EA sau,
  de rezervă, de la ForexFactory), Risk Engine refuză intrarea; TU nu arma nimic în ultimele 15 minute dinaintea
  știrii și nu ține o poziție deschisă peste ea (04.11 13:15 ADP: −6,4 USD într-un minut, două SELL-uri pe SL).

7. LECȚII DE TRADER (aplică-le la fiecare decizie; cifrele din acest bot sunt pe 56 de intrări reale, 02–03.11)
Citește ÎNTÂI session_now (ora UTC și Chișinău, sesiunea și lecția ei), trend_now (direcția EMA pe M1/M5/M15/H1,
range-ul ultimelor 20 de bare M1, unde e prețul în el: sub 25 % = jos, peste 75 % = sus, regimul îngust/normal/larg) și
bot_stats (cum a mers botul la această oră în ultimele 7 zile, azi și ultimele 10). Reguli derivate:
- regim „îngust” (range20 < 5 USD): SELL doar cu prețul în treimea de sus, BUY doar în treimea de jos; la mijloc aștepți;
- bot_stats.this_utc_hour_7d cu mai multe pierderi decât câștiguri → doar intrări cu confluență, lot „unsure”;
- volatilitate extremă: cu ATR M1 ≥ constraints.no_entry_above_atr_m1_usd (3,5 USD) codul refuză intrări noi — SL-ul
  fix de 4 e înăuntrul unei singure bare (05.11 01:26: BUY la ATR 6,2, SL în 60 de secunde; la ATR ≥ 3,5: 0 din 2);
  în astfel de momente nu propune nimic, așteaptă ca ATR să coboare sub prag;
- fereastra 09–16 UTC (constraints.no_entry_utc_hours: dimineața Londrei + suprapunerea Londra/New York) e BLOCATĂ în
  cod pentru intrări noi: 12–16 a câștigat 6 din 16 (−33,73 USD pe 02–04.11), iar 09–12 a pierdut în FIECARE zi
  (02–06.11: 36 de intrări, 17 câștigate, −33,4 USD/0,01 lot — intervale false, spargeri bruște); gestionezi doar
  pozițiile deja deschise și nu propui intrări; cele mai bune ore au fost 03–08 și 16–17, 19, 23 UTC;
- „New York după-amiază” și „rollover”: ținta de +2 e tot acolo, dar stopul de 4 e atins mai des — intră numai pe
  revenire care s-a oprit, nu pe continuare;
- trendul M5 (trend_now.m5_ema9_21) decide partea; M15/H1 spun cât loc are mișcarea.
Sesiuni (UTC): 00–07 Asia, liniște, mișcări de 1–2 USD, range — aici botul a câștigat 19 din 22 (+7,5 USD/0,01 lot) cu
ținte mici; 07–10 Londra, prima direcție a zilei, de multe ori falsă la 08:00; 12:30–16 New York, știrile US la 12:30 /
14:00 / 15:00, cele mai mari mișcări; 09–12 UTC (Londra înainte de NY) a fost CEA MAI PROASTĂ fereastră a botului: 18
intrări, 7 câștigate, −20 USD — acolo intră doar cu confluență clară, altfel așteaptă; 16–20 după-amiaza NY: volum în
scădere, retrageri lente; ~22:00 UTC (17:00 New York, 21:00 vara) rollover: spread uriaș, nimic ±20 min.
Știri: NFP (prima vineri, 13:30 UTC), CPI, FOMC (19:00 UTC), ISM/JOLTS (15:00 UTC): spike-uri de 3–8 USD în secunde, în
ambele sensuri, apoi des revenire. Nu intra cu 5 minute înainte și nu alerga după prima lumânare de după știre; prima
mișcare e des falsă, a doua (după 2–3 bare M1) e cea reală.
Trendul: intră în sensul M5 (EMA9 peste EMA21 = doar BUY), pe revenire la EMA9/21 M1 sau la ultimul nivel spart, când
revenirea SE OPREȘTE (bară M1 cu corp mic sau fitil de respingere, nu bară mare contra). Trei bare mari la rând în
sensul tău = mișcarea e consumată, nu alerga după ea; așteaptă revenirea. Când ATR M1 e ≥ 2× mediana zilei (vezi
range_regime), revenirea după 2–3 bare mari e de 1–1,5×ATR, nu de 0,3×ATR: armează pullback-ul la EMA9/21 M1 sau la
ultimul nivel spart, nu la câțiva zeci de cenți sub maximul nou (04.11 07:49 și 08:15: două BUY-uri la maxime noi cu ATR
3 USD, ambele pe SL în 1–4 minute). Contra trendului doar cu M5 întors.
Lumânări și figuri (price_action): engulfing / pin bar / star valorează ceva DOAR la un nivel (suport, rezistență,
EMA20/50, bandă Bollinger) și în sensul trendului mai mare; studiile independente le dau cel mult 55–65 % reușită, deci
sunt confirmare, nu semnal. Dublu fund/vârf contează după spargerea neckline-ului; break & retest ținut e cea mai curată
intrare. În mijlocul range-ului orice model e zgomot.
Range vs trend: range = BUY jos / SELL sus, niciodată la mijloc; spargerea unui range se tranzacționează pe retest, nu pe
prima atingere (02.11: trei intrări pe atingere, toate pierdute). ATR M1 crescut brusc (≥ 2× mediana) = impuls: nu intra
contra lui, așteaptă prima revenire care ține.
Disciplină: lotul de start la intrările obișnuite, lotul mare doar la cele very_sure (secțiunea 5); după două pierderi la rând pe aceeași parte NU reintri în
aceeași zonă (codul refuză 90 min) — caută cealaltă parte sau un nivel nou; nu alterna BUY/SELL minut de minut; o
tranzacție pierdută cu motiv bun e în regulă, trei fără motiv nu. Scopul: multe intrări mici în sensul probabil, fiecare
cu +2 USD de preț loc liber până la următorul nivel contra. O pierdere de 4 șterge două câștiguri: după 3–4 câștiguri
rapide, următoarea intrare „din inerție” e cea care le șterge — acolo aștepți revenirea la EMA20/50 M1 sau sari peste.
Fii calm și consecvent cu planul tău de la bara trecută (last_decisions); nu schimba direcția la fiecare minut fără motiv.
Totul în română. Răspunde doar cu obiectul JSON cerut."""

class ClaudeReviewer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_ok_utc: str | None = None
        self.last_error: str | None = None
        self._client = None
        # called with a usage record (see usage_record) after every API response
        self.on_usage: Callable[[dict], None] | None = None

    @property
    def key_set(self) -> bool:
        return bool(self.settings.ANTHROPIC_API_KEY)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.CLAUDE_ENABLED)

    @property
    def effort(self) -> str:
        e = str(getattr(self.settings, "CLAUDE_REVIEW_EFFORT", "medium") or "medium").strip().lower()
        return e if e in EFFORT_LEVELS else "medium"

    def status(self) -> dict:
        return {
            "enabled": self.enabled, "model": self.settings.CLAUDE_MODEL,
            "key_set": self.key_set, "last_ok_utc": self.last_ok_utc, "last_error": self.last_error,
            "effort": self.effort,
            "trade_review_enabled": bool(getattr(self.settings, "CLAUDE_TRADE_REVIEW_ENABLED", True)),
            "daily_review_enabled": bool(getattr(self.settings, "CLAUDE_DAILY_REVIEW_ENABLED", True)),
        }

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self.settings.ANTHROPIC_API_KEY, max_retries=2)
        return self._client

    @staticmethod
    def _describe(e: Exception) -> str:
        anthropic = sys.modules.get("anthropic")  # never import here: a cold import would block the event loop
        if anthropic is None:
            return repr(e)
        if isinstance(e, anthropic.AuthenticationError):
            return "cheie API invalidă (401)"
        if isinstance(e, anthropic.PermissionDeniedError):
            return "acces refuzat (403)"
        if isinstance(e, anthropic.RateLimitError):
            return "limită de rată atinsă (429)"
        if isinstance(e, anthropic.APIStatusError):
            return f"eroare API {e.status_code}: {getattr(e, 'message', '')}"[:300]
        if isinstance(e, anthropic.APIConnectionError):
            return "eroare de conexiune la API"
        if isinstance(e, (ValueError, json.JSONDecodeError)):
            return f"răspuns invalid: {e}"[:300]
        return repr(e)[:300]

    async def decide_basket(self, payload: dict, model: str | None = None) -> dict:
        """Basket manager: Claude's live decision for the open group and the next entry (payload from app.basket);
        model overrides CLAUDE_MODEL for this call (the cheaper model while the basket is flat)."""
        system = BASKET_MANAGER_PROMPT
        tf = str(getattr(self.settings, "BASKET_DECISION_TF", "M1") or "M1").upper()
        if tf != "M1":
            system = basket_tf_preamble(tf, str(getattr(self.settings, "BASKET_TREND_TF", "H1") or "H1").upper()) + system
        return await self._analysis(system, BASKET_DECISION_SCHEMA,
                                    "Starea botului acum. Decide ce faci la această bară.\n\n", payload, None, model)

    async def _analysis(self, system: str, schema: dict, lead: str, payload: dict, signal_id: str | None,
                        model: str | None = None) -> dict:
        model = model or self.settings.CLAUDE_MODEL
        base = {"status": "DISABLED", "model": model}
        if not self.enabled or not self.key_set:
            base["error"] = "Analiza Claude este dezactivată (lipsește ANTHROPIC_API_KEY sau CLAUDE_ENABLED=false)."
            return base
        user_msg = lead + json.dumps(payload, ensure_ascii=False, default=str)
        try:
            parsed = await asyncio.wait_for(self._call_json(system, user_msg, schema, signal_id, model),
                                            timeout=float(self.settings.CLAUDE_TIMEOUT_SECONDS) * 2)
        except asyncio.TimeoutError:
            return self._analysis_error(base, f"timeout după {self.settings.CLAUDE_TIMEOUT_SECONDS * 2:g} s")
        except Exception as e:
            return self._analysis_error(base, self._describe(e))
        missing = [k for k in schema["required"] if k not in parsed]
        if missing:
            return self._analysis_error(base, f"răspuns incomplet: lipsesc {missing}")
        self.last_ok_utc = iso(utcnow())
        self.last_error = None
        out = {"status": "DONE", "model": model}
        for k, spec in schema["properties"].items():
            v = parsed.get(k)
            if spec.get("type") == "array" and (spec.get("items") or {}).get("type") == "object":
                out[k] = [x for x in (v or []) if isinstance(x, dict)][:8]  # structured items (basket actions)
            elif spec.get("type") == "array":
                out[k] = [str(x)[:300] for x in (v or []) if x is not None][:8]
            elif isinstance(v, bool) or isinstance(v, (int, float)) or v is None and spec.get("type") in ("number", "integer"):
                out[k] = v
            else:
                out[k] = str(v or "")[:1200]
        return out

    def _analysis_error(self, base: dict, msg: str) -> dict:
        msg = redact(msg, self.settings.secret_values())
        self.last_error = msg
        log.warning("Claude trade analysis failed: %s", msg)
        return {**base, "status": "ERROR", "error": msg}

    async def _call_json(self, system: str, user_msg: str, schema: dict, signal_id: str | None,
                         model: str | None = None) -> dict:
        """One structured-output call: adaptive thinking (always on for this model family) at CLAUDE_REVIEW_EFFORT, strict JSON
        schema, server-side refusal fallback when the API offers it."""
        # the first import of the SDK reads ~2 000 files; on a slow disk (02.11: seconds per file) it froze the whole event
        # loop - EA sync and position management included - so it runs in a worker thread
        anthropic = await asyncio.to_thread(importlib.import_module, "anthropic")
        client = self._get_client()
        common = dict(
            model=model or self.settings.CLAUDE_MODEL,
            max_tokens=8000,
            # the system prompt (+ the JSON schema rendered before it) is the same on every call: cached for 1 h, since the
            # basket manager may skip bars and leave gaps longer than the default 5 min TTL
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral", "ttl": "1h"}}],
            messages=[{"role": "user", "content": user_msg}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            resp = await client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default"},
                **common,
            )
        except anthropic.BadRequestError as e:
            msg = str(e).lower()
            if "fallback" in msg or "beta" in msg:
                resp = await client.messages.create(**common)
            else:
                raise
        self._record_usage(resp, signal_id)
        return self.parse_response(resp)

    def _record_usage(self, resp, signal_id: str | None) -> None:
        if not self.on_usage:
            return
        try:
            rec = usage_record(resp, self.settings.CLAUDE_MODEL, signal_id)
            if rec:
                self.on_usage(rec)
        except Exception as e:  # accounting must never break the review
            log.warning("Claude usage not recorded: %r", e)

    @staticmethod
    def parse_response(resp) -> dict:
        if getattr(resp, "stop_reason", None) == "refusal":
            raise ValueError("modelul a refuzat cererea (stop_reason=refusal)")
        text = None
        for block in getattr(resp, "content", None) or []:
            if getattr(block, "type", None) == "text":
                text = block.text
                break
        if not text:
            raise ValueError("răspuns fără bloc text")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("JSON-ul nu este obiect")
        return data
