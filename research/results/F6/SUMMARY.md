# Etapa F — Familia F6: strategii de trend pe H4 pe aur (XAUUSD) și pe cross-urile JPY

Verdict: **REJECTED** la Poarta 1 (VALID). Configurația aleasă pe TRAIN (donchian-v1 pe H4, **doar XAUUSD**, `entry_n` 100, `exit_n` 20, SL 3×ATR(20), ieșirea default `channel(20)`) are pe VALID (2019-01-01 → 2021-01-01, cont STANDARD) **n = 32, avg R = +0,211, CI90 [−0,312; +0,794], PF 1,415**, 2019 negativ / 2020 pozitiv; pe ECN +0,212 / PF 1,419. Trece criteriile de semn (avg R > 0, PF > 1,10, un an pozitiv), dar **pică la n ≥ 40 (are 32) și la limita inferioară CI90 > −0,02 (are −0,31)**. Rezultatul este în aceeași direcție ca pe TRAIN (+0,197 / PF 1,42), nu o inversare ca în F2, dar cu 32 de tranzacții nu este distins statistic de zero și depinde în întregime de 2020 (raliul aurului: +10,4 R din 15 tranzacții; 2019: −3,6 R din 17).

Toate rulările: `research/results/F6/*.json` (runner `research/run_bt.py`, perioade fixe), jurnalul tuturor comenzilor: `research/results/F6/_runlog.txt`. Rezumatul este scris după rulările de pe VALID; protocolul (`docs/research/STAGE_F_PROTOCOL.md`) nu a fost modificat.

## 1. Protocol urmat (și abaterile, explicit)

