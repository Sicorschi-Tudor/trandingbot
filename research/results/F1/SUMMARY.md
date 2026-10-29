# Etapa F — Familia F1: re-testarea pe eșantion mare a pluginurilor intraday existente

**Verdict: REJECTED (Poarta 1 — VALID).** Niciuna dintre cele 24 de configurații încercate pe TRAIN (5,6 ani × 12 instrumente, costuri STANDARD) nu are speranță pozitivă; cea mai bună (trend-v1 pe H1/H4, SL = TP = 3×ATR, sesiune 07–17 UTC, ieșire `base` cu time stop 24 h) are pe TRAIN avg R = −0,039 (CI90 [−0,054; −0,024], PF 0,90, n = 8 218) și pe VALID avg R = −0,076 (CI90 [−0,101; −0,053], PF 0,82, n = 3 047), negativ în ambii ani. Pe ECN rezultatul rămâne negativ (VALID −0,042, PF 0,89). Concluzia etapei C se confirmă pe un eșantion de ~45× mai mare: pluginurile intraday trend-v1, meanrev-v1, breakout-v1 și pullback-v1 nu au avantaj după costuri; variația parametrilor schimbă doar *cât* de negativ este rezultatul (în principal prin raportul spread/SL), nu semnul.

Agent: F1 (cercetător cantitativ, sesiune Claude Code). Data: 2026-10-29. Toate rulările cu `research/run_bt.py --workers 1`; perioadele sunt cele fixate de runner (TRAIN 2013-06-01 → 2019-01-01, VALID 2019-01-01 → 2021-01-01). **TEST nu a fost atins.**

## 1. Protocolul urmat

- Protocol pre-înregistrat: `docs/research/STAGE_F_PROTOCOL.md` (citit înainte de orice rulare). Familia F1: trend-v1 (HTF H4 pe H1), meanrev-v1, breakout-v1, pullback-v1 — grilă pe H1, M15 doar pentru finalist.
- Date: `data/research.db` (STANDARD) pentru toată grila; `data/research_ecn.db` (ECN) doar pentru cele mai bune 3 configurații pe TRAIN și pentru configurația aleasă pe VALID. Specificații: `data/research_specs.json`. Cost `stats` (spread modelat per simbol × oră UTC), slippage 1 punct, comision 7 USD/lot dus-întors (valoarea implicită a runner-ului, aplicată pe ambele baze — pe STANDARD se adaugă spread-ului larg, deci STANDARD este, dacă ceva, conservator; vezi §7). Swap: implicit `off` pentru pluginurile intraday (poziții închise în ≤ 24 h, dar vezi §7).
- Regula de selecție (protocol §4): configurația cu cel mai mare avg R pe STANDARD/TRAIN dintre cele cu ≥ 150 tranzacții; **exact 24 de configurații încercate** (limita protocolului). Rulările ECN ale celor 3 cele mai bune configurații și descompunerile pe ani ale configurației alese (§5) nu sunt configurații noi (aceeași configurație, altă bază / alt interval), deci nu intră în numărătoare.
- VALID: **o singură rulare** a configurației alese pe fiecare bază (STANDARD + ECN). Nu a fost nevoie de re-rulare.
- Nicio modificare a fișierelor partajate (`app/backtest.py`, `app/strategy.py`, `app/risk.py`, `app/exits.py`, pluginurile existente, `research/run_bt.py`). F1 nu adaugă pluginuri noi (re-testează pluginurile existente); fișierul de test de fum cerut este `tests/test_stage_f_f1.py` (16 teste: import prin registru, forma ieșirii identică cu trend-v1 pe bare sintetice H1/H4, direcția nu depinde de cotația curentă — fără lookahead prin cotație, perioade fixe și refuzul TEST în runner, rezolvarea harness-ului F1 în backtest, o rulare cap-coadă de 2 luni pe baza de cercetare).

### 1.1 Harness fix pentru toate configurațiile (decizii luate înainte de prima rulare, documentate aici)

Riscul (`--risk-json`) pentru fiecare rulare:

```
{"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2,
 "max_spread_points": 60, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000}
```

