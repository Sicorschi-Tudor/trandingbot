# Etapa F — Familia F2: trend D1/H4 ținut ca PORTOFOLIU pe 12 instrumente

Verdict: **REJECTED** la Poarta 1 (VALID). Configurația aleasă pe TRAIN (tsmom-v1, D1, lookback 126, filtru de volatilitate 0,5 σ, SL 3×ATR, portofoliu de 12 instrumente) are pe VALID (2019-01-01 → 2021-01-01, cont STANDARD) **n = 94, avg R = −0,248, CI90 [−0,486; +0,010], PF 0,63**, negativă în ambii ani; pe ECN −0,225 / PF 0,66. Niciuna dintre cele 24 de configurații încercate pe TRAIN nu a fost convingătoare: singura cu avg R > 0 și n ≥ 60 (cea aleasă) avea CI90 larg în jurul lui zero (PF 1,10) și nu s-a reprodus out-of-sample. Noul plugin `disc-f2-tsmom2` (ansamblu de lookback-uri) a fost **negativ sau nul pe TRAIN în toate variantele cu n ≥ 60**, pe D1 și pe H4.

Toate rulările: `research/results/F2/*.json` (runner `research/run_bt.py`, perioade fixe). Rezumatul de față este scris după rulările de pe VALID; protocolul (`docs/research/STAGE_F_PROTOCOL.md`) nu a fost modificat.

## 1. Protocol urmat (și abaterile, explicit)

- Date: `data/research.db` (STANDARD) pentru toată grila; `data/research_ecn.db` (ECN) doar pentru cele mai bune 3 configurații pe TRAIN și pentru configurația aleasă pe VALID. 12 instrumente, bare UTC, TRAIN 2013-06-01 → 2019-01-01, VALID 2019-01-01 → 2021-01-01. TEST nu a fost atins (runner-ul refuză fără `--allow-test`; nu s-a folosit `--start/--end`).
- Runner: `run_bt.py --workers 1 --swap-mode table`, `--htf NONE` (pluginurile D1/H4 nu folosesc HTF), ieșirea default a fiecărui plugin (`signal_flip` pentru tsmom-v1 și disc-f2-tsmom2; `channel(20)` pentru donchian-v1; `combo[trailing, signal_flip]` pentru ma-trend-v1). Pentru D1 runner-ul dezactivează automat blackout-ul de rollover și pauza după pierdere.
- Portofoliu: `--risk-json '{"max_open_positions": 12, "max_trades_per_day": 50, "max_same_currency_direction": 4}'`.
- **Abatere 1 (documentată): `max_spread_points` ridicat de la 30 (implicit live) la 100 pentru rulările D1 cu sufixul `_sp100`.** Motiv descoperit după primele 12 rulări: barele D1 se închid la ora rollover-ului (21–22 UTC), unde spread-ul modelat STANDARD este cel mai larg (×2,2/×2,6). La acea oră spread-ul depășește 30 de puncte pentru 10 din 12 instrumente (ex. GBPJPY 70–83, XAUUSD 66–78, AUDJPY 57–68, EURGBP 40–47; doar EURUSD 24–29 și USDJPY 26–31 trec), așa că filtrul live bloca ~85 % dintre candidați (`blocked_market` 9 426 din 11 108 la tsmom-v1 D1) și „portofoliul” era de fapt EURUSD + USDJPY (43 de tranzacții în 5,6 ani). Filtrul de 30 de puncte este gândit pentru intraday pe majore; pe D1 cu SL de 3×ATR spread-ul reprezintă ≤ 2 % din distanța SL, iar blocarea „spread > 20 % din SL” rămâne activă. **Spread-ul larg este în continuare plătit integral** la intrare (mod `stats`, pe ora UTC a barei), deci schimbarea nu face costurile mai optimiste, ci doar permite tranzacțiile. Cele 6 rulări D1 inițiale (cu 30) sunt păstrate și **numărate** în bugetul de 24. Rulările H4 (6) au rămas cu 30 de puncte: erau deja clar negative (avg R −0,04 … −0,13 la n = 580–1 473) și nu aveau cum să schimbe selecția.
- **Abatere 2 (limitare a runner-ului, nu a protocolului):** `app.backtest` păstrează doar ultimele 300 de tranzacții în lista `trades` (`MAX_TRADES_OUT = 300`), iar `by_year` din runner se calculează din această listă. Pentru rulările cu n > 300 (toate cele H4, plus tsmom-v1 lb63 și tsmom2 lbB pe D1) tabelul pe ani este deci parțial (ultimele 300 de tranzacții, 2016–2018). Metricile agregate (n, avg R, CI90, PF, max DD) sunt pe toate tranzacțiile. Configurația aleasă și VALID au n < 300, deci tabelele lor pe ani sunt complete. Nu am modificat fișierele partajate.
- Selecție (protocol §4): cea mai mare `avg_r` pe STANDARD/TRAIN dintre configurațiile cu **n ≥ 60** (D1/H4), ≤ 24 configurații. Exact **24 de configurații** au fost încercate pe TRAIN (STANDARD). Cele 3 rulări ECN pe TRAIN și cele 2 rulări VALID sunt aceleași configurații pe altă bază/altă perioadă, nu configurații noi.
- VALID a fost rulat **o singură dată** (STANDARD + ECN), după închiderea grilei. Nu a fost nevoie de re-rulare.
- Plugin nou: `app/strategies/discovery/disc_f2_tsmom2.py` (NAME `disc-f2-tsmom2`), test de fum `tests/test_stage_f_f2.py` (9 teste, trec: rezolvare în registru, forma de ieșire trend-v1, fără lookahead / independență de fereastră, cotația nu influențează direcția, cazuri construite pentru k și filtrul de volatilitate, rulare completă prin `app.backtest.run` cu ieșire FLIP și swap).

