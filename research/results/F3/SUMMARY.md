# Etapa F — Familia F3: cele 20 de ipoteze de descoperire (disc-H01…H20) pe 5,6 ani × 12 instrumente

Verdict: **TRECE Poarta 1 (VALID, cont STANDARD) — la limită, cu rezerve serioase; NU este încă `PROMISING`** (Poarta 2 / TEST și verificarea adversarială §5 revin coordonatorului). Configurația aleasă mecanic pe TRAIN după regula §4 este **disc-h06 pe H4 (compresie ATR → bară de expansiune), cu filtrele slăbite `c` 0,7 / `range_mult` 1,3 / `close_frac` 0,3** (a 24-a și ultima configurație încercată). Pe VALID (2019-01-01 → 2021-01-01, STANDARD): **n = 149, avg R = +0,134, CI90 [−0,012; +0,278], PF 1,35**, pozitivă în ambii ani (2019 +0,148, 2020 +0,118). Pe ECN: n = 167, +0,106, CI90 [−0,026; +0,234], PF 1,27 — pică **doar** criteriul CI90 inferior > −0,02 (cu 0,006), deci nu e `ECN_ONLY`, ci „STANDARD trece, ECN aproape”.

Celelalte 19 ipoteze: **toate negative sau nule pe TRAIN** (18 negative, 12 cu CI90 integral sub zero; H16 +0,0095 cu CI90 care include zero), confirmând concluzia etapei E pe date de ~10× mai lungi. H14 nu a putut fi evaluată (necesită NZDUSD, absent din baza de cercetare).

Toate rulările: `research/results/F3/*.json` (runner `research/run_bt.py`, perioade fixe). Acest rezumat este scris după rulările VALID; protocolul (`docs/research/STAGE_F_PROTOCOL.md`) nu a fost modificat.

## 1. Protocol urmat (și abaterile, explicit)

- Date: `data/research.db` (STANDARD) pentru toată grila; `data/research_ecn.db` (ECN) pentru cele mai bune 3 configurații pe TRAIN și pentru configurația aleasă pe VALID. 12 instrumente, bare UTC, TRAIN 2013-06-01 → 2019-01-01, VALID 2019-01-01 → 2021-01-01. TEST nu a fost atins (fără `--allow-test`, fără `--start/--end`).
- Runner: `run_bt.py --workers 1`, risc de portofoliu exact cel cerut: `--risk-json '{"max_open_positions": 6, "max_trades_per_day": 30, "max_same_currency_direction": 3}'`; swap = `table` din `RUN_DEFAULTS`-ul fiecărui plugin (toate îl cer); ieșirea = `default_exit()` al pluginului, cu excepțiile de mai jos.
- **Timeframe per ipoteză**: `run_bt.py` trimite întotdeauna `--tf/--htf`, care **suprascriu** `RUN_DEFAULTS` (vezi `app.backtest.resolve`), deci fiecare modul a fost rulat cu TF/HTF-ul din propriul `RUN_DEFAULTS` (tabelul §2; pinat în `tests/test_stage_f_f3.py`).
- **Abatere 1 (D1, documentată, identică cu F2 §1):** pentru cele 5 ipoteze D1 (H03, H07, H10, H12, H13) `max_spread_points` a fost ridicat de la 30 la **100**. Barele D1 se închid la rollover (21–22 UTC), unde spread-ul modelat este ×2,2/×2,6: la 30 de puncte treceau doar EURUSD și USDJPY (F2 a măsurat ~85 % candidați blocați). Spread-ul larg este **plătit integral** la intrare, deci costurile nu sunt mai optimiste. H4/H1 au rămas la 30 (paritate live).
- **Abatere 2 (limitare a runner-ului):** ieșirile „la oră fixă” ale pluginurilor din etapa E (`time_exit` / `exit_deadline` din `_disc0` / `_disc3`) sunt armate doar de scriptul de screening al etapei E, **nu de `app.backtest` / `run_bt.py`**. Nu am modificat fișiere partajate; am folosit căderea implicită a fiecărui plugin sau am emulat termenul cu opțiunile runner-ului:
  - H01 (ieșire 16:00 UTC): cădere `max_hold_minutes` 480 de la intrarea la 08:00 → **echivalent**. H17 (8 bare): cădere 480 min → **echivalent**. H05 (sfârșitul zilei următoare): cădere `max_hold` 2 zile → **aproximativ echivalent**. H09 (închiderea de vineri): cădere `max_hold` 5 zile → **mai lung** (poate ține și în săptămâna următoare).
  - H04 (termen luni 13:00 UTC): `--exit base --exit-params '{"max_hold_hours": 14}'` (intrare duminică 22:00/23:00 UTC → exact în iarnă, cu 1 h mai devreme în vară).
  - H08 (ieșire 20:00 UTC): `--session-json 07:00–20:00 UTC L–V` + `--strategy-json '{"close_at_session_end": true}'` → poziția se închide la deschiderea barei de 20:00 (`SESSION_END`, 2 525 din 9 286 ieșiri). Fereastra de intrare a pluginului (07–17) este inclusă în sesiune.
  - H13 (ieșire după 2 bare D1, altfel **fără nicio ieșire** în afară de SL și TP 50×ATR): `--exit base --exit-params '{"max_hold_days": 2}'` (220 din 233 ieșiri `TIME`).