- Date: `data/research.db` (STANDARD) pentru toată grila; `data/research_ecn.db` (ECN) doar pentru cele mai bune 3 configurații pe TRAIN și pentru configurația aleasă pe VALID. Bare H4 în UTC, TRAIN 2013-06-01 → 2019-01-01, VALID 2019-01-01 → 2021-01-01. TEST nu a fost atins (fără `--allow-test`, fără `--start/--end`).
- Runner: `run_bt.py --tf H4 --htf NONE --swap-mode table --workers 1`; ieșirea default a fiecărui plugin (`channel(exit_n=20)` pentru donchian-v1, `combo[trailing, signal_flip]` pentru ma-trend-v1, `signal_flip` pentru tsmom-v1) în grila de parametri; apoi politicile `trailing`, `channel(10)` și `combo[channel, trailing]` prin `--exit/--exit-params`.
- Trei sub-universuri, conform temei familiei: **(a) XAU** = XAUUSD singur; **(b) JPY** = AUDJPY, EURJPY, GBPJPY, USDJPY; **(c) ALL** = toate cele 12 instrumente (referință). **Alegerea sub-universului după vederea rezultatelor este o formă de selecție**: fiecare combinație (strategie, univers, parametri, ieșire) rulată este numărată ca o configurație distinctă în bugetul de 24.
- **Abatere 1 (decisă ÎNAINTE de prima rulare, aplicată identic tuturor celor 24 de configurații, documentată aici):** `--risk-json` extins față de cel indicat pentru familie: `{"max_open_positions": 12, "max_trades_per_day": 50, "max_same_currency_direction": 4, "max_spread_points": 100, "rollover_blackout_minutes": 0, "cooldown_after_loss_minutes": 0, "max_consecutive_losses": 1000, "max_total_open_risk_pct": 100, "max_daily_loss_pct": 100}`. Motive, verificate în baza de date înainte de rulări: (i) barele H4 ale brokerului „NY close” se deschid/închid în UTC la 21/01/05/09/13/17 (vară) sau 22/02/06/10/14/18 (iarnă), deci **o decizie din 6 cade exact la ora rollover-ului** (17:00 New York), unde blocarea live `rollover_blackout_minutes` (±20 min) respinge orice intrare, iar spread-ul modelat este ×2,2–2,6 (XAUUSD 66–78 puncte, GBPJPY 70–83, AUDJPY 57–68 > 30); la decizia de la 01/02 UTC (×1,4/1,3) XAUUSD (42/39), GBPJPY (45/42) și AUDJPY (36/34) depășesc din nou 30 de puncte. Cu setările live, un breakout Donchian pe aur sau GBPJPY ar fi fost ratat sistematic în 2 din 6 sloturi (în F2: `blocked_market` 963 + `Rollover` 136 pe donchian H4). (ii) Limitele de cont (pauză după pierdere, 3 pierderi consecutive, risc total deschis 2 %, pierdere zilnică) sunt protecții ale contului, nu ale strategiei; etapa C2 le-a dezactivat explicit în rulările în R exact din acest motiv, iar F2 a ridicat `max_spread_points` la 100 pe D1 (abaterea 1 din F2). **Spread-ul este în continuare plătit integral** (mod `stats`, pe ora UTC a barei), deci schimbarea nu face costurile mai optimiste. Blocarea „spread > 20 % din SL” rămâne activă. Referință pentru efectul abaterii: rulările F2 cu `--risk-json`-ul literal (spread 30, blackout activ) pe 12 instrumente H4 dau donchian-v1 −0,048 R (n 1077), ma-trend-v1 −0,039 (n 997), tsmom-v1 −0,114 (n 1221) față de −0,052 / −0,045 / −0,103 aici — practic identice, deci abaterea nu umflă rezultatele.
- **Limitare a runner-ului (nu a protocolului):** `app.backtest` păstrează doar ultimele 300 de tranzacții în lista `trades` (`MAX_TRADES_OUT`), iar `by_year` se calculează din această listă; pentru rulările cu n > 300 (toate cele pe ALL, majoritatea pe JPY) tabelul pe ani este parțial (ultimele 300 de tranzacții). Metricile agregate (n, avg R, CI90, PF, max DD) sunt pe toate tranzacțiile. Configurația aleasă (n 92 TRAIN, 32 VALID) are tabele pe ani complete.
- Selecție (protocol §4): cea mai mare `avg_r` pe STANDARD/TRAIN dintre configurațiile cu **n ≥ 60** (H4), ≤ 24 configurații. Exact **24 de configurații** au fost încercate pe TRAIN (STANDARD). Cele 3 rulări ECN pe TRAIN și cele 2 rulări VALID sunt aceleași configurații pe altă bază / altă perioadă, nu configurații noi.
- VALID a fost rulat **o singură dată** (STANDARD + ECN), după închiderea grilei. Nu a fost nevoie de re-rulare.
- Niciun plugin nou (familia testează pluginurile existente din etapa C2 pe H4). Test de fum nou: `tests/test_stage_f_f6.py` (9 teste, trec): rezolvarea în registru și `entry_n` automat 100 pe H4, forma de ieșire trend-v1 pe bare H4 sintetice pentru cele 3 pluginuri, decizii doar pe bare închise (independență de fereastră, cotația nu schimbă direcția), validitatea specificațiilor de ieșire `trailing` / `channel` / `combo`, rulare completă prin `app.backtest.run` pe H4 cu setările de risc F6 (ieșiri CHANNEL și TRAIL apar, blackout-ul de rollover nu blochează, swap negativ, determinism).

## 2. Grila pe TRAIN (STANDARD, 2013-06-01 → 2019-01-01) — toate cele 24 de configurații

H4, `--htf NONE`, swap `table`, riscul din Abaterea 1. „default” = ieșirea default a pluginului. Configurațiile cu n < 60 nu ar fi eligibile (toate au n ≥ 60 aici).