### Pluginul `disc-f2-tsmom2` (ce testează)

Oglindă a lui `tsmom_v1.py` (aceleași `RUN_DEFAULTS`, `default_exit` = `signal_flip`), dar cu ansamblu: pentru fiecare lookback L ∈ `lookbacks` (implicit [21, 63, 126, 252] bare închise) randamentul logaritmic r_L și scorul z_L = r_L / (σ₁·√L), σ₁ = deviația standard a ultimelor `vol_n` = 60 randamente de o bară (același filtru ca tsmom-v1). Un lookback votează sign(r_L) doar dacă |z_L| ≥ `min_z` = 0,5; direcția = semnul sumei voturilor, cu condiția ca ≥ k din 4 lookback-uri să voteze în acea direcție. SL = `sl_atr` × ATR(20); TP 20×ATR doar ca plasă. `bias()` (pentru `signal_flip`) = semnul sumei semnelor brute ale celor 4 randamente (fără filtru, fără k); egalitatea 2–2 dă 0 = „flat”, care nu închide poziția. Strict pe bare închise: `analyze()` și `bias()` citesc doar `sig_bars[-400:]`; cotația este folosită numai pentru prețul de intrare și filtrul de spread.

## 2. Grila pe TRAIN (STANDARD, 2013-06-01 → 2019-01-01) — toate cele 24 de configurații

`sp` = `max_spread_points` din `--risk-json`; restul riscului = portofoliu (12 / 50 / 4). Ieșirea = default-ul pluginului. Configurațiile cu n < 60 **nu sunt eligibile** pentru selecție (marcate cu „—”).