- **Abatere 3 (date transversale):** H03/H19 citesc panelul USD din `--strategy-json '{"panel_db": "data/research.db"}'` (5 din 6 perechi USD, NZDUSD lipsește; `MIN_PAIRS` = 4 → indicele USD se calculează). H13 citește universul din `xs_db`. **H12** are doar un hook în proces (`_disc3.set_universe`), inaccesibil din runner — rulat prin **pluginul nou `disc-f3-h12`** (`app/strategies/discovery/disc_f3_h12.py`): încarcă universul D1 din `cfg.xs_db` în hook și delegă totul lui `disc_h12` (fără lookahead: `strengths()` păstrează doar barele închise la `ctx.now_ts`). **H14** cere închiderile tuturor celor 6 perechi USD (intersecție) → fără NZDUSD factorul e gol → 0 candidați în 103 871 analize → **neevaluabilă pe aceste date** (rulare păstrată și numărată).
- Selecție (protocol §4): cea mai mare `avg_r` pe STANDARD/TRAIN dintre configurațiile cu **n ≥ 150 (H1) / n ≥ 60 (H4, D1)**. Exact **24 de configurații** pe TRAIN: 20 implicite + 4 vecini ai celei mai bune (H06). Cei 4 vecini au fost **decliși înainte** de a vedea rezultatul lor și înainte de rezultatele H1: fiecare slăbește unul din cele 3 filtre de intrare ale H06 (singura ipoteză pozitivă, dar neeligibilă la n = 43), al 4-lea pe toate trei, cu scopul declarat de a aduce eșantionul la ≥ 60.
- ECN pe TRAIN: cele mai bune 3 eligibile (H06_N4, H06_N1, H16) + H10 (rulat preventiv când era în top 3, înainte de vecini; nu e configurație nouă).
- VALID a fost rulat **o singură dată** (STANDARD + ECN), după închiderea grilei. Fără re-rulări.
- Limitare: `app.backtest` păstrează ultimele 300 de tranzacții (`MAX_TRADES_OUT`), iar `by_year` din runner se calculează din ele → pentru rulările cu n > 300 tabelul pe ani este parțial (inclusiv TRAIN al configurației alese, n = 370: lipsește 2013). Metricile agregate sunt complete; VALID (n = 149/167) e complet.

## 2. Grila pe TRAIN (STANDARD, 2013-06-01 → 2019-01-01) — toate cele 24 de configurații

`sp` = `max_spread_points`. Eligibil = n ≥ 150 (H1) / ≥ 60 (H4, D1).