| # | Etapă | Strategie | Univers | Parametri | Ieșire | n | avg R | CI90 | PF | max DD (R) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | bază | donchian-v1 | XAU | entry 100, exit 20, sl_atr 2 | default channel(20) | 105 | +0,1237 | [−0,198; +0,513] | 1,201 | 10,5 |
| 2 | bază | donchian-v1 | JPY | entry 100, exit 20, sl_atr 2 | default | 416 | +0,0700 | [−0,108; +0,248] | 1,105 | 52,4 |
| 3 | bază | donchian-v1 | ALL | entry 100, exit 20, sl_atr 2 | default | 1199 | −0,0521 | [−0,154; +0,044] | 0,924 | 106,5 |
| 4 | bază | ma-trend-v1 | XAU | EMA 50/200, sl_atr 3, trail 3 | default combo[trailing, flip] | 128 | −0,0188 | [−0,130; +0,102] | 0,926 | 10,8 |
| 5 | bază | ma-trend-v1 | JPY | EMA 50/200, sl_atr 3, trail 3 | default | 511 | −0,0386 | [−0,086; +0,011] | 0,835 | 24,9 |
| 6 | bază | ma-trend-v1 | ALL | EMA 50/200, sl_atr 3, trail 3 | default | 1641 | −0,0451 | [−0,077; −0,012] | 0,820 | 83,6 |
| 7 | bază | tsmom-v1 | XAU | lookback 126, min_z 0,5, sl_atr 3 | default signal_flip | 127 | −0,1014 | [−0,353; +0,162] | 0,846 | 24,7 |
| 8 | bază | tsmom-v1 | JPY | lookback 126, min_z 0,5, sl_atr 3 | default | 509 | −0,0653 | [−0,178; +0,050] | 0,886 | 62,8 |
| 9 | bază | tsmom-v1 | ALL | lookback 126, min_z 0,5, sl_atr 3 | default | 1309 | −0,1030 | [−0,178; −0,030] | 0,827 | 161,0 |
| 10 | rafinare | donchian-v1 | XAU | entry 55, sl_atr 2 | default | 143 | +0,1434 | [−0,141; +0,451] | 1,222 | 12,9 |
| 11 | rafinare | donchian-v1 | XAU | entry 200, sl_atr 2 | default | 72 | +0,1220 | [−0,284; +0,561] | 1,195 | 9,3 |
| **12** | **rafinare** | **donchian-v1** | **XAU** | **entry 100, sl_atr 3** | **default channel(20)** | **92** | **+0,1971** | **[−0,084; +0,508]** | **1,423** | **6,2** |
| 13 | rafinare | donchian-v1 | JPY | entry 55, sl_atr 2 | default | 591 | +0,0458 | [−0,096; +0,201] | 1,070 | 71,3 |
| 14 | rafinare | donchian-v1 | JPY | entry 200, sl_atr 2 | default | 262 | +0,0603 | [−0,149; +0,270] | 1,092 | 28,9 |
| 15 | rafinare | donchian-v1 | JPY | entry 100, sl_atr 3 | default | 363 | +0,0682 | [−0,064; +0,205] | 1,129 | 36,7 |
| 16 | rafinare | ma-trend-v1 | JPY | EMA 50/100, sl_atr 3, trail 3 (cel mai bun H4 din C2) | default | 609 | −0,0124 | [−0,060; +0,042] | 0,948 | 14,6 |
| 17 | rafinare | tsmom-v1 | JPY | lookback 63 (cel mai bun H4 din C2) | default | 750 | −0,0336 | [−0,118; +0,049] | 0,932 | 55,5 |
| 18 | ieșiri | donchian-v1 | XAU | entry 100, sl_atr 3 | `trailing` start 1R, 3×ATR14, min 0 | 98 | +0,1474 | [−0,111; +0,421] | 1,289 | 6,9 |
| 19 | ieșiri | donchian-v1 | XAU | entry 100, sl_atr 3 | `channel` 10 | 113 | +0,0194 | [−0,172; +0,226] | 1,048 | 9,7 |
| 20 | ieșiri | donchian-v1 | XAU | entry 100, sl_atr 3 | `combo[channel 20, trailing]` start 2R, 2×ATR14 | 98 | +0,1736 | [−0,042; +0,407] | 1,386 | 6,0 |
| 21 | ieșiri | donchian-v1 | JPY | entry 100, sl_atr 3 | `trailing` start 1R, 3×ATR14, min 0 | 388 | +0,0111 | [−0,110; +0,128] | 1,020 | 39,1 |
| 22 | ieșiri | donchian-v1 | JPY | entry 100, sl_atr 3 | `channel` 10 | 447 | +0,0171 | [−0,078; +0,117] | 1,040 | 33,5 |
| 23 | ieșiri | donchian-v1 | JPY | entry 100, sl_atr 3 | `combo[channel 20, trailing]` start 2R, 2×ATR14 | 393 | +0,0316 | [−0,085; +0,139] | 1,061 | 35,8 |
| 24 | vecin | donchian-v1 | XAU | entry 55, sl_atr 3 | default | 130 | +0,1259 | [−0,143; +0,390] | 1,228 | 9,6 |