| # | Etapă | Strategie | TF | Parametri | sp | n | avg R | CI90 | PF | eligibil (n ≥ 60) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | bază | tsmom-v1 | D1 | implicit (lookback 126, min_z 0,5, sl_atr 3) | 30 | 43 | +0,0006 | [−0,501; +0,574] | 1,001 | — |
| 2 | bază | tsmom-v1 | H4 | implicit | 30 | 1221 | −0,1140 | [−0,192; −0,037] | 0,810 | da |
| 3 | bază | donchian-v1 | D1 | implicit (entry 55, exit 20, sl_atr 2) | 30 | 38 | −0,0696 | [−0,599; +0,522] | 0,908 | — |
| 4 | bază | donchian-v1 | H4 | implicit (entry 100, exit 20, sl_atr 2) | 30 | 1077 | −0,0484 | [−0,149; +0,048] | 0,929 | da |
| 5 | bază | ma-trend-v1 | D1 | implicit (EMA50/200, sl_atr 3, trail 3) | 30 | 39 | −0,1042 | [−0,261; +0,097] | 0,555 | — |
| 6 | bază | ma-trend-v1 | H4 | implicit | 30 | 997 | −0,0390 | [−0,080; +0,003] | 0,849 | da |
| 7 | grilă brută | disc-f2-tsmom2 | D1 | lb [21,63,126,252], k 2, sl_atr 3 | 30 | 53 | +0,1049 | [−0,339; +0,611] | 1,204 | — |
| 8 | grilă brută | disc-f2-tsmom2 | D1 | lb [21,63,126,252], k 3, sl_atr 3 | 30 | 33 | +0,1695 | [−0,473; +0,861] | 1,269 | — |
| 9 | grilă brută | disc-f2-tsmom2 | D1 | lb [21,63,126,252], k 4, sl_atr 3 | 30 | 15 | +0,3172 | [−0,694; +1,334] | 1,490 | — |
| 10 | grilă brută | disc-f2-tsmom2 | H4 | lb [21,63,126,252], k 2, sl_atr 3 | 30 | 1473 | −0,1304 | [−0,192; −0,066] | 0,770 | da |
| 11 | grilă brută | disc-f2-tsmom2 | H4 | lb [21,63,126,252], k 3, sl_atr 3 | 30 | 1012 | −0,1325 | [−0,222; −0,041] | 0,795 | da |
| 12 | grilă brută | disc-f2-tsmom2 | H4 | lb [21,63,126,252], k 4, sl_atr 3 | 30 | 580 | −0,1298 | [−0,254; +0,004] | 0,820 | da |
| 13 | D1 re-rulat (sp 100) | tsmom-v1 | D1 | implicit (lookback 126, min_z 0,5, sl_atr 3) | 100 | **198** | **+0,0576** | **[−0,146; +0,275]** | **1,103** | **da — ALEASĂ** |
| 14 | D1 re-rulat (sp 100) | donchian-v1 | D1 | implicit | 100 | 264 | −0,2222 | [−0,383; −0,046] | 0,680 | da |
| 15 | D1 re-rulat (sp 100) | ma-trend-v1 | D1 | implicit | 100 | 267 | −0,0594 | [−0,131; +0,011] | 0,739 | da |
| 16 | D1 re-rulat (sp 100) | disc-f2-tsmom2 | D1 | lb [21,63,126,252], k 2, sl_atr 3 | 100 | 249 | −0,0466 | [−0,215; +0,132] | 0,912 | da |
| 17 | D1 re-rulat (sp 100) | disc-f2-tsmom2 | D1 | lb [21,63,126,252], k 3, sl_atr 3 | 100 | 154 | −0,0097 | [−0,248; +0,261] | 0,984 | da |
| 18 | D1 re-rulat (sp 100) | disc-f2-tsmom2 | D1 | lb [21,63,126,252], k 4, sl_atr 3 | 100 | 94 | −0,0069 | [−0,333; +0,339] | 0,990 | da |
| 19 | rafinare | disc-f2-tsmom2 | D1 | lb [21,63,126,252], k 3, sl_atr 2 | 100 | 197 | −0,0370 | [−0,309; +0,284] | 0,952 | da |
| 20 | rafinare | disc-f2-tsmom2 | D1 | lb [21,63,126,252], k 3, sl_atr 4 | 100 | 152 | −0,0162 | [−0,205; +0,180] | 0,967 | da |
| 21 | rafinare | disc-f2-tsmom2 | D1 | lb B [10,21,63,126], k 3, sl_atr 3 | 100 | 312 | −0,1067 | [−0,223; +0,022] | 0,779 | da |
| 22 | rafinare | disc-f2-tsmom2 | D1 | lb C [42,84,168,336], k 3, sl_atr 3 | 100 | 137 | −0,2100 | [−0,455; +0,075] | 0,715 | da |
| 23 | vecini ai celei mai bune | tsmom-v1 | D1 | lookback 63 (min_z 0,5, sl_atr 3) | 100 | 341 | −0,1078 | [−0,214; +0,007] | 0,776 | da |
| 24 | vecini ai celei mai bune | tsmom-v1 | D1 | lookback 252 (min_z 0,5, sl_atr 3) | 100 | 120 | −0,0263 | [−0,267; +0,243] | 0,959 | da |

