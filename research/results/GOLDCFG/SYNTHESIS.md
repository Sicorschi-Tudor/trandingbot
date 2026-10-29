# SINTEZĂ — ce arată backtest-urile pe aur și ce schimbăm în configurație

Date: cercetare XAUUSD 2012-11..2022-03 (`data/research.db` = model de spread STANDARD, `data/research_ecn.db` = model ECN/raw + comision), split TRAIN 2013-06..2019-01, VALID 2019-01..2021-01 (TEST neatins). În DB există doar M15/M30/H1/H4/D1 — **nu există M5**, deci M15 ține locul lui M5 și e, dacă ceva, mai blând decât M5. Toate comenzile, JSON-urile și listele de tranzacții sunt în `research/results/GOLDCFG/` (`grid_log.jsonl`, `pullback_grid_log.jsonl`, `attrib_grid_log.jsonl`, `exit_grid_log.jsonl`, `*.cmd`). Criteriul pre-înregistrat: **R mediu (expectanță) cu IC90 și n ≥ 300**, nu win rate și nu USD net.

---

## 1. Ce arată cele 4 backtest-uri ale tale pe 90 de zile (M5/H1, SL 1.5×ATR, TP 2.5×ATR, în sesiune)

| Strategie (dashboard) | n | R mediu | IC90 | Net USD | Ce înseamnă |
|---|---|---|---|---|---|
| trend-v1 · scor ≥4 | 1137 | **−0,10R** | [−0,15; −0,05] | −97,60 | IC90 complet sub zero: pierdere sigură, nu zgomot |
| pullback-v1 · scor ≥3 | 265 | −0,11R | [−0,23; 0,00] | −27,16 | negativ; n < 300 |
| breakout-v1 · scor ≥3 | 23 | −0,18R | [−0,48; +0,13] | −4,83 | 23 tranzacții = zero informație (IC ±0,3R) |
| meanrev-v1 · scor ≥3 | 66 | +0,06R | [−0,21; +0,34] | +6,81 | n 66, IC uriaș; pe 9 ani (G: n 150) meanrev H1 e −0,088R → nu e un plus real |

**De ce M5 este cea mai proastă configurație — costul pe R.** Cu SL = 1,5×ATR pe un timeframe rapid, stopul e mic în USD, iar spread-ul pe aur (0,27–0,45 USD, în model ~31 puncte) rămâne același. Măsurat pe profilul tău exact (T01, M15/H1, `min_sl_points 150`, SL 1.5, TP 2.5, hold 240, rescue on, spread max 60):

- SL mediu = **222 puncte (2,22 USD)**, spread mediu 31,5 puncte ⇒ **spread-ul singur costă 0,150R pe tranzacție**, comision + slippage încă 0,034R ⇒ **≈0,18R cost total**.
- Rezultat măsurat **−0,1835R** (n 6623, IC90 [−0,20; −0,16]); **contrafactual cu cost zero: +0,0005R**. Adică intrarea trend-v1 nu are niciun avantaj, iar toată pierderea e cost de tranzacționare.
- Fiecare an 2013–2018 e −0,17..−0,20R; **fiecare oră din zi** și **fiecare zi a săptămânii** e negativă (−0,09..−0,37). Nu există „ora bună”, există doar taxa.
- Singura variabilă monotonă din atribuire (A01) e **spread/SL**: <0,05 → +0,12R (n 14); 0,08–0,10 → −0,10R; 0,12–0,20 → −0,20R; >0,20 → −0,29R. 74% din tranzacțiile pe M15 plătesc ≥12% din SL în spread.
- Pe **M5 live** SL-ul e și mai strâns (1,5×ATR(M5) lovește des pragul de 150 puncte), deci spread/SL e 0,20–0,40R. Cele −0,10R ale tale pe 1137 tranzacții sunt exact acest mecanism (demo-ul are spread mai mic decât modelul, de aceea pierzi „doar” 0,10 și nu 0,18).
- Scara costului: M15 SL1.5 → −0,18R (cost 0,18) · M15 SL3 → −0,06..−0,09R (cost 0,09) · H1 SL1.5 → −0,03..−0,045R (cost ~0,08) · H1 SL3 → −0,002..−0,015R (cost 0,046) · H1 SL3 pe ECN → +0,019R (cost 0,024). Avantajul brut al intrării rămâne ~0..+0,04R peste tot; **rezultat = avantaj brut (~0,04) − cost**. Nimic altceva din grilă nu mișcă avantajul brut.
- Confirmare VALID (2019–2020, out-of-sample): profilul live pe M15/H1 dă **−0,194R** (n 2289, IC90 [−0,225; −0,162]) pe STANDARD și −0,111R pe ECN. Ambii ani negativi.