Fișiere: `train_<strategie>_H4_<univers>_<tag>.json` (ex. #12 = `train_donchian-v1_H4_XAU_e100_sl3.json`, #20 = `train_donchian-v1_H4_XAU_e100_sl3_xcombo.json`, #16 = `train_ma-trend-v1_H4_JPY_ema100_sl3.json`).

Observații pe TRAIN:
- **Donchian este singura strategie cu avg R > 0**, și numai pe sub-universuri: XAU +0,12…+0,20 (n 72–143), JPY +0,01…+0,07 (n 262–591); pe toate cele 12 este negativă (−0,052, n 1199). ma-trend-v1 și tsmom-v1 sunt negative pe toate cele trei universuri și în ambele variante suplimentare (#16, #17). Deci teza familiei („instrumente cu trend-uri lungi”) se vede numai la breakout-ul de canal, nu la momentum sau medii mobile.
- Pe aur, toate cele 8 variante Donchian (#1, #10–12, #18–20, #24) au avg R între +0,02 și +0,20 și PF 1,05–1,42, dar **niciuna nu are CI90 care exclude zero** (n 72–143 pe 5,6 ani). SL mai larg (3×ATR) ajută consistent (+0,12 → +0,20 la entry 100; rata de câștig 32 % → 39 %), `entry_n` 55/100/200 schimbă puțin (+0,12…+0,14 la sl 2). Ieșirea `channel(10)` strică clar (+0,02); `trailing` și `combo` sunt sub `channel(20)`.
- Pe cross-urile JPY efectul este mult mai slab (PF ≤ 1,13) și dominat de 2014–2015 (yen-ul slab după „Abenomics”); 2017 este negativ în toate variantele; AUDJPY este negativ în 5 din 6 variante Donchian. Pe simbol (#15): GBPJPY +0,16, EURJPY +0,08, USDJPY +0,04, AUDJPY −0,01.
- Pe 12 instrumente, Donchian (#3, pe simbol): pozitiv pe XAUUSD +0,06, USDCAD +0,15, AUDJPY +0,13, GBPJPY +0,11, USDJPY +0,10, EURJPY +0,07; negativ pe EURCHF −0,43, USDCHF −0,35, EURGBP −0,21, AUDUSD −0,17, EURUSD −0,06, GBPUSD −0,01. Sub-universurile „bune” au fost alese **după** vederea acestui tip de rezultat (în F2 și aici), ceea ce este exact forma de selecție pe care VALID trebuie s-o verifice.
- Swap-ul contează puțin pe aur (−2,6 R din 92 de tranzacții la #12, 14 % din net) și mult mai mult pe JPY (−13,6 R din +24,8 R net la #15).

### Cele mai bune 3 configurații pe TRAIN și pe ECN (`research_ecn.db`)

| # | Config | STANDARD n / avg R / CI90 / PF | ECN n / avg R / CI90 / PF |
|---|---|---|---|
| 12 | donchian-v1 XAU entry 100, sl_atr 3, channel(20) | 92 / +0,1971 / [−0,084; +0,508] / 1,423 | 92 / +0,2032 / [−0,079; +0,514] / 1,439 |
| 20 | donchian-v1 XAU entry 100, sl_atr 3, combo[channel, trailing] | 98 / +0,1736 / [−0,042; +0,407] / 1,386 | 98 / +0,1818 / [−0,035; +0,416] / 1,407 |
| 18 | donchian-v1 XAU entry 100, sl_atr 3, trailing | 98 / +0,1474 / [−0,111; +0,421] / 1,289 | 98 / +0,1528 / [−0,106; +0,427] / 1,300 |

ECN schimbă aproape nimic pe aur: spread-ul STANDARD de 30 de puncte (0,30 USD) este ≈ 1 % din distanța SL de 3×ATR H4 (de ordinul 15–30 USD), iar swap-ul (identic) este costul dominant.

## 3. Configurația aleasă și DE CE

Regula §4: cea mai mare `avg_r` pe STANDARD/TRAIN cu n ≥ 60 → **#12, donchian-v1 pe H4, numai XAUUSD, `entry_n` 100, `exit_n` 20, `atr_n` 20, `sl_atr` 3, `tp_atr` 20 (plasă), ieșirea default `channel(20)`**, swap `table`, riscul din Abaterea 1. TRAIN: n = 92, avg R +0,1971, CI90 [−0,084; +0,508], PF 1,423, win 39 %, max DD 6,2 R, net +18,1 R (swap −2,6 R); pe ani: 2013 +0,28 (10), 2014 +0,41 (14), 2015 +0,19 (18), 2016 +0,19 (15), 2017 −0,11 (20), 2018 +0,36 (15). Ieșiri: CHANNEL 54, SL 33, TP 4, END 1.

Comanda exactă (fără `--split`):

```
/tmp/claude-0/venv/bin/python research/run_bt.py --db data/research.db --strategy donchian-v1 --tf H4 --htf NONE \
  --symbols XAUUSD --swap-mode table --workers 1 --strategy-json '{"entry_n": 100, "sl_atr": 3.0}' \
  --risk-json '{"max_open_positions": 12, "max_trades_per_day": 50, "max_same_currency_direction": 4, "max_spread_points": 100, "rollover_blackout_minutes": 0, "cooldown_after_loss_minutes": 0, "max_consecutive_losses": 1000, "max_total_open_risk_pct": 100, "max_daily_loss_pct": 100}' \
  --json-out research/results/F6/valid_donchian-v1_H4_XAU_e100_sl3.json
```

Am aplicat regula mecanic. Observație făcută **înainte** de rularea pe VALID și notată aici: configurația are 92 de tranzacții în 5,6 ani (≈ 16/an), deci pe cei 2 ani de VALID erau de așteptat ≈ 33 de tranzacții — **sub pragul n ≥ 40 al Porții 1**. Regula de selecție nu are un criteriu de frecvență, așa că nu am schimbat alegerea (ar fi fost o abatere de la protocol); rezultatul pe n era previzibil, iar lipsa de putere statistică este, în sine, o concluzie despre universul „XAUUSD singur pe H4”.

## 4. VALID (2019-01-01 → 2021-01-01) — rulat o singură dată

| Cont | n | win | avg R | CI90 | PF | max DD (R) | serie max pierderi | net R | swap R |
|---|---|---|---|---|---|---|---|---|---|
| **STANDARD** (`valid_donchian-v1_H4_XAU_e100_sl3.json`) | 32 | 0,375 | **+0,2113** | **[−0,312; +0,794]** | **1,415** | 4,8 | 5 | +6,76 | −0,70 |
| ECN (`valid_ecn_donchian-v1_H4_XAU_e100_sl3.json`) | 32 | 0,375 | +0,2123 | [−0,308; +0,779] | 1,419 | 4,8 | 5 | +6,79 | −0,70 |

Pe ani (STANDARD):

| An | n | avg R | sumă R |
|---|---|---|---|
| 2019 | 17 | −0,212 | −3,61 |
| 2020 | 15 | +0,692 | +10,37 |

Pe ani (ECN): 2019 17 / −0,201; 2020 15 / +0,680. Ieșiri (STANDARD): CHANNEL 17, SL 12, TP 2 (plasa de 20×ATR atinsă de două ori în raliul din 2020), END 1 (poziție închisă forțat la 2021-01-01). Contoare: 150 de candidați, 118 blocați pentru „poziție deja deschisă”, 32 semnale, 32 umplute, `blocked_market` 0.

### Poarta 1 (STANDARD)

| Criteriu | Prag | Valoare | Trece? |
|---|---|---|---|
| avg R | > 0 | +0,211 | da |
| CI90 inferior | > −0,02 | −0,312 | **NU** |
| PF | > 1,10 | 1,415 | da |
| n | ≥ 40 (D1/H4) | 32 | **NU** |
| avg R > 0 în cel puțin un an | — | 2019 −0,21; 2020 +0,69 | da |

**Verdict: REJECTED** (2 din 5 criterii nu sunt îndeplinite). ECN pică identic (n 32, CI90 inferior −0,31) → nu este nici `ECN_ONLY`. Nu se trece la TEST și nu se cere verificarea adversarială (§5).

## 5. Ce înseamnă rezultatul

- Spre deosebire de F2 (tsmom D1: +0,06 TRAIN → −0,25 VALID), aici rezultatul out-of-sample are **același semn și aceeași mărime** ca pe TRAIN (+0,20 → +0,21, PF 1,42 → 1,42, win 39 % → 38 %). Asta este consecvent cu ipoteza că breakout-ul de canal pe aur a avut un avantaj mic și real în 2013–2020, dar la fel de consecvent cu zgomotul: CI90 pe VALID este [−0,31; +0,79], iar întregul rezultat vine din 15 tranzacții din 2020 (raliu de +25 % al aurului, cu două TP-uri „de plasă” la 20×ATR). În 2019 — tot un an cu trend ascendent pe aur (+18 %) — strategia a pierdut (−0,21 R, 17 tranzacții). Un singur instrument la ≈ 16 tranzacții/an nu poate produce dovada cerută de protocol în 2 ani.
- Pe portofoliul de 12 instrumente H4 Donchian rămâne negativ (−0,05 R, n 1199, PF 0,92), iar pe cross-urile JPY avantajul este marginal (PF ≤ 1,13) și concentrat în 2014–2015. Toate configurațiile de momentum (tsmom-v1) și de medii mobile (ma-trend-v1) pe H4 sunt negative pe toate cele trei universuri, inclusiv variantele „cele mai bune din C2”. Concluzia C2/F2 (trend pe H4 ca familie nu are avantaj după costuri) se menține; excepția aparentă (Donchian pe aur) nu este demonstrabilă la puterea statistică disponibilă.
- Dacă cineva vrea totuși să continue această direcție, calea protocolară este un studiu nou pre-înregistrat, nu o repetare a VALID: de exemplu Donchian pe aur pe un timeframe mai mic (H1) sau cu `entry_n` mai mic, ca să existe ≥ 40 de tranzacții pe 2 ani, cu un singur config fixat **înainte** de a vedea VALID. Rezultatul de aici (+0,21 R pe 32 de tranzacții) nu trebuie folosit ca argument pentru bani reali.

## 6. Caveats

1. **Selecție în trei straturi**: strategie (3), univers (3), parametri/ieșiri (18 variante Donchian). Universul XAU a fost ales după ce s-au văzut rezultatele pe simbol din F2 și din #3; cu 24 de configurații, cel mai bun avg R pe TRAIN este optimist prin construcție. Toate cele 24 sunt raportate și numărate.
2. **Eșantion foarte mic**: 92 de tranzacții pe TRAIN, 32 pe VALID. CI90 de ±0,3 R pe TRAIN și ±0,55 R pe VALID; nici un avantaj real de +0,1 R nu ar fi fost detectabil.
3. **Abaterea 1** (`max_spread_points` 100, blackout rollover 0, limite de cont dezactivate) a fost decisă înainte de rulări și aplicată uniform, dar înseamnă că un bot live cu setările actuale **nu ar reproduce** aceste tranzacții: 1 din 6 decizii H4 ar fi blocată la rollover și spread-ul aurului (30 de puncte bază, 39–78 în Asia/rollover) ar depăși filtrul de 30 în 2 din 6 sloturi. Spread-ul a fost plătit integral în toate rulările.
4. **Swap modelat, nu real**: tabel implicit −0,5 pip/zi pe ambele direcții. Pe aur swap-ul real al brokerilor retail este de regulă mult mai negativ pe cumpărare (de ordinul −0,3…−0,6 USD/oz/zi pe 1 lot, adică de câteva ori tabelul); cu poziții ținute 1–3 săptămâni (277 zile de swap pe 32 de tranzacții) acest cost ar reduce vizibil cele +6,8 R nete. Semnul rezultatului pe VALID ar putea depinde de asta.
5. **Spread-ul aurului modelat** (30 de puncte = 0,30 USD STANDARD, 12 ECN) este o aproximare; brokerii retail au deseori 0,20–0,50 USD pe aur, mai mult la știri. Fără calendar economic (NFP, FOMC) — rezultatele sunt, dacă ceva, optimiste.
6. **Bare H4 pe granițele brokerului** (deplasate cu o oră la schimbările DST New York), nu pe ore UTC fixe; `entry_n` 100 bare ≈ 17 zile de tranzacționare.
7. **`by_year` parțial** pentru rulările cu n > 300 (toate pe ALL, #2, #13–15, #17, #21–23 pe JPY): limitarea `MAX_TRADES_OUT` din `app.backtest` (fișier partajat, nemodificat). Metricile agregate sunt complete.
8. Testul de sensibilitate ±20 % (§5) nu a fost necesar (nu a trecut Poarta 1); vecinii din grilă (entry 55/200, sl 2, ieșiri alternative) sunt toți pozitivi dar mai slabi (+0,02…+0,17), deci configul ales este un maxim local, nu un platou.
9. **Rezultatele din trecut nu garantează rezultatele viitoare**: un backtest negativ arată că regulile nu au avut avantaj demonstrabil pe acest istoric; unul pozitiv la n = 32 nu ar fi dovedit nimic nici dacă ar fi trecut poarta.

## 7. Fișiere

- Fără plugin nou (familia folosește `app/strategies/donchian_v1.py`, `ma_trend_v1.py`, `tsmom_v1.py` existente, neschimbate).
- Test de fum: `tests/test_stage_f_f6.py` (`/tmp/claude-0/venv/bin/python -m pytest -q tests/test_stage_f_f6.py` → 9 passed).
- Rulări: `research/results/F6/train_*.json` (24 STANDARD + 3 ECN), `research/results/F6/valid_donchian-v1_H4_XAU_e100_sl3.json`, `research/results/F6/valid_ecn_donchian-v1_H4_XAU_e100_sl3.json`, jurnal `research/results/F6/_runlog.txt`.
- Nu s-a modificat niciun fișier partajat (`app/backtest.py`, `app/strategy.py`, `app/risk.py`, `app/exits.py`, pluginurile existente, `app/strategies/__init__.py`, `_common.py`, `_htf.py`, `research/run_bt.py`); nimic nu a fost comis în git.