| # | ID | Plugin | TF/HTF | Parametri / particularități | sp | n | avg R | CI90 | PF | max DD (R) | eligibil |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | H01 | disc-H01 | H1/D1 | implicit; fade Asia la Londra | 30 | 769 | −0,1730 | [−0,234; −0,115] | 0,668 | 135,9 | da |
| 2 | H02 | disc-h02 | H1/D1 | implicit; reversal fix 16:00 Londra | 30 | 5477 | −0,0516 | [−0,063; −0,041] | 0,764 | 286,8 | da |
| 3 | H03 | disc-H03 | D1/NONE | implicit; panel_db | 100 | 39 | −0,0341 | [−0,206; +0,140] | 0,881 | 4,4 | — (n < 60) |
| 4 | H04 | disc-h04 | H1/NONE | implicit; exit base 14 h (emulare) | 30 | 158 | −0,1373 | [−0,242; −0,030] | 0,680 | 23,6 | da |
| 5 | H05 | disc-H05 | H1/D1 | implicit; NR7 inside-day | 30 | 596 | −0,0912 | [−0,152; −0,031] | 0,799 | 58,3 | da |
| 6 | H06 | disc-h06 | H4/NONE | implicit (c 0,6; range_mult 1,5; close_frac 0,2) | 30 | 43 | **+0,2103** | [−0,044; +0,457] | 1,674 | 4,7 | — (n < 60) |
| 7 | H07 | disc-H07 | D1/NONE | implicit; range-expansion day | 100 | 511 | −0,0657 | [−0,125; −0,004] | 0,814 | 40,8 | da |
| 8 | H08 | disc-h08 | H1/D1 | implicit; sesiune 07–20 UTC + close_at_session_end | 30 | 9286 | −0,0954 | [−0,110; −0,080] | 0,773 | 891,3 | da |
| 9 | H09 | disc-H09 | H4/D1 | implicit; sweep PWH/PWL | 30 | 1647 | −0,0717 | [−0,114; −0,028] | 0,861 | 128,9 | da |
| 10 | H10 | disc-h10 | D1/NONE | implicit; RSI(2) + SMA200 | 100 | 731 | −0,0303 | [−0,063; +0,001] | 0,859 | 32,3 | da |
| 11 | H11 | disc-H11 | H4/NONE | implicit; fade N închideri consecutive | 30 | 3055 | −0,0623 | [−0,078; −0,046] | 0,734 | 196,7 | da |
| 12 | H12 | disc-f3-h12 | D1/NONE | implicit; xs_db (univers 12) | 100 | 275 | −0,0452 | [−0,094; +0,002] | 0,788 | 15,0 | da |
| 13 | H13 | disc-H13 | D1/NONE | implicit; xs_db; exit base 2 zile (emulare) | 100 | 233 | −0,0511 | [−0,104; −0,000] | 0,761 | 15,9 | da |
| 14 | H14 | disc-h14 | H4/NONE | implicit; factor_db | 30 | **0** | — | — | — | — | — (neevaluabil: NZDUSD lipsă) |
| 15 | H15 | disc-H15 | H1/D1 | implicit; week squaring vineri | 30 | 1274 | −0,0547 | [−0,082; −0,030] | 0,789 | 79,1 | da |
| 16 | H16 | disc-h16 | H1/D1 | implicit (variant 'eur': SELL EURUSD/EURJPY 07–12 UTC) | 30 | 2890 | +0,0095 | [−0,002; +0,020] | 1,076 | 16,3 | da |
| 17 | H17 | disc-H17 | H1/NONE | implicit; numere rotunde | 30 | 3550 | −0,1746 | [−0,201; −0,146] | 0,687 | 621,8 | da |
| 18 | H18 | disc-h18 | H4/NONE | implicit; fakey inside bar | 30 | 2697 | −0,2033 | [−0,241; −0,162] | 0,693 | 559,6 | da |
| 19 | H19 | disc-H19 | H4/NONE | implicit; panel_db | 30 | 533 | −0,1369 | [−0,238; −0,034] | 0,779 | 78,3 | da |
| 20 | H20 | disc-h20 | H1/NONE | implicit; fade spike | 30 | 1295 | −0,1572 | [−0,191; −0,125] | 0,583 | 208,1 | da |
| 21 | H06_N1 | disc-h06 | H4/NONE | c 0,7 (restul implicit) | 30 | 243 | +0,0105 | [−0,089; +0,112] | 1,027 | 13,2 | da |
| 22 | H06_N2 | disc-h06 | H4/NONE | range_mult 1,3 | 30 | 54 | +0,2369 | [+0,021; +0,468] | 1,742 | 6,6 | — (n < 60) |
| 23 | H06_N3 | disc-h06 | H4/NONE | close_frac 0,3 | 30 | 54 | +0,2304 | [−0,007; +0,471] | 1,663 | 5,3 | — (n < 60) |
| 24 | H06_N4 | disc-h06 | H4/NONE | **c 0,7; range_mult 1,3; close_frac 0,3** | 30 | **370** | **+0,0533** | **[−0,045; +0,147]** | **1,121** | 11,9 | **da — ALEASĂ** |