Concluzie pentru secțiunea 1: nu există parametru de oră/zi/lună/volatilitate/putere HTF care să facă trend-v1 pozitiv pe M5 sau M15 pe aur. Singura pârghie e **mai puține tranzacții cu stop mai larg pe timeframe mai lent** (dilurează costul), iar asta duce la ~0, nu la plus.

---

## 2. Cele mai bune 8 configurații pe TRAIN (n ≥ 300) și cele mai proaste 3

Toate pe `--split train`, risk-json „curat” (1 poziție, 20 tranz./zi, fără cooldown/limite). Sortate după R mediu; cost STANDARD dacă nu scrie ECN.

| # | ID | Strategie / TF | Parametri | Sesiune | n | R mediu | IC90 | PF | Tranz./săpt. | Observație |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | B04 | breakout-v1 H1/H4 | SL opus capăt range + cap 1.5×ATR, TP 2.5×range, hold 24h | fereastră plugin 06–10 UTC | 523 | **+0,100** | [−0,010; +0,209] | 1,20 | 1,8 | 3/6 ani negativi; top 5% = 32% din brut; fără ei −0,10R; **pe VALID −0,059R** |
| 2 | P10 (ECN) | pullback-v1 H1/H4 | tp_r 2.5, sl_max_atr 3.0, rescue off, hold 24h | 08–16 UTC | 310 | +0,088 | [−0,022; +0,215] | 1,20 | 1,1 | doar pe ECN; 2014 = 61% din net; 2 ani negativi |
| 3 | B05 | breakout-v1 H1/H4 | SL opus, TP 2.5, range_max_atr 2.0 | plugin 06–10 | 335 | +0,083 | [−0,020; +0,181] | 1,18 | 1,15 | 3/6 ani negativi; fără top 5% −0,03 |
| 4 | G15/B01 | breakout-v1 H1/H4 | SL opus, TP 2.5, hold 8h (B01) / 24h (G15) | plugin 06–10 | 527 | +0,065 (G15 +0,070) | [−0,005; +0,136] | 1,18 | 1,8 | 5/6 ani pozitivi pe TRAIN, dar **G15 pe VALID +0,009R** (2019 −0,17 / 2020 +0,19 → „long aur 2020”), respins la Gate 1 |
| 5 | B02 | breakout-v1 H1/H4 | SL opus, TP 2.5, doar Londra (intrări 06–09) | plugin | 460 | +0,059 | [−0,027; +0,150] | 1,13 | 1,6 | 4/6; mai slab decât G15 pe toate metricile |
| 6 | P08 | pullback-v1 H1/H4 | tp_r 2.5, sl_max_atr 3.0, hold 24h | 08–16 UTC | 305 | +0,050 | [−0,068; +0,165] | 1,11 | 1,05 | 3/6 ani negativi; 2014 = 91% din net; fără top 5% −22R |
| 7 | P03/P09 | pullback-v1 H1/H4 | tp_r 2.5, hold 24h | 07–17 UTC | 355 | +0,040 | [−0,063; +0,155] | 1,09 | 1,2 | 4/6 pozitivi, dar 2017–18 ≈ +0,02; top 5% = 3,1× net |
| 8 | T12 (ECN) | trend-v1 H1/H4 | SL 3×ATR, TP 4.5×ATR, hold 1440, scor 4, rescue off | 07–17 UTC | 1429 | +0,019 | [−0,020; +0,059] | 1,05 | 4,9 | singurul trend-v1 pozitiv, doar pe ECN; același set pe STANDARD (T05) = **−0,0025R** [−0,043; +0,039], PF 0,99 |

Observație generală: **niciuna** dintre cele 8 nu are IC90 cu limita inferioară peste zero. Singura cu IC90 complet pozitiv este B03 (breakout, `range_max_atr 1.5`: +0,212R, 6/6 ani) dar are n 153 (< 300) și pragul 1,5 e un vârf, nu un platou (1,75 → +0,079; 2,0 → +0,083; 2,5 → +0,042) — rămâne o ipoteză de pre-înregistrat pentru VALID, nu un rezultat.