- Primele trei chei sunt cele cerute de sarcină (portofoliu intraday, max 4 poziții simultane).
- `max_spread_points` 60 (live: 30): cu 30, GBPJPY (spread modelat 32 pct la ora de bază) și XAUUSD (30 pct) ar fi fost blocate aproape permanent de filtrul de spread, iar protocolul cere 12 instrumente. Filtrul rămâne activ la rollover (21–23 UTC, spread ×1,9–2,6) și filtrul „spread > 20 % din distanța SL” din `_common.finish` / `strategy.analyze` rămâne neschimbat.
- `max_daily_loss_pct` 100 și `max_consecutive_losses` 1000: limitele de cont live (pierdere zilnică 1 % = 4R la 0,25 %/tranzacție; pauză 90 min după 3 pierderi) ar fi trunchiat eșantionul în funcție de ordinea întâmplătoare a pierderilor — același precedent ca în etapa C2 („R măsoară strategia, nu limitele contului”). Celelalte valori live rămân (risc 0,25 %/tranzacție, `max_total_open_risk_pct` 2 % ⇒ cel mult 8 poziții, mai permisiv decât `max_open_positions` 4; cooldown 30 min după pierdere; blackout rollover ±20 min; limita de 5 semnale/simbol/sesiune).

Strategia (`--strategy-json`): `"rescue_enabled": false` în toate rulările — ieșirea RESCUE live este, conform docstring-ului `app/exits.py`, un model valid doar pentru TF ≤ M5 (pe H1 prima bară închisă depășește deja `rescue_minutes` 30 cu o fracție 0/1 sau 1/1). Restul setărilor live rămân: BE la 1R cu `be_lock_r` 0,1, `min_sl_points` 60, `signal_expiry_minutes` 15 (irelevant: umplerea e la deschiderea barei următoare).