Fișiere: `train_<ID>.json` (+ `.out`/`.err`), `grid_log.jsonl` (ordinea și timpii rulărilor).

Observații pe TRAIN:
- Dintre cele 17 configurații eligibile, **3 au avg R > 0**: H06_N4 (+0,053), H06_N1 (+0,011), H16 (+0,010) — toate cu CI90 care include zero. Celelalte 14 sunt negative, 12 cu CI90 integral sub zero. Ipotezele intraday clasice (fade Asia, fix de 16:00, gap de luni, NR7, PDH/PDL, numere rotunde, spike-uri) pierd −0,05 … −0,20 R pe tranzacție după costuri STANDARD, pe mii de tranzacții: nu e zgomot, e lipsă de avantaj + cost.
- H06 cu parametri impliciți are +0,21 R pe 43 de tranzacții (PF 1,67), dar profitul vine din 2015–2016 (+15,4 R din +9,0 net); 2013, 2014, 2017, 2018 negative. Slăbirea **unui singur** filtru schimbă radical rezultatul: `c` 0,7 → +0,011 (n 243); `range_mult` 1,3 sau `close_frac` 0,3 → +0,23 dar tot n = 54; toate trei → +0,053 (n 370). Răspuns **non-monoton** la parametri = semn de zgomot/selecție, nu de avantaj robust.
- H16 ('eur'): 2 890 tranzacții = câte una pe zi lucrătoare pe EURUSD și EURJPY, 98,8 % ieșiri pe timp; +0,0095 R/tranzacție e sub o jumătate de spread; ECN: +0,020 [+0,009; +0,030], PF 1,17 — singura configurație cu CI90 strict pozitiv pe ECN/TRAIN, dar efect minuscul (≈ 1 pip/zi) și 2018 negativ (−0,002).
- H08 cu sesiune: 9 286 tranzacții în 5,6 ani (fiecare sweep intraday), −0,095 R; H02: 5 477; H17: 3 550 — eșantioane mari, concluzie fermă negativă.
- D1: toate negative și cu sp 100 (H07 −0,066, H13 −0,051, H12 −0,045, H10 −0,030); H03 doar 39 de tranzacții (≈ 7/an, prin construcție).

### Cele mai bune 3 configurații eligibile pe TRAIN și pe ECN (`research_ecn.db`)

| # | Config | STANDARD n / avg R / CI90 / PF | ECN n / avg R / CI90 / PF |
|---|---|---|---|
| 24 | disc-h06 H4, c 0,7 / rm 1,3 / cf 0,3 | 370 / +0,0533 / [−0,045; +0,147] / 1,121 | 419 / +0,0703 / [−0,019; +0,156] / 1,158 |
| 21 | disc-h06 H4, c 0,7 | 243 / +0,0105 / [−0,089; +0,112] / 1,027 | 274 / +0,0426 / [−0,053; +0,140] / 1,108 |
| 16 | disc-h16 H1, 'eur' | 2890 / +0,0095 / [−0,002; +0,020] / 1,076 | 2890 / +0,0200 / [+0,009; +0,030] / 1,167 |
| (10) | disc-h10 D1 (rulat preventiv pe ECN) | 731 / −0,0303 / [−0,063; +0,001] / 0,859 | 730 / −0,0138 / [−0,043; +0,016] / 0,933 |