**Cele mai proaste 3 (n ≥ 300):**

| ID | Configurație | n | R mediu | IC90 | PF | Tranz./săpt. | De ce |
|---|---|---|---|---|---|---|---|
| V_base (VALID) | trend-v1 M15/H1, profilul live exact | 2289 | **−0,194** | [−0,225; −0,162] | 0,60 | ~22 | out-of-sample, −445R în 2 ani; ambii ani negativi |
| A01 / T01 | trend-v1 M15/H1, profilul live exact (TRAIN) | 6682 | −0,186 | [−0,204; −0,166] | 0,61 | 23 | cost 0,18R/tranz., avantaj brut 0; fiecare oră/zi negativă |
| T02 | trend-v1 M15/H1, SL1.5/TP2.5, hold 1440, rescue off | 6322 | −0,180 | [−0,203; −0,158] | 0,70 | 21,7 | hold-ul nu contează pe M15; SL/TP decid în câteva ore |

(Mențiune: X_B04_combo, ieșire `combo [trailing, stall]` pe H1, −0,068R IC90 [−0,129; −0,002] — singura ieșire care face un candidat pozitiv să devină cert negativ.)

Alte lecții din grilă, cu cifre: `min_score 5` strică (T07 −0,061 vs −0,002 la scor 4); time-stop 240 min cu SL 3×ATR pe H1 strică (T08: 81% ieșiri TIME, −0,027, IC90 complet negativ; 1440 e strict mai bun); sesiunea 07–17 UTC bate 12–17 UTC și „fără sesiune” în fiecare pereche (+0,01..+0,02R) pentru că scoate intrările din Asia/seară cu spread modelat 33–78 puncte; 12–17 pierde ora 07 UTC care e cel mai puțin rău bucket (n ~500, +0,04).

---

## 3. Ce ieșire ajută și ce strică

Studiu pe 2 candidați (B04 pe STANDARD, P10 pe ECN), 8 ieșiri fiecare, aceleași intrări. Rezultatul e identic pe ambii: **ieșirea `base` (SL/TP + break-even la 1R + time-stop 24h) este cea mai bună**; orice suprapunere scade R mediu.

| Ieșire | B04 (breakout) R mediu | Δ vs base | P10 (pullback ECN) R mediu | Δ vs base |
|---|---|---|---|---|
| **base** (hold 24h) | **+0,100** | — | **+0,088** | — |
| giveback (arm 1R, 50% retrace) | +0,089 | −0,011 | +0,064 | −0,024 |
| profit_lock 1.0/0.5 | +0,046 | −0,054 | +0,076 | −0,012 |
| profit_lock implicit (0.67/0.33 — valorile din bot) | +0,038 | −0,062 | +0,077 | −0,011 |
| partial (50% la 1R) | +0,051 | −0,048 | +0,073 | −0,015 |
| trailing (1R / 1.5×ATR) | +0,075 | −0,025 | +0,060 | −0,028 |
| stall (60 min / 0.3R) | **−0,050** | −0,150 | +0,029 | −0,059 |
| combo [trailing, stall] | **−0,068** | −0,167 | +0,017 | −0,071 |

Ce strică și de ce:
- **Tot avantajul stă în coada dreaptă** (TP-urile și ieșirile TIME lungi). În B04 top 5% din tranzacții = 33% din profitul brut și 2× netul; fără ele R mediu = −0,10. Orice ieșire care taie câștigătorii devreme (partial, profit-lock, trailing strâns) vinde exact partea care plătește.
- **Capcana win rate-ului**: `partial` ridică win rate 50,1% → 51,2% (P10: 52,9% → 54,5%) și scade drawdown-ul, dar scade expectanța. `profit_lock` implicit are cel mai mare win rate (51,7%) și a doua cea mai mică expectanță dintre variantele pozitive. După criteriul pre-înregistrat ambele sunt **înrăutățiri**.
- **`stall` și `combo` cu valorile implicite sunt scalate pentru M5** (60 min = prima închidere de bară pe H1): închid 54% din tranzacții pe loc, plătind spread-ul degeaba, și întorc semnul (5/6 ani negativi). Nu se folosesc pe H1 fără rescalare.
- `giveback` e singura aproape neutră (−0,011R, în zgomot) și netezește puțin anii (2018 −0,047 vs −0,072, DD −1,7R); nu e motiv de schimbare.
- Ieșirea din profil live (`BREAK_EVEN_AT_R 1.0`, `BE_LOCK_R 0.1`) face parte din `base` și e păstrată. `RESCUE` (REVIEW: schimbă R mediu cu <0,001 pe M15) e irelevant pe timeframe-uri lente; modelul lui e valid doar ≤ M5.