Ieșirea de referință: `base` cu `max_hold_hours` 24 (time stop 24 h, precedentul etapei D pentru H1) în locul time stop-ului live de 240 min, construit pentru M5 (pe H1 ar însemna 4 bare). Varianta live 240 min a fost încercată explicit (config #21). Politicile din `app/exits.py` încercate: `base`, `trailing` (1R / 1,5×ATR / 0,5R), `combo[trailing, stall]` cu `stall_minutes` 240 (4 bare H1), toate cu `max_hold_hours` 24.

Sesiunea: `--session-json '{"start":"07:00","end":"17:00","tz":"UTC","days":[MON..FRI]}'` (Londra + New York) pentru trend-v1, meanrev-v1, pullback-v1 (variante cu și fără sesiune). breakout-v1 are propria fereastră (interval asiatic 00–06 UTC, intrare 06–10 UTC), deci filtrul de sesiune nu e natural și nu a fost aplicat.

## 2. Grila pe TRAIN (STANDARD) — toate cele 24 de configurații, în ordinea rulării

Proiectare: (1) coarse — fiecare plugin cu parametrii impliciți (#1–4); (2) câte 2 variante de parametri per plugin (#5–12); (3) sesiunea 07–17 UTC pentru cele trei pluginuri unde e naturală (#13–15); (4) politicile de ieșire `trailing` și `combo` pe cele două cele mai bune strategii (#16–19); (5) rafinare în jurul celei mai bune (#20–23: time stop live 240 min, SL/TP 3×ATR, închidere la finalul sesiunii, SL/TP 2×ATR); (6) finalistul pe M15 (#24). Fiecare rulare: 12 simboluri, 2013-06-01 → 2019-01-01, ~2 000 de zile.

| # | run | strategie | TF/HTF | parametri | sesiune | ieșire | n | avg R | CI90 | PF | max DD (R) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `B01_breakout_def_base24h` | breakout-v1 | H1/H4 | `{}` | fără | `base {"max_hold_hours":24}` | 4847 | -0.081 | [-0.109; -0.053] | 0.849 | 418.1 |
| 2 | `P01_pullback_def_base24h` | pullback-v1 | H1/H4 | `{}` | fără | `base {"max_hold_hours":24}` | 5342 | -0.099 | [-0.124; -0.076] | 0.808 | 536.6 |
| 3 | `M01_meanrev_def_base24h` | meanrev-v1 | H1/H4 | `{}` | fără | `base {"max_hold_hours":24}` | 1540 | -0.160 | [-0.207; -0.110] | 0.712 | 260.2 |
| 4 | `T01_trend_live_base24h` | trend-v1 | H1/H4 | `{}` | fără | `base {"max_hold_hours":24}` | 16290 | -0.125 | [-0.139; -0.111] | 0.772 | 2054.2 |
| 5 | `B02_breakout_slopp_base24h` | breakout-v1 | H1/H4 | `{"sl_mode":"opposite"}` | fără | `base {"max_hold_hours":24}` | 4894 | -0.070 | [-0.093; -0.047] | 0.852 | 363.4 |
| 6 | `B03_breakout_rmax2_base24h` | breakout-v1 | H1/H4 | `{"range_max_atr":2.0}` | fără | `base {"max_hold_hours":24}` | 3601 | -0.118 | [-0.149; -0.086] | 0.788 | 447.5 |
| 7 | `T02_trend_score5_base24h` | trend-v1 | H1/H4 | `{"min_score":5}` | fără | `base {"max_hold_hours":24}` | 9695 | -0.120 | [-0.139; -0.102] | 0.783 | 1173.0 |
| 8 | `T03_trend_sl2.5tp2.5_base24h` | trend-v1 | H1/H4 | `{"atr_sl_mult":2.5,"atr_tp_mult":2.5}` | fără | `base {"max_hold_hours":24}` | 10554 | -0.081 | [-0.096; -0.067] | 0.820 | 879.6 |
| 9 | `P03_pullback_tp1.5_base24h` | pullback-v1 | H1/H4 | `{"tp_r":1.5}` | fără | `base {"max_hold_hours":24}` | 5428 | -0.103 | [-0.126; -0.081] | 0.799 | 569.4 |
| 10 | `P02_pullback_slope0.20_base24h` | pullback-v1 | H1/H4 | `{"strong_slope_atr":0.2}` | fără | `base {"max_hold_hours":24}` | 3367 | -0.101 | [-0.133; -0.069] | 0.805 | 347.4 |
| 11 | `M02_meanrev_bbk2.5_base24h` | meanrev-v1 | H1/H4 | `{"bb_k":2.5}` | fără | `base {"max_hold_hours":24}` | 964 | -0.162 | [-0.223; -0.098] | 0.709 | 166.7 |
| 12 | `M03_meanrev_flat0.10_base24h` | meanrev-v1 | H1/H4 | `{"flat_slope_atr":0.1}` | fără | `base {"max_hold_hours":24}` | 2690 | -0.184 | [-0.220; -0.151] | 0.675 | 507.7 |
| 13 | `P04_pullback_def_sess0717_base24h` | pullback-v1 | H1/H4 | `{}` | 07–17 UTC | `base {"max_hold_hours":24}` | 3852 | -0.095 | [-0.125; -0.065] | 0.816 | 396.0 |
| 14 | `M04_meanrev_def_sess0717_base24h` | meanrev-v1 | H1/H4 | `{}` | 07–17 UTC | `base {"max_hold_hours":24}` | 1082 | -0.174 | [-0.232; -0.117] | 0.691 | 190.2 |
| 15 | `T04_trend_sl2.5tp2.5_sess0717_base24h` | trend-v1 | H1/H4 | `{"atr_sl_mult":2.5,"atr_tp_mult":2.5}` | 07–17 UTC | `base {"max_hold_hours":24}` | 9494 | -0.066 | [-0.081; -0.050] | 0.855 | 650.3 |
| 16 | `B04_breakout_slopp_trail24h` | breakout-v1 | H1/H4 | `{"sl_mode":"opposite"}` | fără | `trailing {"max_hold_hours":24}` | 4896 | -0.070 | [-0.092; -0.048] | 0.853 | 361.7 |
| 17 | `B05_breakout_slopp_combo24h` | breakout-v1 | H1/H4 | `{"sl_mode":"opposite"}` | fără | `combo {"modes":["trailing","stall"],"stall_minutes":240,"max_hold_hours":24}` | 4977 | -0.075 | [-0.094; -0.057] | 0.798 | 378.1 |
| 18 | `T06_trend_sl2.5tp2.5_combo24h` | trend-v1 | H1/H4 | `{"atr_sl_mult":2.5,"atr_tp_mult":2.5}` | fără | `combo {"modes":["trailing","stall"],"stall_minutes":240,"max_hold_hours":24}` | 19196 | -0.084 | [-0.092; -0.075] | 0.723 | 1626.7 |
| 19 | `T05_trend_sl2.5tp2.5_trail24h` | trend-v1 | H1/H4 | `{"atr_sl_mult":2.5,"atr_tp_mult":2.5}` | fără | `trailing {"max_hold_hours":24}` | 10554 | -0.081 | [-0.096; -0.067] | 0.820 | 880.5 |
| 20 | `T08_trend_sl3tp3_sess0717_base24h` | trend-v1 | H1/H4 | `{"atr_sl_mult":3.0,"atr_tp_mult":3.0}` | 07–17 UTC | `base {"max_hold_hours":24}` | 8218 | -0.039 | [-0.054; -0.024] | 0.902 | 345.5 |
| 21 | `T07_trend_sl2.5tp2.5_sess0717_baselive240` | trend-v1 | H1/H4 | `{"atr_sl_mult":2.5,"atr_tp_mult":2.5}` | 07–17 UTC | `base` | 17306 | -0.064 | [-0.072; -0.057] | 0.770 | 1120.9 |
| 22 | `T09_trend_sl2.5tp2.5_sess0717_closeend_base24h` | trend-v1 | H1/H4 | `{"atr_sl_mult":2.5,"atr_tp_mult":2.5,"close_at_session_end":true}` | 07–17 UTC | `base {"max_hold_hours":24}` | 10542 | -0.068 | [-0.080; -0.057] | 0.814 | 737.2 |
| 23 | `T10_trend_sl2tp2_sess0717_base24h` | trend-v1 | H1/H4 | `{"atr_sl_mult":2.0,"atr_tp_mult":2.0}` | 07–17 UTC | `base {"max_hold_hours":24}` | 11616 | -0.079 | [-0.092; -0.064] | 0.841 | 955.2 |
| 24 | `T11_trend_sl3tp3_sess0717_base24h_M15` | trend-v1 | M15/H4 | `{"atr_sl_mult":3.0,"atr_tp_mult":3.0}` | 07–17 UTC | `base {"max_hold_hours":24}` | 16428 | -0.102 | [-0.115; -0.090] | 0.807 | 1696.6 |

**Observații pe grilă**

- Toate cele 24 de configurații au avg R negativ și limita superioară CI90 sub zero (cu excepția #24, tot negativă). Nicio politică de ieșire nu schimbă semnul: `trailing` pe trend-v1 cu TP = SL este inert (TP la 1R coincide cu pragul de pornire a trailing-ului — #19 are exact aceleași 10 554 tranzacții ca #8), iar `combo[trailing, stall]` înrăutățește (stall-ul la 4 bare taie tranzacțiile înainte să ajungă la TP și crește numărul lor).
- Singurul efect sistematic este **raportul cost/SL**: lărgirea SL-ului îmbunătățește monoton avg R la trend-v1 (SL 1,5×ATR −0,125 → 2×ATR −0,079 → 2,5×ATR −0,066 → 3×ATR −0,039, toate cu sesiune) și la breakout-v1 (SL la mijlocul intervalului −0,081 → SL la partea opusă −0,070). Este dilutarea costului fix pe o tranzacție mai lungă (RR 1, time stop 24 h: 37 % din ieșiri sunt TIME la #20), nu un avantaj: PF rămâne < 1 și win rate ≈ 48 % la RR 1.
- Sesiunea 07–17 UTC ajută marginal trend-v1 (−0,081 → −0,066) și pullback-v1 (−0,099 → −0,095), dar strică meanrev-v1 (−0,160 → −0,174).
- meanrev-v1 este cel mai slab (−0,16 … −0,18) și cu cel mai mic eșantion; pullback-v1 ≈ −0,10 indiferent de parametri; breakout-v1 ≈ −0,07 … −0,12.
- M15 (#24, aceeași configurație ca #20) este clar mai slab decât H1 (−0,102 vs −0,039): pe M15 SL = 3×ATR(M15) este mult mai strâns în puncte, deci raportul spread/SL crește.

### 2.1 Cele mai bune 3 configurații pe TRAIN — STANDARD vs ECN

| config | n STD | avg R STD | CI90 STD | PF STD | n ECN | avg R ECN | CI90 ECN | PF ECN |
|---|---|---|---|---|---|---|---|---|
| #20 `T08` trend-v1 H1/H4, SL=TP=3×ATR, sesiune 07–17, base 24 h | 8218 | −0,039 | [−0,054; −0,024] | 0,902 | 8272 | −0,016 | [−0,031; −0,001] | 0,959 |
| #21 `T07` trend-v1 H1/H4, SL=TP=2,5×ATR, sesiune 07–17, base live (time stop 240 min) | 17306 | −0,064 | [−0,072; −0,057] | 0,770 | 17353 | −0,036 | [−0,044; −0,028] | 0,865 |
| #15 `T04` trend-v1 H1/H4, SL=TP=2,5×ATR, sesiune 07–17, base 24 h | 9494 | −0,066 | [−0,081; −0,050] | 0,855 | 9482 | −0,039 | [−0,056; −0,024] | 0,910 |

Nici pe ECN (spread ≈ 1/5 + comision 7 USD/lot) nu există configurație cu avg R > 0 pe TRAIN; cea mai bună ajunge la −0,016 cu limita superioară CI90 la −0,001.

## 3. Configurația aleasă și de ce

Regula protocolului §4 (cel mai mare avg R pe STANDARD/TRAIN, n ≥ 150, ≤ 24 configurații) alege **#20 `T08`**:

```
/tmp/claude-0/venv/bin/python research/run_bt.py --db data/research.db --strategy trend-v1 --tf H1 --htf H4 \
  --strategy-json '{"rescue_enabled": false, "atr_sl_mult": 3.0, "atr_tp_mult": 3.0}' \
  --risk-json '{"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2, "max_spread_points": 60, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000}' \
  --exit base --exit-params '{"max_hold_hours": 24}' \
  --session-json '{"start":"07:00","end":"17:00","tz":"UTC","days":["MON","TUE","WED","THU","FRI"]}' \
  --workers 1 --json-out research/results/F1/<run>.json   (+ --split train | valid)
```

trend-v1 (EMA20/50, RSI, MACD, trend EMA50 pe H4, `min_score` 4 live), SL = TP = 3×ATR(14) pe H1 (RR 1, BE la 1R inert pentru că TP = 1R), intrări doar 07–17 UTC, ieșire `base` cu time stop 24 h. Este cea mai bună **doar relativ**: pe TRAIN are avg R −0,039, CI90 integral sub zero, PF 0,90; pe simboluri, singurul pozitiv este EURUSD (+0,005, PF 1,01), restul între −0,005 (AUDJPY) și −0,166 (EURCHF). A fost aleasă și rulată pe VALID pentru că protocolul cere o rulare VALID a configurației selectate; rezultatul TRAIN deja o respinge în fond.

Ieșiri pe TRAIN (#20): TP 2 529, SL 2 651, TIME 3 029, BE 6, END 3; win rate 48,1 %. 13 910 semnale respinse de `max_open_positions` 4 și 32 030 blocate de limita de expunere pe monedă (portofoliul a fost saturat în permanență — strategia generează mult mai multe semnale decât poate ține).

## 4. VALID (2019-01-01 → 2021-01-01) — o singură rulare per bază

| bază | n | win | avg R | CI90 | PF | max DD (R) | net R | serie max pierderi |
|---|---|---|---|---|---|---|---|---|
| STANDARD (`VALID_T08_STANDARD.json`) | 3047 | 0,461 | **−0,0757** | [−0,1012; −0,0531] | 0,818 | 242,0 | −230,6 | 15 |
| ECN (`VALID_T08_ECN.json`) | 3029 | 0,475 | −0,0424 | [−0,0674; −0,0179] | 0,893 | 139,6 | −128,5 | 16 |

Ieșiri (STANDARD): TP 891, SL 1 033, TIME 1 114, BE 5, END 4. Pe simboluri (STANDARD, avg R / n): GBPUSD +0,009 (374), EURJPY −0,006 (338), XAUUSD −0,018 (353), EURUSD −0,048 (359), AUDJPY −0,062 (176), USDCHF −0,067 (100), USDJPY −0,075 (278), AUDUSD −0,080 (220), GBPJPY −0,103 (306), EURGBP −0,202 (226), USDCAD −0,230 (152), EURCHF −0,246 (165). Un singur simbol marginal pozitiv din 12 (și nu cel pozitiv pe TRAIN).

## 5. Pe ani

`run_bt.py` calculează `by_year` din lista de tranzacții a rezultatului, pe care `app.backtest` o trunchiază la ultimele 300 (`MAX_TRADES_OUT`), așa că valorile `by_year` din JSON-urile principale acoperă doar coada eșantionului și **nu sunt folosite**. Tabelul de mai jos vine din rulări separate ale aceleiași configurații pe fiecare an calendaristic (`--start/--end`, fișiere în `research/results/F1/byyear/`); pozițiile deschise la 31 decembrie sunt închise forțat la sfârșitul intervalului (câteva pe an), de aceea suma pe ani diferă marginal de rularea întreagă. Nu este o optimizare: aceeași configurație, intervalele sunt sub-intervale fixe ale split-urilor.

| an | set | bază | n | avg R | CI90 | PF | net R |
|---|---|---|---|---|---|---|---|
| 2013 (06–12) | TRAIN | STANDARD | 823 | −0,011 | [−0,060; +0,038] | 0,969 | −9,3 |
| 2014 | TRAIN | STANDARD | 1439 | −0,062 | [−0,096; −0,026] | 0,846 | −88,5 |
| 2015 | TRAIN | STANDARD | 1531 | −0,022 | [−0,059; +0,013] | 0,944 | −33,2 |
| 2016 | TRAIN | STANDARD | 1472 | −0,019 | [−0,056; +0,017] | 0,951 | −28,3 |
| 2017 | TRAIN | STANDARD | 1462 | −0,061 | [−0,097; −0,025] | 0,853 | −89,8 |
| 2018 | TRAIN | STANDARD | 1490 | −0,046 | [−0,082; −0,011] | 0,887 | −68,6 |
| **2019** | **VALID** | STANDARD | 1528 | **−0,098** | [−0,130; −0,060] | 0,773 | −149,2 |
| **2020** | **VALID** | STANDARD | 1519 | **−0,053** | [−0,087; −0,017] | 0,867 | −80,7 |
| 2019 | VALID | ECN | 1521 | −0,054 | [−0,088; −0,021] | 0,866 | −82,6 |
| 2020 | VALID | ECN | 1508 | −0,030 | [−0,065; +0,006] | 0,923 | −45,0 |

**0 din 8 ani pozitivi** (0/6 pe TRAIN, 0/2 pe VALID), pe ambele baze. Cel mai bun an (2013, jumătate de an) este −0,011. VALID este mai slab decât TRAIN (−0,076 vs −0,039), semn că și mica îmbunătățire obținută prin lărgirea SL-ului pe TRAIN era parțial potrivire pe eșantion.

## 6. Verdict față de Poarta 1 (protocol §4, STANDARD)

| criteriu | prag | VALID STANDARD | trece? |
|---|---|---|---|
| avg R | > 0 | −0,0757 | **NU** |
| limita inferioară CI90 | > −0,02 | −0,1012 | **NU** |
| PF | > 1,10 | 0,818 | **NU** |
| n (intraday) | ≥ 100 | 3047 | da |
| avg R > 0 în cel puțin un an | — | 2019: −0,098; 2020: −0,053 | **NU** |

**REJECTED.** Pe ECN rezultatul este tot negativ (avg R −0,042, CI90 [−0,067; −0,018], PF 0,89, ambii ani negativi), deci nu este nici `ECN_ONLY`. Nu există nimic de trimis la verificarea adversarială (§5) sau la TEST (Poarta 2); TEST nu a fost rulat.

Interpretare: diferența STANDARD → ECN pe VALID este de +0,033 R/tranzacție, iar diferența TRAIN → VALID pe STANDARD este −0,037. Costul explică ordinea de mărime a rezultatului, dar **nici fără aproape tot spread-ul (ECN) semnalul nu e pozitiv**: pluginurile nu au informație, iar grila nu a făcut decât să mute costul. Rezultatul etapei C (120 de zile, M5/M15) nu era un artefact de eșantion mic.

## 7. Caveat-uri

1. **Spread modelat, nu istoric.** `research.db` nu are spread real; spread-ul per simbol × oră UTC este o aproximare conservatoare pentru un broker retail. Dacă un broker real are spread-uri mai mici pe STANDARD, rezultatul se apropie de cel ECN — tot negativ.
2. **Comision 7 USD/lot pe ambele baze** (runner-ul îl trece implicit): pe STANDARD este o dublă contorizare mică (~0,01–0,02 R/tranzacție la SL de 3×ATR pe H1), în sens conservator. Nu schimbă nicio concluzie (ECN, cu spread la 1/5, rămâne negativ).
3. **Fără calendar economic** (blocările pe știri nu sunt simulate) ⇒ rezultatele intraday sunt, dacă ceva, optimiste.
4. **Swap `off`** pentru pluginurile intraday, dar cu time stop 24 h o parte din tranzacții trec peste rollover-ul de la 17:00 NY (TIME = 37 % din ieșiri la #20). Swap-ul ar adăuga un cost mic (−0,5 pip/zi), tot în direcția unui rezultat mai negativ.
5. **Harness-ul** (max_spread 60, limitele de cont dezactivate, rescue off, time stop 24 h) a fost fixat înainte de prima rulare și e același pentru toate configurațiile; varianta cu time stop live (240 min, #21) e în grilă și e mai slabă. Limitele de cont live ar reduce numărul tranzacțiilor fără a schimba semnul speranței pe tranzacție.
6. **Saturarea portofoliului**: cu 12 simboluri și max 4 poziții, 13 910 semnale (TRAIN) au fost respinse de `max_open_positions`; ordinea de arbitrare (HTF-aligned, apoi raportul spread/SL) e cea live. Altă limită ar alege alte tranzacții, dar din aceeași distribuție negativă (toate cele 12 simboluri sunt ≤ +0,01 pe TRAIN).
7. **`by_year` din runner** este calculat din ultimele 300 de tranzacții (limitare în `app.backtest`, fișier partajat, neatins) — nefolosit; descompunerea pe ani a fost făcută prin rulări pe sub-intervale (§5). Nicio altă abatere de la protocol.
8. **Testare multiplă**: 24 configurații pe TRAIN + 1 VALID per bază; cum nici cea mai bună configurație pe TRAIN nu e pozitivă, nu există risc de selecție optimistă — rezultatul negativ este robust la alegerea configurației.
9. Rulările nu au fost comise în git (conform sarcinii). Timp total de calcul ≈ 70 min (4 procese `--workers 1` în paralel).

## 8. Fișiere

- `research/results/F1/SUMMARY.md` — acest raport.
- `research/results/F1/grid_log.jsonl` — o linie per rulare (argumentele exacte ale runner-ului + linia JSON tipărită), în ordinea rulării: 24 TRAIN STANDARD, 3 TRAIN ECN, 2 VALID.
- `research/results/F1/<run>.json` — rezultatul complet al fiecărei rulări (sumar, pe simbol, pe rezultat, contoare, avertismente); numele rulării = coloana `run` din tabelul §2; `*_ECN.json` pentru cele 3 pe ECN; `VALID_T08_STANDARD.json`, `VALID_T08_ECN.json` pentru VALID.
- `research/results/F1/byyear/*.json` + `log.txt` — descompunerea pe ani (§5).
- `tests/test_stage_f_f1.py` — testul de fum al familiei (16 teste, trec).