Pe ECN H06 are mai multe tranzacții (419 vs 370): filtrul de 30 de puncte bloca 77 de candidați pe STANDARD (bare H4 închise la 21:00 / 01:00 UTC, spread ×2,2 / ×1,4), pe ECN 1.

## 3. Configurația aleasă și DE CE

Regula §4: cea mai mare `avg_r` pe STANDARD/TRAIN dintre configurațiile eligibile → **#24, disc-h06 pe H4**, `--strategy-json '{"c": 0.7, "range_mult": 1.3, "close_frac": 0.3}'`, restul implicit (`fast_n` 5, `slow_n` 100, `comp_look` 5, `comp_min` 3, `sl_min_atr` 1, `sl_max_atr` 2, `tp_r` 2, `exit_variant` fixed → TP 2R + time stop 48 h), risc portofoliu 6 / 30 / 3, spread max 30, swap table. TRAIN: n = 370, avg R +0,0533, CI90 [−0,045; +0,147], PF 1,121, win 46,8 %, max DD 11,9 R, swap −8,1 R; ieșiri TIME 172 / SL 134 / TP 64.

Regulile (din cod, bare închise): compresie = ATR(5)/ATR(100) < 0,7 pe ≥ 3 din cele 5 bare dinaintea barei-trigger; trigger = ultima bară închisă cu range ≥ 1,3 × ATR(100) (ATR-ul barei precedente) și închidere în 30 % superioare (BUY) / inferioare (SELL) ale range-ului; intrare la deschiderea următoare, SL la extrema opusă a barei (între 1 și 2 × ATR100), TP 2R, ieșire pe timp după 48 h. Cotația e folosită doar pentru prețul de intrare și filtrul de spread.

Am ales-o mecanic. A priori era slabă: CI90 include zero, PF 1,12, 2018 negativ (−0,12 pe 39), vecinii „la un pas” (c 0,7 singur) ≈ 0, și e **ultima** din 24 de configurații.

Comanda exactă (fără `--split`):

```
/tmp/claude-0/venv/bin/python research/run_bt.py --db data/research.db --strategy disc-h06 --tf H4 --htf NONE \
  --workers 1 --risk-json '{"max_open_positions": 6, "max_trades_per_day": 30, "max_same_currency_direction": 3}' \
  --strategy-json '{"c": 0.7, "range_mult": 1.3, "close_frac": 0.3}' --json-out research/results/F3/<run>.json
```

## 4. VALID (2019-01-01 → 2021-01-01) — rulat o singură dată

| Cont | n | win | avg R | CI90 | PF | max DD (R) | net R | swap R | ieșiri TIME / SL / TP |
|---|---|---|---|---|---|---|---|---|---|
| **STANDARD** (`valid_H06_N4_all.json`) | **149** | 0,490 | **+0,134** | **[−0,012; +0,278]** | **1,349** | 9,4 | +19,96 | −3,34 | 82 / 44 / 23 |
| ECN (`valid_ecn_H06_N4_all.json`) | 167 | 0,491 | +0,106 | [−0,026; +0,234] | 1,266 | 10,1 | +17,72 | −3,69 | 88 / 54 / 25 |

Pe ani:

| An | STANDARD n | avg R | sumă R | ECN n | avg R | sumă R |
|---|---|---|---|---|---|---|
| 2019 | 79 | +0,148 | +11,70 | 89 | +0,108 | +9,64 |
| 2020 | 70 | +0,118 | +8,26 | 78 | +0,104 | +8,08 |

Pe simbol (STANDARD, avg R / n): AUDJPY +1,96 / 1, GBPJPY +0,67 / 13, USDCAD +0,47 / 11, GBPUSD +0,33 / 21, XAUUSD +0,12 / 23, USDCHF +0,08 / 12, EURJPY +0,06 / 4, EURGBP +0,04 / 14, EURUSD +0,04 / 17, AUDUSD −0,03 / 5, EURCHF −0,19 / 9, **USDJPY −0,32 / 19**. 9 din 12 pozitive; GBPUSD + GBPJPY + USDCAD aduc +20,7 R din +20,0 net (restul portofoliului ≈ 0). USDJPY e negativ și pe VALID și pe ECN, deși pe TRAIN era cel mai bun (+0,23 / 56).