---

## 4. Rezultatul VALID (2019-01..2021-01) și verdictul

Pre-înregistrat: baseline = profilul live (trend-v1 M15/H1), candidat = cea mai bună ieșire a celui mai bun candidat pe TRAIN = B04 cu `base`.

| | STANDARD | ECN |
|---|---|---|
| **Baseline** trend-v1 M15/H1 (V_base) | n 2289, **−0,194R**, IC90 [−0,225; −0,162], PF 0,60, net −445R, DD 451R; 2019 −0,220 / 2020 −0,171 | n 2395, −0,111R, IC90 [−0,141; −0,081], PF 0,73, net −266R; 2019 −0,089 / 2020 −0,133 |
| **Candidat** breakout-v1 H1/H4 B04 (V_cand) | n 193, **−0,059R**, IC90 [−0,209; +0,103], PF 0,89, net −11R, DD 23R; 2019 −0,238 / 2020 +0,115; BUY +0,096 / SELL −0,303; top 5% = 38% din brut, fără ei −0,244 | n 193, −0,020R, IC90 [−0,161; +0,142], PF 0,96, net −4R; 2019 −0,106 / 2020 +0,063 |

Verificarea regulii: **ADOPT** cerea R mediu > 0, IC90 inferior > −0,05 și ani ne-opuși — picat pe toate trei. **REJECT** s-ar aplica doar dacă nu bate baseline-ul — îl bate clar pe ambele modele de cost (+0,135R/tranz. pe STANDARD; limita superioară a IC90 a baseline-ului, −0,162, e sub punctul candidatului). ⇒ **Verdict: ADOPT_AS_LEAST_BAD** — reduce pierderea, nu produce profit.

Nuanțe obligatorii: (a) candidatul e tot negativ în expectanță și are n 193 < 300; (b) aproape toată „îmbunătățirea” vine din a tranzacționa ~12× mai puțin (193 vs 2289 în 2 ani) cu un stop mai larg → cost/SL mai mic; (c) plafonul 1,5×ATR (B04) este **mai slab pe VALID** decât SL-ul necapat la capătul opus al range-ului (G15: +0,009 STD / +0,031 ECN, n 187) — deci dacă se păstrează un breakout, SL-ul G15 e varianta mai bună, deși și G15 a fost deja respins la Gate 1 (IC90 inferior −0,131, PF 1,02); (d) rezultatul 2020 e „long aur în rally”, nu avantaj de breakout.

---

## 5. Schimbările EXACTE recomandate în `PROFILES["gold"]` (`app/config.py`) — și ce NU se schimbă

Profilul se aplică peste `.env` și peste setările salvate din dashboard, deci e locul potrivit. Cheile de mai jos sunt câmpuri existente ale `Settings` (nume identice).

**A. Dacă strategia activă rămâne `trend-v1` (ca acum) — configurația cu cel mai mic cost, așteptare ≈ 0R/tranz., ~5 tranz./săpt. (T05/G02; pe ECN T12 +0,019R):**

```python
"gold": {
    # ... cheile existente rămân ...
    "SIGNAL_TIMEFRAME": "H1",        # era M5 (Settings default). M5/M15 = −0,10..−0,19R, cost 0,18R/tranz.
    "HTF_TIMEFRAME": "H4",           # era H1
    "ATR_SL_MULT": 3.0,              # era 1.5. Singura pârghie care mișcă rezultatul (dilurează spread/SL)
    "ATR_TP_MULT": 4.5,              # era 2.5. 3.0 și 4.5 sunt echivalente (−0,002 vs −0,0025); 4.5 are PF 0,99
    "MAX_HOLD_MINUTES": 1440,        # era 240. Cu SL 3×ATR pe H1, 240 min închide 81% pe TIME și e cert negativ (T08)
    "RESCUE_ENABLED": False,         # modelul RESCUE e valid doar ≤ M5; pe H1 e zgomot
    "SESSION_ENABLED": True,
    "SESSION_START": "09:00",        # = 07:00 UTC iarna (tz = DEFAULT_TIMEZONE Europe/Chisinau)
    "SESSION_END": "19:00",          # = 17:00 UTC iarna. Londra+NY; 12–17 UTC singur e mai slab (pierde ora 07 UTC)
    "SESSION_DAYS": "MON,TUE,WED,THU,FRI",
    "CLOSE_AT_SESSION_END": False,   # ieșirile TIME de 24h sunt partea care câștigă (P07: 8h → −0,05R)
}
```
Notă pe sesiune: cercetarea a folosit 07:00–17:00 **UTC**. Dacă vrei exactitate și vara, pune `"DEFAULT_TIMEZONE": "UTC"` în profil și `SESSION_START "07:00"` / `SESSION_END "17:00"`.