Fișiere: `train_<strategie>_<TF>[_...][_sp100].json` (ex. #13 = `train_tsmom-v1_D1_sp100.json`, #17 = `train_tsmom2_D1_k3_sl3_sp100.json`, #21 = `train_tsmom2_D1_k3_sl3_lbB_sp100.json`).

Observații pe TRAIN:
- Dintre cele 18 configurații eligibile (n ≥ 60), **una singură** are avg R > 0: tsmom-v1 D1 (#13), +0,058 cu CI90 [−0,15; +0,27] — nesemnificativă statistic. Toate celelalte 17 sunt negative.
- Ansamblul `disc-f2-tsmom2` nu îmbunătățește tsmom-v1 pe D1: k = 3 / 4 dau ≈ −0,01 (PF 0,98–0,99), k = 2 −0,05; SL mai strâns (2×ATR) sau mai larg (4×ATR) nu schimbă semnul; lookback-uri mai scurte (set B) sau mai lungi (set C) sunt mai rău (−0,11 / −0,21). Pe H4 toate variantele sunt ≈ −0,13 R cu PF 0,77–0,82 și CI90 aproape în întregime sub zero.
- Rulările D1 cu filtru de spread 30 (#1, #3, #5, #7–#9) au avg R pozitiv la n mic (15–53): practic doar EURUSD + USDJPY, 2014–2015 bune — eșantion prea mic, neeligibil.
- Sensibilitatea la parametri a configurației alese este slabă: lookback 63 → −0,108, lookback 252 → −0,026 (vecinii pe ambele părți sunt negativi), ceea ce sugerează că +0,058 la lookback 126 este zgomot de selecție.

### Cele mai bune 3 configurații pe TRAIN și pe ECN (`research_ecn.db`)

| # | Config | STANDARD n / avg R / CI90 / PF | ECN n / avg R / CI90 / PF |
|---|---|---|---|
| 13 | tsmom-v1 D1 lookback 126, sp 100 | 198 / +0,0576 / [−0,146; +0,275] / 1,103 | 201 / +0,0435 / [−0,166; +0,252] / 1,076 |
| 18 | disc-f2-tsmom2 D1 k 4, sl_atr 3, sp 100 | 94 / −0,0069 / [−0,333; +0,339] / 0,990 | 93 / +0,0202 / [−0,297; +0,377] / 1,031 |
| 17 | disc-f2-tsmom2 D1 k 3, sl_atr 3, sp 100 | 154 / −0,0097 / [−0,248; +0,261] / 0,984 | 154 / −0,0080 / [−0,240; +0,234] / 0,987 |

ECN nu schimbă imaginea: spread-ul este o parte mică din costul unei tranzacții D1 ținute săptămâni (swap-ul domină, vezi §4).

## 3. Configurația aleasă și DE CE

Regula §4: cea mai mare `avg_r` pe STANDARD/TRAIN cu n ≥ 60 → **#13, tsmom-v1 pe D1, parametri impliciți** (`lookback` 126, `vol_n` 60, `min_z` 0,5, `atr_n` 20, `sl_atr` 3, `tp_atr` 20), ieșire `signal_flip` pe semnul randamentului pe 126 de bare, portofoliu 12 / 50 / 4, `max_spread_points` 100, swap `table`. TRAIN: n = 198, avg R +0,0576, CI90 [−0,146; +0,275], PF 1,103, max DD 25,4 R; pe ani: 2013 −0,28 (15), 2014 +0,30 (37), 2015 +0,08 (32), 2016 +0,55 (37), 2017 −0,57 (33), 2018 +0,02 (44).

Comanda exactă (fără `--split`):

```
/tmp/claude-0/venv/bin/python research/run_bt.py --db data/research.db --strategy tsmom-v1 --tf D1 --htf NONE \
  --swap-mode table --workers 1 \
  --risk-json '{"max_open_positions": 12, "max_trades_per_day": 50, "max_same_currency_direction": 4, "max_spread_points": 100}' \
  --json-out research/results/F2/valid_tsmom-v1_D1_sp100.json
```

Am ales-o mecanic, după regulă, deși a priori era slabă (CI90 include zero, PF 1,10, 2 ani din 6 negativi, vecinii de lookback negativi). Protocolul cere o singură rulare pe VALID pentru configurația aleasă, indiferent de cât de convingătoare e pe TRAIN; o configurație slabă pe TRAIN care pică pe VALID este un rezultat valid.

## 4. VALID (2019-01-01 → 2021-01-01) — rulat o singură dată

| Cont | n | win | avg R | CI90 | PF | max DD (R) | serie max pierderi | net R | swap R (pondere în net) |
|---|---|---|---|---|---|---|---|---|---|
| **STANDARD** (`valid_tsmom-v1_D1_sp100.json`) | 94 | 0,245 | **−0,248** | **[−0,486; +0,010]** | **0,626** | 34,9 | 13 | −23,33 | −12,88 (55 %) |
| ECN (`valid_ecn_tsmom-v1_D1_sp100.json`) | 94 | — | −0,225 | [−0,462; +0,037] | 0,661 | 34,5 | — | −21,18 | — |

Pe ani (STANDARD):

| An | n | avg R | sumă R |
|---|---|---|---|
| 2019 | 43 | −0,330 | −14,21 |
| 2020 | 51 | −0,179 | −9,13 |

Pe ani (ECN): 2019 43 / −0,344; 2020 51 / −0,125.

Ieșiri (STANDARD): SL 48, FLIP 35, TP 3, END 8 (poziții închise forțat la 2021-01-01). Doar 24,5 % tranzacții câștigătoare; stop-ul de 3×ATR a fost atins în 51 % din cazuri, adică trendul pe 126 de zile s-a inversat brusc mai des decât a continuat în 2019–2020 (piețe FX în regim lateral, apoi șocul din martie 2020).

Pe simbol (STANDARD, avg R / n): XAUUSD +2,54 / 3, EURUSD +0,25 / 6, AUDUSD +0,24 / 8, USDCAD +0,04 / 11, AUDJPY −0,17 / 8, USDCHF −0,22 / 7, EURCHF −0,29 / 7, EURJPY −0,50 / 7, USDJPY −0,51 / 10, GBPJPY −0,51 / 12, EURGBP −0,97 / 8, GBPUSD −1,06 / 7. Singurul instrument cu trend lung în perioadă (aurul) a adus +7,6 R din 3 tranzacții; restul portofoliului a pierdut 31 R. Perechile cu GBP (Brexit) au fost cele mai rele: 0 câștiguri din 15.

Contoare (STANDARD): 6 226 analize, 3 594 candidați, 122 semnale, 94 umplute; `blocked_market` 0 (filtrul de spread nu a mai blocat nimic la 100 de puncte), blocaje de context 3 192 (poziție deja deschisă pe simbol) + 280 (expunere corelată pe monedă/direcție), 27 semnale respinse de limita `max_total_open_risk_pct` (risc total deschis, lăsat la valoarea live) și 1 de `consecutive_losses`.

### Poarta 1 (STANDARD)

| Criteriu | Prag | Valoare | Trece? |
|---|---|---|---|
| avg R | > 0 | −0,248 | **NU** |
| CI90 inferior | > −0,02 | −0,486 | **NU** |
| PF | > 1,10 | 0,626 | **NU** |
| n | ≥ 40 (D1/H4) | 94 | da |
| avg R > 0 în cel puțin un an | — | 2019 −0,33; 2020 −0,18 | **NU** |

**Verdict: REJECTED.** ECN pică la fel (avg R −0,225, PF 0,66, ambii ani negativi) → nu este nici `ECN_ONLY`. Nu se trece la TEST și nu se cere verificarea adversarială (§5).

## 5. Ce înseamnă rezultatul

- Trend following pe D1/H4 ca portofoliu de 12 perechi FX + aur, cu reguli clasice (momentum pe 3–12 luni, Donchian, medii mobile) și **cu costurile unui cont retail STANDARD plus swap conservator**, nu a avut avantaj nici pe 5,6 ani de TRAIN (18 configurații eligibile, una singură marginal pozitivă) și pică clar pe cei 2 ani de VALID. Rezultatul este consecvent cu etapa C2 (aceleași pluginuri, altă fereastră și alt feed) și cu literatura: momentumul pe FX a fost slab în 2015–2020, iar costul de carry/swap modelat (−0,5 pip/zi pe ambele direcții) consumă o parte mare din randament pe poziții ținute săptămâni: pe VALID swap-ul este 55 % din pierderea netă; fără el rezultatul ar fi fost tot negativ (≈ −0,11 R/tranzacție).
- Ansamblul de lookback-uri (`disc-f2-tsmom2`) reduce zgomotul semnalului (mai puține tranzacții la k mare), dar nu adaugă avantaj: pe TRAIN toate variantele cu n ≥ 60 au PF ≤ 0,99.

## 6. Caveats (ce ar putea schimba concluzia și ce nu am făcut)

1. **Eșantion mic pe D1**: 94 de tranzacții pe VALID și 198 pe TRAIN; CI90 de ±0,2–0,25 R. Un avantaj real de +0,05 R nu ar fi detectabil, dar nici nu ar schimba verdictul (am măsurat −0,25 cu limita superioară +0,01).
2. **Swap modelat, nu real**: tabel implicit −5 puncte/zi pe ambele direcții pentru toate instrumentele (conservator). Pe un cont real swap-ul este pozitiv pe direcția de carry și mult mai mare negativ pe aur; semnul rezultatului nu depinde de asta (vezi §5), dar mărimea da.
3. **Spread modelat la ora rollover-ului**: decizia D1 cade la 21–22 UTC, ora cu spread-ul maxim din model (×2,2–2,6). Un sistem live ar putea intra la deschiderea Londrei a doua zi, cu spread normal; costul de intrare este însă ≤ 2 % din SL pe D1 și nu explică pierderea.
4. **`max_spread_points` 100** este o abatere de la `--risk-json`-ul indicat pentru familie, introdusă după primele 12 rulări (vezi §1). Fără ea, D1 ar fi avut n = 15–53 (neeligibil) și F2 ar fi rămas practic netestată. Toate cele 24 de rulări sunt raportate.
5. **`max_total_open_risk_pct`** a rămas la valoarea live și a respins 27 de semnale pe VALID (din 122); portofoliul nu a fost deci întotdeauna „plin”. Nu am ridicat limita ca să nu adaug configurații; efectul asupra `avg_r` este probabil mic (semnalele respinse sunt distribuite aleatoriu în timp), dar nu l-am măsurat.
6. **`by_year` pentru rulările cu n > 300** (H4, tsmom-v1 lb63, tsmom2 lbB) acoperă doar ultimele 300 de tranzacții (limitare `MAX_TRADES_OUT` în `app.backtest`, fișier partajat pe care nu l-am modificat). Metricile agregate sunt complete.
7. **Fără calendar economic** și cu umplere la deschiderea barei următoare; pentru D1 rezultatele sunt, dacă ceva, **optimiste** (`skipped_gap` 0).
8. **Selection bias**: 24 de configurații pe TRAIN; cea aleasă avea +0,058 R și a dat −0,248 R out-of-sample — același tipar ca donchian-v1/D1 în etapa C2 (+0,095 TRAIN → −0,247 TEST).
9. Testul de sensibilitate ±20 % (§5 din protocol) nu a fost necesar (configurația nu a trecut Poarta 1); vecinii de lookback din grilă (63 / 252) erau deja negativi pe TRAIN.
10. Nu am rulat H4 cu `max_spread_points` 100 (buget epuizat pe D1, unde era întrebarea deschisă); pe H4 doar 1 bară din 6 cade la ora rollover-ului și rezultatele erau deja clar negative la n = 580–1 473, deci concluzia pe H4 nu depinde de filtru.

## 7. Fișiere

- Plugin nou: `app/strategies/discovery/disc_f2_tsmom2.py` (NAME `disc-f2-tsmom2`, auto-descoperit).
- Test de fum: `tests/test_stage_f_f2.py` (`/tmp/claude-0/venv/bin/python -m pytest -q tests/test_stage_f_f2.py` → 9 passed).
- Rulări: `research/results/F2/train_*.json` (24 STANDARD + 3 ECN), `research/results/F2/valid_tsmom-v1_D1_sp100.json`, `research/results/F2/valid_ecn_tsmom-v1_D1_sp100.json`.
- Nu s-a modificat niciun fișier partajat (`app/backtest.py`, `app/strategy.py`, `app/risk.py`, `app/exits.py`, pluginurile existente, `app/strategies/__init__.py`, `_common.py`, `_htf.py`, `research/run_bt.py`); nimic nu a fost comis în git.