Contoare (STANDARD): 37 308 analize, 228 candidați, 149 semnale umplute; `blocked_market` 26 (spread > 30 la orele de rollover/Asia), blocaje de context 53 (34 poziție existentă pe simbol, 10 blackout rollover, 5 expunere corelată, 4 pauză după pierderi). Nicio poziție închisă forțat la final.

### Poarta 1 (STANDARD)

| Criteriu | Prag | Valoare | Trece? |
|---|---|---|---|
| avg R | > 0 | +0,134 | da |
| CI90 inferior | > −0,02 | −0,012 | **da (la limită: marjă 0,008 R)** |
| PF | > 1,10 | 1,349 | da |
| n | ≥ 40 (D1/H4) | 149 | da |
| avg R > 0 în cel puțin un an | — | 2019 +0,148; 2020 +0,118 | da (ambii) |

**Verdict Poarta 1: TRECE (STANDARD).** Pe ECN: avg R +0,106 > 0, PF 1,27 > 1,10, n 167, ambii ani pozitivi, dar CI90 inferior −0,026 < −0,02 → **nu trece** criteriul CI pe ECN (cu 0,006). Configurația merge mai departe către verificarea adversarială §5 și, dacă supraviețuiește, către TEST (o singură rulare, de către coordonator). **Nu este `PROMISING`** până atunci; nu se activează nimic live.

## 5. Ce înseamnă rezultatul (onest)

- Din 20 de ipoteze de descoperire, 19 nu au avantaj după costuri pe 5,6 ani (sau nu pot fi evaluate). Pattern-ul este identic cu etapele C, C2, E și cu familia F2: regulile simple pe preț, pe FX retail, dau ≈ 0 brut și negativ net.
- Singurul semnal pozitiv (H06, compresie → expansiune pe H4) are o poveste economică plauzibilă (breakout de volatilitate cu follow-through pe 2 zile) și **s-a reprodus out-of-sample cu un rezultat mai bun decât pe TRAIN** (+0,134 vs +0,053), în ambii ani, pe 9 din 12 instrumente, și pe ECN. Asta e mai mult decât a reușit orice altă configurație din etapele C–F.
- Dar: a fost selectată din 24 de încercări, parametrii finali au fost găsiți prin slăbirea filtrelor (pasul de rafinare), răspunsul la parametri e non-monoton, TRAIN 2018 e negativ în toate variantele, iar pe VALID limita inferioară a CI90 e la 0,008 R de prag. Probabilitatea ca un rezultat de acest fel să apară din întâmplare la 24 de încercări nu e mică; exact pentru asta Poarta 2 cere CI90 > 0 pe TEST.

## 6. Caveats (ce ar putea schimba concluzia și ce nu am făcut)