**B. Dacă vrei „cea mai puțin rea” configurație găsită (verdictul VALID) — `breakout-v1` H1/H4, ~1,8 tranz./săpt.:** strategia activă se alege prin `PUT /api/v1/strategy` / pagina *Control & Risc* (nu prin profil); parametrii pluginului sunt în `app/strategies/breakout_v1.py: PARAMS`. Valorile cercetării: `sl_mode "opposite"` (nu `"mid"`, implicitul pluginului), `tp_range_mult 2.5` (nu 1.5), `sl_max_atr 3.0` (**fără** plafonul 1.5 — pe VALID G15 bate B04), `range_max_atr 0` (filtrul 1.5 e doar ipoteză), fereastră 00–06 / intrări până la 10 UTC (implicit), `rescue_enabled false`, hold 24h (`MAX_HOLD_MINUTES 1440`) sau 8h (același R, DD mai mic: 13,6R vs 21,4R). SL/TP din dashboard „1.5×ATR / 2.5×ATR” **nu se aplică** acestui plugin — e forma greșită pentru breakout.

**C. Ce NU trebuie schimbat (dovedit că strică sau că nu face nimic):**
- `MIN_SCORE_COMPONENTS` rămâne **4** (scor 5: T07 −0,061 vs −0,002, n 549).
- `BREAK_EVEN_AT_R 1.0`, `BE_LOCK_R 0.1` — rămân (fac parte din ieșirea `base`, cea mai bună).
- `PROFIT_LOCK_ENABLED` rămâne **False**; valorile 0.67/0.33 sunt a doua cea mai proastă ieșire pozitivă (−0,062R vs base). `TRAILING_ENABLED` rămâne **False** (−0,025..−0,028R).
- Nu activa `stall`/`combo` pe H1 (valorile implicite sunt pentru M5; întorc semnul).
- `MIN_SL_POINTS 150`, `MAX_SPREAD_POINTS 60`, `MAX_VOLUME_LOTS 0.05`, `NEWS_BLACKOUT_MINUTES 45` — rămân; pe H1 pragul de 150 puncte nu mai e atins (0,06%), spread cap 60 nu blochează nimic între 07–17 UTC.
- `MAX_OPEN_POSITIONS 3` / `MAX_POSITIONS_PER_SYMBOL 3` / piramidare: pe o strategie cu expectanță ≤ 0 piramidarea doar multiplică pierderea (docs/PROFIL_AUR.md §piramidare); pentru măsurare pe DEMO folosește 1.
- Nu adăuga filtre de oră exactă (09–12 UTC, A05 +0,011) sau de zi/lună/volatilitate: sunt vârfuri in-sample (o deplasare de 1h costă 0,04R; terțilele ATR sunt în U; Fri/Mon, Jan/Dec au |z| < 2,1). Au nevoie de VALID pre-înregistrat înainte de orice.
- Nu reveni la M5/M15 cu nicio combinație de parametri: cea mai bună fereastră M15 (A04, 13–17 UTC) e tot −0,132R cu IC90 complet negativ.

---

## 6. Ce înseamnă asta pentru profit — o frază onestă

Pe 9 ani de date și pe cele 90 de zile ale tale, nicio configurație testată a celor 4 strategii nu are expectanță pozitivă cu IC90 peste zero după costuri; schimbările de mai sus (H1/H4, SL 3×ATR, hold 24h, sesiune Londra+NY, fără profit-lock/trailing) transformă o pierdere sigură de ≈0,10–0,19R pe tranzacție la 20+ tranzacții/săptămână într-o așteptare de ≈0R la ~5 tranzacții/săptămână — adică **opresc sângerarea, nu promit câștig**, și singurul plus real măsurat (+0,02R/tranz.) apare doar pe un cont cu spread raw/ECN și nu este distinct statistic de zero.