1. **Testare multiplă / selecție**: 24 de configurații pe TRAIN (plus 4 ECN și 2 VALID care nu sunt configurații noi). Configurația aleasă a fost a 24-a; cea implicită (n 43) nu era eligibilă. Un rezultat TRAIN de +0,053 cu CI90 [−0,045; +0,147] este compatibil cu zero.
2. **Sensibilitate la parametri (pentru §5c)**: pe TRAIN, `c` 0,6 → 0,7 (singur) duce avg R de la +0,21 (n 43) la +0,011 (n 243); `range_mult` 1,5 → 1,3 (singur) la +0,237 (n 54); toate trei la +0,053 (n 370). Vecinii ±20 % ai configurației alese **nu au fost rulați** (buget epuizat) — verificarea §5c trebuie să îi ruleze pe TRAIN (nu pe VALID).
3. **Stabilitate pe ani**: TRAIN 2018 negativ (−0,12 pe 39) în configurația aleasă și în toți vecinii; 2013 lipsește din `by_year` (limita de 300 de tranzacții). VALID 2019 și 2020 pozitive. Etapa E (date 2021–2023 pe H4, alt feed) a găsit H06 implicit la −0,058 / 106 tranzacții — un regim în care ipoteza nu a mers.
4. **Concentrare**: pe VALID, 3 instrumente (GBPUSD, GBPJPY, USDCAD) aduc tot profitul net; USDJPY pierde constant; eliminarea unui singur instrument ar schimba rezultatul cu ±5–9 R din 20.
5. **Poarta 1 trecută la limită**: CI90 inferior −0,012 vs −0,020. Cu 149 de tranzacții, CI90 are ≈ 0,29 R lățime; un avantaj real de +0,05 nu e distinct de zero la acest eșantion.
6. **Execuție și costuri**: umplere la deschiderea barei H4 următoare cu spread-ul modelat al orei (barele de 21:00 UTC plătesc ×2,2); SL înainte de TP în aceeași bară; slippage 1 punct; fără calendar economic (barele de expansiune sunt adesea bare de știri — în live blocarea pe știri ar tăia o parte din intrări, cu efect necunoscut asupra rezultatului). Swap modelat −5 puncte/zi (−3,3 R pe VALID).
7. **Filtre live active**: blackout-ul de rollover (±20 min la 17:00 NY) a blocat 10 semnale pe VALID (45 pe TRAIN), filtrul de spread 26 — rezultatul include o parte din frecările live, dar nu pe toate (`max_total_open_risk_pct`, pierdere zilnică etc. la valorile live; 0 respingeri pe VALID STANDARD).
8. **Hook-urile de ieșire neactivate** (Abatere 2) afectează H04, H08, H09, H13 (emulate sau cu cădere mai lungă), nu configurația aleasă (H06 folosește exclusiv `default_exit` = base + 48 h, care este ieșirea proiectată).
9. **H14 neevaluată, H12 prin wrapper**: H14 ar necesita NZDUSD (nu există în sursă); H12 a fost evaluată prin `disc-f3-h12` cu universul celor 12 instrumente (în etapa E universul avea 11 perechi FX fără aur/USDCHF) — rezultat negativ oricum.
10. **`by_year` parțial** pentru rulările cu n > 300 (limitare `MAX_TRADES_OUT` în `app.backtest`, fișier partajat, nemodificat).
11. **Mărimea contului**: pe H4 cu SL de 1–2 × ATR(100) riscul la 0,01 lot este de ordinul 5–12 USD (vezi C2, secțiunea de mărime); pe un cont de 200 USD cu risc 1–2 % majoritatea semnalelor ar fi respinse de lotul minim. Un PROMISING eventual ar însemna forward test pe PAPER/DEMO, nu bani reali (protocol §6).

## 7. Fișiere

- Plugin nou: `app/strategies/discovery/disc_f3_h12.py` (NAME `disc-f3-h12`, wrapper peste `disc_h12` cu univers din `cfg.xs_db`; auto-descoperit, niciodată oferit motorului live).
- Test de fum: `tests/test_stage_f_f3.py` (`/tmp/claude-0/venv/bin/python -m pytest -q tests/test_stage_f_f3.py` → 8 passed): import + rezolvare pentru toate cele 20 de ipoteze, tabelul TF/HTF pinat la `RUN_DEFAULTS`, forma de ieșire trend-v1 a wrapper-ului pe bare sintetice, fără lookahead (univers cu tot viitorul = univers trunchiat), cotația nu stabilește direcția, rulare completă prin `app.backtest.run` cu ieșire TIME la 108 h.
- Rulări: `research/results/F3/train_<ID>.json` (24 STANDARD), `train_ecn_{H06_N4_all,H06_N1_c0.7,H16,H10}.json`, `valid_H06_N4_all.json`, `valid_ecn_H06_N4_all.json`, `grid_log.jsonl`, plus `.out`/`.err` per rulare.
- Nu s-a modificat niciun fișier partajat (`app/backtest.py`, `app/strategy.py`, `app/risk.py`, `app/exits.py`, `app/labeler.py`, pluginurile existente, `app/strategies/__init__.py`, `_common.py`, `_htf.py`, `research/run_bt.py`); nimic nu a fost comis în git.
