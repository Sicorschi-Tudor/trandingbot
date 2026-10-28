# Stage C2 — cercetare walk-forward pe timeframe-uri mari (D1 / H4 / H1)

> Protocol pre-înregistrat: secțiunea de mai jos a fost scrisă în acest fișier ÎNAINTE de orice rulare pe TEST și nu a fost modificată după vederea rezultatelor. Rezultatele sunt adăugate dedesubt de scriptul `walkforward_c2.py`.

## Protocol (pre-înregistrat)

- Date: `bars_archive` dintr-o copie read-only a bazei de date (`c2.db`, copiată din `data/bot.db`); feed MetaQuotes-Demo, bare D1 pe ziua brokerului (închidere New York). Serverul live nu este atins.
- Simboluri (11): EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, EURJPY, GBPJPY, NZDUSD, EURGBP, EURCHF, AUDJPY.
- Strategii: `donchian-v1`, `ma-trend-v1`, `tsmom-v1` pe fiecare din D1, H4, H1 (9 celule strategie × TF). `trend-v1` pe H1 (HTF H4, parametri live, ieșire base cu BE 1R, fără time stop, fără sesiune) doar ca referință, fără căutare.
- Împărțire cronologică, embargo de 5 bare la graniță (TEST începe la graniță + 5 bare din TF):
  - D1: TRAIN = început date .. 2021-12-31, TEST = 2022-01-01 (+5 bare) .. sfârșit.
  - H4: TRAIN = început .. 2024-06-30, TEST = 2024-07-01 (+5 bare) .. sfârșit.
  - H1: TRAIN = început .. 2025-06-30, TEST = 2025-07-01 (+5 bare) .. sfârșit.
  - „Început” = prima bară disponibilă pe TF; fiecare simbol începe să tranzacționeze când pluginul are destule bare închise (încălzire). Pozițiile TRAIN încă deschise sunt închise la sfârșitul TRAIN (nu se folosesc bare TEST).
- Costuri STANDARD (principal): spread fix pe simbol (puncte) EURUSD 10, GBPUSD 14, USDJPY 12, AUDUSD 12, USDCAD 16, EURJPY 18, GBPJPY 28, NZDUSD 16, EURGBP 14, EURCHF 18, AUDJPY 18; slippage 1 punct; comision 0; swap `table` cu tabelul implicit conservator −0.5 pip/zi pe ambele direcții, miercuri ×3.
- Sensibilitate (doar raportată, nu decide): swap `zero`; ECN = 25% din spread-uri + 7 USD/lot dus-întors (swap table).
- Portofoliu: toate cele 11 perechi, filtru de corelație max 1 poziție per monedă+direcție, `max_open_positions` 6, fără filtru de sesiune. R măsurat cu `size_mode=r_only` (risc 0.5% pe 200 USD, fără rotunjirea lotului). În rulările R limitele de pierdere zilnică / pierderi consecutive / risc total deschis sunt dezactivate (100% / 1000 / 100%), ca R să măsoare strategia, nu limitele contului.
- Căutare doar pe TRAIN (costuri STANDARD), per strategie × TF:
  - donchian-v1 (8 combinații): `sl_atr`=2 cu (entry_n, exit_n) ∈ {20,55,100}×{10,20} (6) + `sl_atr`=3 cu (55,20) și (100,20) (2).
  - ma-trend-v1 (4): `ema_slow_n` ∈ {100,200} × `sl_atr`=`trail_atr` ∈ {2,3}.
  - tsmom-v1 (6): `lookback` ∈ {63,126,252} × `min_z` (filtru volatilitate) ∈ {0, 0.5}.
  - Ieșirea pluginului (default) în grilă; alegere = cel mai mare avg R cu ≥ 60 tranzacții TRAIN (dacă niciuna nu are 60: cea cu cele mai multe tranzacții, marcată).
  - Apoi ≤ 4 politici de ieșire pe configul ales (inclusiv default), aceeași regulă de alegere:
    - donchian-v1: E1 default `channel(exit_n)`; E2 `trailing` (start 1R, 3×ATR14, min 0); E3 `combo[channel, trailing]` (aceiași parametri); E4 `combo[channel, trailing]` cu trailing de la 2R, 2×ATR14.
    - ma-trend-v1: E1 default `combo[trailing, signal_flip]`; E2 `signal_flip` singur; E3 `trailing` singur (de la 0R, `trail_atr`×ATR14); E4 `channel` 20.
    - tsmom-v1: E1 default `signal_flip`; E2 `trailing` (start 1R, 3×ATR14); E3 `combo[signal_flip, trailing]`; E4 `channel` 20.
  - Numărul total de configuri încercate este raportat.
- TEST: configul selectat per strategie × TF, evaluat O SINGURĂ dată (STANDARD + cele două sensibilități). Raport: tranzacții, win rate, avg R net, CI90 bootstrap, profit factor, max drawdown în R, cea mai lungă serie de pierderi, R/an, avg R pe simbol, avg R pe an, ponderea swap-ului în costuri.
- Regula de decizie (costuri STANDARD): PROMISING doar dacă TEST avg R > 0 ȘI limita inferioară CI90 > −0.05 ȘI PF > 1.15 ȘI ≥ 60 tranzacții TEST ȘI pozitiv în cel puțin 2/3 din anii TEST (ani calendaristici cu ≥ 1 tranzacție; pentru H1: 2 din 3 sub-perioade egale). Altfel REJECTED.
- Verificare practică de mărime (contul demo de 200 USD): pentru fiecare config selectat, cu lot minim 0.01 și tick value reale (curs istoric la momentul semnalului, contract 100 000; verificat față de tick value-urile din tranzacțiile live), mediana și percentila 90 a riscului în USD al UNEI tranzacții la 0.01 lot (distanța SL × tick value) și capitalul minim necesar ca riscul pe tranzacție să fie ≤ 1% și ≤ 2%. Plus un backtest cu mărime live (`size_mode` live) pe TEST, equity 200, risc 2%, setările de risc live (cu `max_open_positions` 6): equity final, drawdown maxim %, câte semnale au fost sărite pentru că lotul minim depășește riscul.

<!-- REZULTATE (generate de mkreport_c2.py) -->

## Concluzie pe scurt

**0 din 9 combinații strategie × timeframe sunt PROMISING; toate cele 9 sunt REJECTED** conform regulii pre-înregistrate. Toate au avg R negativ pe TEST la costuri STANDARD, cu limita superioară CI90 sub zero în toate cele 9 cazuri, iar rezultatul rămâne negativ și fără swap și cu costuri ECN. Referința trend-v1 pe H1 este de asemenea clar negativă. Recomandare: fără trecere pe bani reali; contul rămâne pe DEMO și niciuna dintre aceste strategii nu se activează live.

În plus, chiar dacă o strategie ar fi fost profitabilă, **contul de 200 USD este prea mic pentru D1/H4**: o singură tranzacție la lotul minim 0.01 riscă în mediană ~14–21 USD pe D1 (7–10% din cont) și ~4.6–7.1 USD pe H4; la 2% risc backtestul cu mărime live nu deschide NICIO tranzacție pe D1 (vezi secțiunea de mărime).

| Strategie / TF | Config selectat (TRAIN) | TRAIN n / avg R | TEST n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | R/an | ani (sub-perioade) pozitivi | Decizie |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| donchian-v1/D1 | entry_n=20, exit_n=10, sl_atr=2; ieșire E4 `combo[channel, trailing] channel_n=10, trail_start_r=2, trail_atr_mult=2, trail_min_r=0` | 207 / +0.095 | 156 | 0.31 | -0.247 | [-0.384; -0.105] | 0.571 | 42.1 | 9 | -8.0 | 0/5 ani | **REJECTED** |
| ma-trend-v1/D1 | ema_slow_n=100, sl_atr=2, trail_atr=2; ieșire E1-default `combo[trailing, signal_flip] trail_start_r=0, trail_atr_mult=2, trail_min_r=0` | 255 / -0.017 | 202 | 0.24 | -0.077 | [-0.150; -0.000] | 0.727 | 19.7 | 12 | -3.2 | 1/5 ani | **REJECTED** |
| tsmom-v1/D1 | lookback=252, min_z=0; ieșire E4 `channel channel_n=20` | 273 / -0.031 | 223 | 0.20 | -0.135 | [-0.211; -0.060] | 0.585 | 34.2 | 17 | -6.3 | 1/5 ani | **REJECTED** |
| donchian-v1/H4 | entry_n=55, exit_n=20, sl_atr=2; ieșire E4 `combo[channel, trailing] channel_n=20, trail_start_r=2, trail_atr_mult=2, trail_min_r=0` | 361 / -0.026 | 274 | 0.28 | -0.175 | [-0.317; -0.035] | 0.746 | 60.0 | 12 | -20.6 | 1/3 ani | **REJECTED** |
| ma-trend-v1/H4 | ema_slow_n=100, sl_atr=3, trail_atr=3; ieșire E3 `trailing trail_start_r=0, trail_atr_mult=3, trail_min_r=0` | 360 / +0.019 | 271 | 0.35 | -0.113 | [-0.216; -0.004] | 0.745 | 35.2 | 8 | -13.2 | 1/3 ani | **REJECTED** |
| tsmom-v1/H4 | lookback=63, min_z=0; ieșire E3 `combo[signal_flip, trailing] trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 1132 / -0.035 | 858 | 0.30 | -0.097 | [-0.139; -0.055] | 0.683 | 98.9 | 15 | -35.9 | 0/3 ani | **REJECTED** |
| donchian-v1/H1 | entry_n=100, exit_n=20, sl_atr=3; ieșire E3 `combo[channel, trailing] channel_n=20, trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 538 / -0.094 | 434 | 0.35 | -0.223 | [-0.315; -0.132] | 0.601 | 99.0 | 9 | -73.0 | 0/3 sub-per. | **REJECTED** |
| ma-trend-v1/H1 | ema_slow_n=200, sl_atr=3, trail_atr=3; ieșire E2 `signal_flip` | 1191 / -0.064 | 924 | 0.18 | -0.066 | [-0.110; -0.024] | 0.755 | 70.9 | 30 | -46.1 | 1/3 sub-per. | **REJECTED** |
| tsmom-v1/H1 | lookback=126, min_z=0; ieșire E4 `channel channel_n=20` | 1584 / -0.054 | 1206 | 0.21 | -0.095 | [-0.143; -0.045] | 0.757 | 127.2 | 24 | -85.9 | 0/3 sub-per. | **REJECTED** |
| trend-v1/H1 | parametri live; ieșire base `base` | 3706 / -0.153 | 2833 | 0.36 | -0.128 | [-0.164; -0.092] | 0.775 | 369.4 | 14 | -274.2 | 0/3 sub-per. | referință |

Sensibilitate costuri pe TEST (avg R / PF):

| Strategie / TF | STANDARD (swap table) | swap zero | ECN (25% spread + 7 USD/lot) | cost mediu / tranzacție (R, STANDARD) | pondere swap în costuri |
|---|---|---|---|---|---|
| donchian-v1/D1 | -0.247 / 0.571 | -0.162 / 0.700 | -0.246 / 0.572 | 0.098 | 87% |
| ma-trend-v1/D1 | -0.077 / 0.727 | -0.044 / 0.838 | -0.075 / 0.734 | 0.044 | 76% |
| tsmom-v1/D1 | -0.135 / 0.585 | -0.077 / 0.746 | -0.134 / 0.588 | 0.066 | 89% |
| donchian-v1/H4 | -0.175 / 0.746 | -0.122 / 0.814 | -0.167 / 0.758 | 0.086 | 61% |
| ma-trend-v1/H4 | -0.113 / 0.745 | -0.072 / 0.832 | -0.101 / 0.769 | 0.063 | 65% |
| tsmom-v1/H4 | -0.097 / 0.683 | -0.077 / 0.742 | -0.085 / 0.715 | 0.043 | 47% |
| donchian-v1/H1 | -0.223 / 0.601 | -0.195 / 0.642 | -0.201 / 0.638 | 0.082 | 34% |
| ma-trend-v1/H1 | -0.066 / 0.755 | -0.055 / 0.793 | -0.052 / 0.798 | 0.064 | 17% |
| tsmom-v1/H1 | -0.095 / 0.757 | -0.074 / 0.805 | -0.085 / 0.779 | 0.071 | 29% |
| trend-v1/H1 | -0.128 / 0.775 | -0.116 / 0.794 | -0.098 / 0.827 | 0.107 | 12% |

Ponderea swap-ului este aproximativă: costul spread+slippage al intrării în R (spread + 1 punct față de distanța SL) comparat cu swap-ul total (tabel implicit −0.5 pip/zi pe ambele direcții, conservator; swap-ul real al brokerului poate fi pozitiv pe direcția carry).

## Perioade efective

| TF | TRAIN | TEST (după embargo de 5 bare) |
|---|---|---|
| D1 | 2013-11-26 → 2022-01-01 | 2022-01-07T22:00 → 2026-10-28T16:36 |
| H4 | 2021-05-14 → 2024-07-01 | 2024-07-01T21:00 → 2026-10-28T16:36 |
| H1 | 2023-09-20 → 2025-07-01 | 2025-07-01T05:00 → 2026-10-28T16:36 |

Începutul D1 diferă pe simbol (GBPUSD/USDJPY din 2013-11, EURGBP abia din 2016-10); fiecare simbol intră în joc după încălzirea pluginului. „Sfârșit” = momentul copiei bazei de date.

## Verificare practică de mărime (cont de 200 USD, lot minim 0.01)

Riscul în USD al UNEI tranzacții la 0.01 lot = distanța SL × tick value × 0.01, pe tranzacțiile TEST ale configului selectat (tick value din cursul istoric la momentul semnalului, contract 100 000; comparat cu tick value-urile reale din tranzacțiile demo recente: USDJPY 0.612 vs 0.633 model, AUDJPY 0.654 vs 0.633, EURGBP 1.285 vs 1.331, EURCHF 1.144 vs 1.12 USD/punct/lot — diferențe ≤ 4%; pentru CHF modelul folosește un curs fix aproximativ, USDCHF nefiind în arhivă). Capital minim = riscul la 0.01 lot ÷ 1% (respectiv ÷ 2%).

| Strategie / TF | risc 0.01 lot mediană | P90 | capital min. 1% (mediană / P90) | capital min. 2% (mediană / P90) | % tranzacții cu risc ≤ 4 USD (= 2% din 200) |
|---|---|---|---|---|---|
| donchian-v1/D1 | 13.72 USD | 22.94 USD | 1,372 / 2,294 USD | 686 / 1,147 USD | 0% |
| ma-trend-v1/D1 | 14.66 USD | 24.76 USD | 1,466 / 2,476 USD | 733 / 1,238 USD | 0% |
| tsmom-v1/D1 | 20.99 USD | 33.07 USD | 2,099 / 3,307 USD | 1,050 / 1,653 USD | 0% |
| donchian-v1/H4 | 4.62 USD | 8.03 USD | 462 / 803 USD | 231 / 402 USD | 34% |
| ma-trend-v1/H4 | 7.11 USD | 12.53 USD | 711 / 1,253 USD | 355 / 626 USD | 3% |
| tsmom-v1/H4 | 7.10 USD | 12.28 USD | 710 / 1,228 USD | 355 / 614 USD | 1% |
| donchian-v1/H1 | 2.88 USD | 4.96 USD | 288 / 496 USD | 144 / 248 USD | 79% |
| ma-trend-v1/H1 | 3.01 USD | 5.16 USD | 301 / 516 USD | 151 / 258 USD | 74% |
| tsmom-v1/H1 | 3.25 USD | 5.31 USD | 325 / 531 USD | 163 / 265 USD | 67% |
| trend-v1/H1 | 1.62 USD | 2.71 USD | 162 / 271 USD | 81 / 136 USD | 99% |

Backtest cu mărime live pe TEST (`size_mode` live, equity inițial 200 USD, risc 2%, setările de risc live din baza de date — risc total deschis max 8%, pierdere zilnică max 10%, pauză după 10 pierderi — plus `max_open_positions` 6 și filtrul de corelație; costuri STANDARD):

| Strategie / TF | tranzacții deschise | equity final | net USD | max drawdown % | semnale | respinse: lot minim > risc | % respinse |
|---|---|---|---|---|---|---|---|
| donchian-v1/D1 | 0 | 200.00 | +0.00 | 0.0% | 1,688 | 1,688 | 100% |
| ma-trend-v1/D1 | 0 | 200.00 | +0.00 | 0.0% | 317 | 317 | 100% |
| tsmom-v1/D1 | 0 | 200.00 | +0.00 | 0.0% | 13,729 | 13,729 | 100% |
| donchian-v1/H4 | 101 | 158.19 | -41.81 | 35.6% | 1,700 | 1,599 | 94% |
| ma-trend-v1/H4 | 6 | 187.60 | -12.40 | 6.2% | 854 | 848 | 99% |
| tsmom-v1/H4 | 22 | 166.83 | -33.20 | 16.6% | 38,336 | 38,314 | 100% |
| donchian-v1/H1 | 128 | 75.90 | -124.10 | 62.0% | 3,068 | 2,940 | 96% |
| ma-trend-v1/H1 | 389 | 117.21 | -82.75 | 43.9% | 1,396 | 1,002 | 72% |
| tsmom-v1/H1 | 340 | 75.48 | -124.48 | 63.0% | 60,930 | 60,557 | 99% |
| trend-v1/H1 | 565 | 38.09 | -161.84 | 82.3% | 50,141 | 49,560 | 99% |

Notă: un semnal respins nu deschide poziție, deci aceeași configurație este semnalată din nou la bara următoare (mai ales tsmom-v1, care propune intrare pe fiecare bară cât timp momentumul trece filtrul); numărul de respingeri numără bare-semnal, nu oportunități distincte. Concluzia practică: pe D1 nicio tranzacție nu încape în 2% din 200 USD; pe H4 doar ~1–34% din tranzacții; pe H1 majoritatea încap, dar exact acolo strategiile pierd cel mai mult (equity 200 → 75–117 USD).

## Testare multiplă și limite

- Configuri încercate pe TRAIN: **82** (9 celule × (grilă 8/4/6 + 3 ieșiri suplimentare) = 81, plus 1 referință trend-v1). Pe TEST s-au evaluat 9 configuri selectate (+1 referință), fiecare o singură dată, plus două sensibilități de cost care nu decid. Cu ~80 de încercări pe TRAIN, cel mai bun rezultat TRAIN este optimist prin construcție (selection bias); exemplul clar: donchian-v1 D1 avea +0.095R pe TRAIN (cel mai bun din 11) și −0.247R pe TEST.
- Cu 9 teste pe TEST, chiar și un rezultat PROMISING ar fi trebuit tratat cu prudență (la α = 10% per test, probabilitatea a cel puțin unui fals pozitiv dintre 9 strategii fără avantaj este ~60%). Aici nu s-a ajuns la această problemă: niciun rezultat nu este pozitiv.
- Pe TRAIN, doar 2 din 9 configuri selectate aveau avg R > 0 (donchian-v1 D1 +0.095, ma-trend-v1 H4 +0.019), ambele cu CI90 care include zero.
- Eșantioane: D1 ~4.8 ani TEST dar doar 156–223 tranzacții; H4 ~2.3 ani; H1 ~1.3 ani (un singur regim). Feed MetaQuotes-Demo (nu neapărat identic cu al unui broker real); swap modelat cu un tabel implicit conservator, nu cu swap-ul real per simbol; fără calendar economic; execuție pe bare (SL înainte de TP în aceeași bară); bare D1/H4 pe granițele brokerului (±1 h la schimbările DST).
- **Rezultatele din trecut nu garantează rezultatele viitoare.** Un backtest negativ arată că aceste reguli, pe acest istoric și cu aceste costuri, nu au avut un avantaj; un backtest pozitiv nu ar fi dovedit contrariul pentru viitor.

## Detalii pe strategie × timeframe

### donchian-v1/D1

TRAIN — grilă de parametri (ieșirea default a pluginului, costuri STANDARD):

| Parametri | n | avg R | CI90 | PF |
|---|---|---|---|---|
| entry_n=20, exit_n=10, sl_atr=2 | 200 | +0.059 | [-0.120; +0.244] | 1.116 |
| entry_n=20, exit_n=20, sl_atr=2 | 161 | -0.003 | [-0.242; +0.236] | 0.995 |
| entry_n=55, exit_n=10, sl_atr=2 | 143 | -0.064 | [-0.255; +0.156] | 0.884 |
| entry_n=55, exit_n=20, sl_atr=2 | 119 | -0.065 | [-0.339; +0.239] | 0.901 |
| entry_n=100, exit_n=10, sl_atr=2 | 111 | -0.137 | [-0.338; +0.070] | 0.763 |
| entry_n=100, exit_n=20, sl_atr=2 | 97 | -0.274 | [-0.524; +0.030] | 0.623 |
| entry_n=55, exit_n=20, sl_atr=3 | 103 | +0.028 | [-0.211; +0.301] | 1.057 |
| entry_n=100, exit_n=20, sl_atr=3 | 83 | -0.143 | [-0.390; +0.120] | 0.746 |

TRAIN — ieșiri pe configul cel mai bun:

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| E1-default `channel channel_n=10` | 200 | +0.059 | [-0.120; +0.244] | 1.116 | 0.34 |
| E2 `trailing trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 179 | -0.025 | [-0.193; +0.154] | 0.956 | 0.36 |
| E3 `combo[channel, trailing] channel_n=10, trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 204 | +0.055 | [-0.106; +0.222] | 1.111 | 0.37 |
| E4 `combo[channel, trailing] channel_n=10, trail_start_r=2, trail_atr_mult=2, trail_min_r=0` | 207 | +0.095 | [-0.074; +0.268] | 1.186 | 0.36 |

**Config selectat:** `strategy_name=donchian-v1`, `signal_tf=D1`, `strategy={"entry_n": 20, "exit_n": 10, "sl_atr": 2.0}`, `exit={"mode": "combo", "params": {"channel_n": 10.0, "trail_start_r": 2.0, "trail_atr_mult": 2.0, "trail_min_r": 0.0, "modes": ["channel", "trailing"]}}`. TRAIN: 207 tranzacții, avg R +0.095, CI90 [-0.074; +0.268], PF 1.186.

TEST (evaluat o singură dată):

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | net R | R/an | swap R |
|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | 156 | 0.31 | -0.247 | [-0.384; -0.105] | 0.571 | 42.1 | 9 | -38.5 | -8.0 | -13.3 |
| swap zero | 156 | 0.33 | -0.162 | [-0.304; -0.015] | 0.700 | 29.2 | 9 | -25.2 | -5.2 | +0.0 |
| ECN | 156 | 0.32 | -0.246 | [-0.384; -0.104] | 0.572 | 42.1 | 9 | -38.4 | -8.0 | -13.3 |

Pe an (STANDARD, avg R (n)): 2022: -0.372 (36) · 2023: -0.206 (26) · 2024: -0.117 (35) · 2025: -0.332 (32) · 2026: -0.186 (27)

Sub-perioade egale: 2022-01-07→2023-08-15: -0.309 (54) · 2023-08-15→2025-03-22: -0.160 (50) · 2025-03-22→2026-10-28: -0.266 (52)

| EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|
| -0.22 (7) | +0.00 (14) | -0.52 (5) | -0.36 (12) | -0.12 (15) | -0.20 (16) | -0.51 (14) | +0.02 (12) | -0.50 (20) | -0.26 (26) | -0.10 (15) |

Ieșiri (STANDARD): CHANNEL 72, SL 58, TRAIL 24, END 2.

**Decizie: REJECTED** — avg R -0.2467 <= 0; CI90 inferior -0.3838 <= -0.05; PF 0.571 <= 1.15; doar 0/5 ani pozitivi.

### ma-trend-v1/D1

TRAIN — grilă de parametri (ieșirea default a pluginului, costuri STANDARD):

| Parametri | n | avg R | CI90 | PF |
|---|---|---|---|---|
| ema_slow_n=100, sl_atr=2, trail_atr=2 | 255 | -0.017 | [-0.107; +0.083] | 0.940 |
| ema_slow_n=100, sl_atr=3, trail_atr=3 | 223 | -0.030 | [-0.102; +0.044] | 0.850 |
| ema_slow_n=200, sl_atr=2, trail_atr=2 | 208 | -0.051 | [-0.150; +0.053] | 0.823 |
| ema_slow_n=200, sl_atr=3, trail_atr=3 | 187 | -0.050 | [-0.124; +0.028] | 0.748 |

TRAIN — ieșiri pe configul cel mai bun:

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| E1-default `combo[trailing, signal_flip] trail_start_r=0, trail_atr_mult=2, trail_min_r=0` | 255 | -0.017 | [-0.107; +0.083] | 0.940 | 0.20 |
| E2 `signal_flip` | 209 | -0.033 | [-0.159; +0.113] | 0.894 | 0.17 |
| E3 `trailing trail_start_r=0, trail_atr_mult=2, trail_min_r=0` | 179 | -0.071 | [-0.211; +0.073] | 0.841 | 0.31 |
| E4 `channel channel_n=20` | 123 | -0.169 | [-0.414; +0.095] | 0.732 | 0.20 |

**Config selectat:** `strategy_name=ma-trend-v1`, `signal_tf=D1`, `strategy={"ema_slow_n": 100, "sl_atr": 2.0, "trail_atr": 2.0}`, `exit={"mode": "combo", "params": {"trail_start_r": 0.0, "trail_atr_mult": 2.0, "trail_min_r": 0.0, "flip_on_flat": 0, "modes": ["trailing", "signal_flip"]}}`. TRAIN: 255 tranzacții, avg R -0.017, CI90 [-0.107; +0.083], PF 0.940.

TEST (evaluat o singură dată):

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | net R | R/an | swap R |
|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | 202 | 0.24 | -0.077 | [-0.150; -0.000] | 0.727 | 19.7 | 12 | -15.6 | -3.2 | -6.7 |
| swap zero | 202 | 0.27 | -0.044 | [-0.120; +0.035] | 0.838 | 13.7 | 12 | -8.9 | -1.9 | +0.0 |
| ECN | 202 | 0.24 | -0.075 | [-0.148; +0.002] | 0.734 | 19.3 | 12 | -15.1 | -3.1 | -6.7 |

Pe an (STANDARD, avg R (n)): 2022: +0.024 (43) · 2023: -0.029 (45) · 2024: -0.222 (37) · 2025: -0.015 (41) · 2026: -0.181 (36)

Sub-perioade egale: 2022-01-07→2023-08-15: -0.036 (78) · 2023-08-15→2025-03-22: -0.154 (59) · 2025-03-22→2026-10-28: -0.057 (65)

| EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|
| +0.38 (16) | +0.06 (18) | -0.16 (12) | -0.17 (22) | -0.35 (25) | +0.09 (24) | -0.11 (20) | -0.17 (16) | +0.03 (14) | -0.19 (21) | -0.14 (14) |

Ieșiri (STANDARD): FLIP 122, TRAIL 76, SL 2, END 2.

**Decizie: REJECTED** — avg R -0.0772 <= 0; CI90 inferior -0.1502 <= -0.05; PF 0.727 <= 1.15; doar 1/5 ani pozitivi.

### tsmom-v1/D1

TRAIN — grilă de parametri (ieșirea default a pluginului, costuri STANDARD):

| Parametri | n | avg R | CI90 | PF |
|---|---|---|---|---|
| lookback=63, min_z=0 | 362 | -0.069 | [-0.149; +0.009] | 0.750 |
| lookback=63, min_z=0.5 | 162 | -0.187 | [-0.356; -0.007] | 0.638 |
| lookback=126, min_z=0 | 248 | -0.048 | [-0.149; +0.062] | 0.840 |
| lookback=126, min_z=0.5 | 84 | -0.147 | [-0.421; +0.185] | 0.771 |
| lookback=252, min_z=0 | 181 | -0.045 | [-0.174; +0.109] | 0.846 |
| lookback=252, min_z=0.5 | 48 | -0.039 | [-0.429; +0.398] | 0.928 |

TRAIN — ieșiri pe configul cel mai bun:

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| E1-default `signal_flip` | 181 | -0.045 | [-0.174; +0.109] | 0.846 | 0.31 |
| E2 `trailing trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 118 | -0.056 | [-0.252; +0.133] | 0.900 | 0.47 |
| E3 `combo[signal_flip, trailing] trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 250 | -0.079 | [-0.164; +0.003] | 0.759 | 0.36 |
| E4 `channel channel_n=20` | 273 | -0.031 | [-0.120; +0.055] | 0.887 | 0.21 |

**Config selectat:** `strategy_name=tsmom-v1`, `signal_tf=D1`, `strategy={"lookback": 252, "min_z": 0.0}`, `exit={"mode": "channel", "params": {"channel_n": 20.0}}`. TRAIN: 273 tranzacții, avg R -0.031, CI90 [-0.120; +0.055], PF 0.887.

TEST (evaluat o singură dată):

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | net R | R/an | swap R |
|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | 223 | 0.20 | -0.135 | [-0.211; -0.060] | 0.585 | 34.2 | 17 | -30.2 | -6.3 | -13.0 |
| swap zero | 223 | 0.21 | -0.077 | [-0.157; +0.002] | 0.746 | 21.5 | 17 | -17.2 | -3.6 | +0.0 |
| ECN | 223 | 0.20 | -0.134 | [-0.210; -0.058] | 0.588 | 33.9 | 17 | -29.9 | -6.2 | -13.0 |

Pe an (STANDARD, avg R (n)): 2022: -0.113 (34) · 2023: +0.076 (51) · 2024: -0.322 (57) · 2025: -0.177 (43) · 2026: -0.111 (38)

Sub-perioade egale: 2022-01-07→2023-08-15: -0.055 (62) · 2023-08-15→2025-03-22: -0.199 (92) · 2025-03-22→2026-10-28: -0.123 (69)

| EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|
| +0.02 (61) | -0.60 (7) | +0.19 (7) | -0.13 (25) | -0.30 (17) | -0.47 (12) | +0.22 (30) | -0.73 (1) | -0.51 (13) | -0.18 (32) | -0.46 (18) |

Ieșiri (STANDARD): CHANNEL 207, SL 13, END 3.

**Decizie: REJECTED** — avg R -0.1354 <= 0; CI90 inferior -0.2112 <= -0.05; PF 0.585 <= 1.15; doar 1/5 ani pozitivi.

### donchian-v1/H4

TRAIN — grilă de parametri (ieșirea default a pluginului, costuri STANDARD):

| Parametri | n | avg R | CI90 | PF |
|---|---|---|---|---|
| entry_n=20, exit_n=10, sl_atr=2 | 575 | -0.151 | [-0.239; -0.063] | 0.738 |
| entry_n=20, exit_n=20, sl_atr=2 | 476 | -0.212 | [-0.325; -0.097] | 0.686 |
| entry_n=55, exit_n=10, sl_atr=2 | 381 | -0.088 | [-0.196; +0.036] | 0.842 |
| entry_n=55, exit_n=20, sl_atr=2 | 318 | -0.073 | [-0.221; +0.098] | 0.883 |
| entry_n=100, exit_n=10, sl_atr=2 | 307 | -0.149 | [-0.269; -0.020] | 0.739 |
| entry_n=100, exit_n=20, sl_atr=2 | 258 | -0.201 | [-0.383; -0.023] | 0.715 |
| entry_n=55, exit_n=20, sl_atr=3 | 290 | -0.126 | [-0.243; -0.002] | 0.759 |
| entry_n=100, exit_n=20, sl_atr=3 | 228 | -0.214 | [-0.344; -0.077] | 0.638 |

TRAIN — ieșiri pe configul cel mai bun:

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| E1-default `channel channel_n=20` | 318 | -0.073 | [-0.221; +0.098] | 0.883 | 0.30 |
| E2 `trailing trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 365 | -0.080 | [-0.185; +0.038] | 0.861 | 0.35 |
| E3 `combo[channel, trailing] channel_n=20, trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 369 | -0.076 | [-0.194; +0.047] | 0.866 | 0.34 |
| E4 `combo[channel, trailing] channel_n=20, trail_start_r=2, trail_atr_mult=2, trail_min_r=0` | 361 | -0.026 | [-0.155; +0.111] | 0.959 | 0.32 |

**Config selectat:** `strategy_name=donchian-v1`, `signal_tf=H4`, `strategy={"entry_n": 55, "exit_n": 20, "sl_atr": 2.0}`, `exit={"mode": "combo", "params": {"channel_n": 20.0, "trail_start_r": 2.0, "trail_atr_mult": 2.0, "trail_min_r": 0.0, "modes": ["channel", "trailing"]}}`. TRAIN: 361 tranzacții, avg R -0.026, CI90 [-0.155; +0.111], PF 0.959.

TEST (evaluat o singură dată):

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | net R | R/an | swap R |
|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | 274 | 0.28 | -0.175 | [-0.317; -0.035] | 0.746 | 60.0 | 12 | -47.9 | -20.6 | -14.3 |
| swap zero | 274 | 0.28 | -0.122 | [-0.266; +0.020] | 0.814 | 47.0 | 12 | -33.6 | -14.4 | +0.0 |
| ECN | 273 | 0.28 | -0.167 | [-0.304; -0.026] | 0.758 | 57.6 | 11 | -45.5 | -19.6 | -14.2 |

Pe an (STANDARD, avg R (n)): 2024: +0.126 (61) · 2025: -0.300 (118) · 2026: -0.212 (95)

Sub-perioade egale: 2024-07-01→2025-04-10: -0.009 (99) · 2025-04-10→2026-01-18: -0.355 (85) · 2026-01-18→2026-10-28: -0.187 (90)

| EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|
| +0.11 (17) | -0.27 (22) | +0.21 (18) | -0.67 (23) | +0.16 (28) | -0.11 (21) | -0.35 (19) | +0.10 (22) | -0.24 (39) | -0.52 (38) | -0.04 (27) |

Ieșiri (STANDARD): SL 158, TRAIL 68, CHANNEL 47, END 1.

**Decizie: REJECTED** — avg R -0.1748 <= 0; CI90 inferior -0.3171 <= -0.05; PF 0.746 <= 1.15; doar 1/3 ani pozitivi.

### ma-trend-v1/H4

TRAIN — grilă de parametri (ieșirea default a pluginului, costuri STANDARD):

| Parametri | n | avg R | CI90 | PF |
|---|---|---|---|---|
| ema_slow_n=100, sl_atr=2, trail_atr=2 | 688 | -0.033 | [-0.094; +0.033] | 0.898 |
| ema_slow_n=100, sl_atr=3, trail_atr=3 | 607 | -0.016 | [-0.066; +0.041] | 0.931 |
| ema_slow_n=200, sl_atr=2, trail_atr=2 | 583 | -0.028 | [-0.085; +0.037] | 0.911 |
| ema_slow_n=200, sl_atr=3, trail_atr=3 | 523 | -0.027 | [-0.074; +0.023] | 0.885 |

TRAIN — ieșiri pe configul cel mai bun:

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| E1-default `combo[trailing, signal_flip] trail_start_r=0, trail_atr_mult=3, trail_min_r=0` | 607 | -0.016 | [-0.066; +0.041] | 0.931 | 0.22 |
| E2 `signal_flip` | 563 | -0.047 | [-0.096; +0.005] | 0.810 | 0.22 |
| E3 `trailing trail_start_r=0, trail_atr_mult=3, trail_min_r=0` | 360 | +0.019 | [-0.076; +0.115] | 1.048 | 0.36 |
| E4 `channel channel_n=20` | 302 | -0.041 | [-0.151; +0.071] | 0.909 | 0.31 |

**Config selectat:** `strategy_name=ma-trend-v1`, `signal_tf=H4`, `strategy={"ema_slow_n": 100, "sl_atr": 3.0, "trail_atr": 3.0}`, `exit={"mode": "trailing", "params": {"trail_start_r": 0.0, "trail_atr_mult": 3.0, "trail_min_r": 0.0}}`. TRAIN: 360 tranzacții, avg R +0.019, CI90 [-0.076; +0.115], PF 1.048.

TEST (evaluat o singură dată):

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | net R | R/an | swap R |
|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | 271 | 0.35 | -0.113 | [-0.216; -0.004] | 0.745 | 35.2 | 8 | -30.6 | -13.2 | -11.1 |
| swap zero | 271 | 0.36 | -0.072 | [-0.177; +0.037] | 0.832 | 26.8 | 8 | -19.5 | -8.4 | +0.0 |
| ECN | 270 | 0.36 | -0.101 | [-0.207; +0.013] | 0.769 | 32.9 | 8 | -27.3 | -11.7 | -11.2 |

Pe an (STANDARD, avg R (n)): 2024: +0.070 (48) · 2025: -0.218 (133) · 2026: -0.055 (90)

Sub-perioade egale: 2024-07-01→2025-04-10: -0.155 (89) · 2025-04-10→2026-01-18: -0.140 (98) · 2026-01-18→2026-10-28: -0.037 (84)

| EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|
| +0.04 (24) | -0.59 (12) | -0.04 (23) | -0.40 (25) | +0.56 (22) | -0.16 (21) | -0.16 (21) | +0.07 (20) | -0.09 (36) | -0.40 (34) | -0.11 (33) |

Ieșiri (STANDARD): TRAIL 216, SL 51, TP 2, END 2.

**Decizie: REJECTED** — avg R -0.113 <= 0; CI90 inferior -0.2164 <= -0.05; PF 0.745 <= 1.15; doar 1/3 ani pozitivi.

### tsmom-v1/H4

TRAIN — grilă de parametri (ieșirea default a pluginului, costuri STANDARD):

| Parametri | n | avg R | CI90 | PF |
|---|---|---|---|---|
| lookback=63, min_z=0 | 936 | -0.049 | [-0.098; +0.002] | 0.827 |
| lookback=63, min_z=0.5 | 384 | -0.111 | [-0.210; -0.005] | 0.770 |
| lookback=126, min_z=0 | 715 | -0.106 | [-0.168; -0.043] | 0.676 |
| lookback=126, min_z=0.5 | 248 | -0.282 | [-0.417; -0.137] | 0.565 |
| lookback=252, min_z=0 | 446 | -0.114 | [-0.197; -0.024] | 0.695 |
| lookback=252, min_z=0.5 | 164 | -0.350 | [-0.518; -0.164] | 0.522 |

TRAIN — ieșiri pe configul cel mai bun:

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| E1-default `signal_flip` | 936 | -0.049 | [-0.098; +0.002] | 0.827 | 0.29 |
| E2 `trailing trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 484 | -0.095 | [-0.186; -0.000] | 0.832 | 0.44 |
| E3 `combo[signal_flip, trailing] trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 1132 | -0.035 | [-0.076; +0.006] | 0.875 | 0.34 |
| E4 `channel channel_n=20` | 724 | -0.071 | [-0.137; -0.011] | 0.812 | 0.23 |

**Config selectat:** `strategy_name=tsmom-v1`, `signal_tf=H4`, `strategy={"lookback": 63, "min_z": 0.0}`, `exit={"mode": "combo", "params": {"flip_on_flat": 0, "trail_start_r": 1.0, "trail_atr_mult": 3.0, "trail_min_r": 0.0, "modes": ["signal_flip", "trailing"]}}`. TRAIN: 1132 tranzacții, avg R -0.035, CI90 [-0.076; +0.006], PF 0.875.

TEST (evaluat o singură dată):

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | net R | R/an | swap R |
|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | 858 | 0.30 | -0.097 | [-0.139; -0.055] | 0.683 | 98.9 | 15 | -83.5 | -35.9 | -17.4 |
| swap zero | 858 | 0.31 | -0.077 | [-0.120; -0.033] | 0.742 | 85.3 | 15 | -66.1 | -28.4 | +0.0 |
| ECN | 858 | 0.31 | -0.085 | [-0.131; -0.041] | 0.715 | 89.2 | 15 | -73.2 | -31.5 | -17.4 |

Pe an (STANDARD, avg R (n)): 2024: -0.064 (169) · 2025: -0.138 (420) · 2026: -0.055 (269)

Sub-perioade egale: 2024-07-01→2025-04-10: -0.074 (293) · 2025-04-10→2026-01-18: -0.157 (311) · 2026-01-18→2026-10-28: -0.052 (254)

| EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|
| -0.13 (52) | -0.08 (72) | +0.10 (26) | -0.22 (68) | -0.02 (84) | -0.04 (62) | -0.06 (85) | -0.09 (52) | -0.10 (70) | -0.16 (169) | -0.11 (118) |

Ieșiri (STANDARD): FLIP 615, TRAIL 143, SL 96, END 3, TP 1.

**Decizie: REJECTED** — avg R -0.0973 <= 0; CI90 inferior -0.1394 <= -0.05; PF 0.683 <= 1.15; doar 0/3 ani pozitivi.

### donchian-v1/H1

TRAIN — grilă de parametri (ieșirea default a pluginului, costuri STANDARD):

| Parametri | n | avg R | CI90 | PF |
|---|---|---|---|---|
| entry_n=20, exit_n=10, sl_atr=2 | 1304 | -0.142 | [-0.208; -0.076] | 0.763 |
| entry_n=20, exit_n=20, sl_atr=2 | 1046 | -0.152 | [-0.238; -0.063] | 0.778 |
| entry_n=55, exit_n=10, sl_atr=2 | 873 | -0.186 | [-0.265; -0.097] | 0.704 |
| entry_n=55, exit_n=20, sl_atr=2 | 723 | -0.183 | [-0.291; -0.071] | 0.746 |
| entry_n=100, exit_n=10, sl_atr=2 | 675 | -0.179 | [-0.264; -0.093] | 0.710 |
| entry_n=100, exit_n=20, sl_atr=2 | 559 | -0.196 | [-0.310; -0.070] | 0.726 |
| entry_n=55, exit_n=20, sl_atr=3 | 613 | -0.143 | [-0.230; -0.055] | 0.748 |
| entry_n=100, exit_n=20, sl_atr=3 | 469 | -0.124 | [-0.226; -0.012] | 0.784 |

TRAIN — ieșiri pe configul cel mai bun:

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| E1-default `channel channel_n=20` | 469 | -0.124 | [-0.226; -0.012] | 0.784 | 0.30 |
| E2 `trailing trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 495 | -0.107 | [-0.200; -0.013] | 0.819 | 0.42 |
| E3 `combo[channel, trailing] channel_n=20, trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 538 | -0.094 | [-0.180; -0.002] | 0.822 | 0.37 |
| E4 `combo[channel, trailing] channel_n=20, trail_start_r=2, trail_atr_mult=2, trail_min_r=0` | 520 | -0.105 | [-0.194; -0.016] | 0.815 | 0.31 |

**Config selectat:** `strategy_name=donchian-v1`, `signal_tf=H1`, `strategy={"entry_n": 100, "exit_n": 20, "sl_atr": 3.0}`, `exit={"mode": "combo", "params": {"channel_n": 20.0, "trail_start_r": 1.0, "trail_atr_mult": 3.0, "trail_min_r": 0.0, "modes": ["channel", "trailing"]}}`. TRAIN: 538 tranzacții, avg R -0.094, CI90 [-0.180; -0.002], PF 0.822.

TEST (evaluat o singură dată):

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | net R | R/an | swap R |
|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | 434 | 0.35 | -0.223 | [-0.315; -0.132] | 0.601 | 99.0 | 9 | -96.8 | -73.0 | -12.1 |
| swap zero | 434 | 0.36 | -0.195 | [-0.288; -0.102] | 0.642 | 87.3 | 9 | -84.6 | -63.8 | +0.0 |
| ECN | 426 | 0.36 | -0.201 | [-0.295; -0.112] | 0.638 | 88.2 | 9 | -85.6 | -64.5 | -12.3 |

Pe an (STANDARD, avg R (n)): 2025: -0.280 (162) · 2026: -0.189 (272)

Sub-perioade egale: 2025-07-01→2025-12-09: -0.261 (145) · 2025-12-09→2026-05-20: -0.248 (134) · 2026-05-20→2026-10-28: -0.166 (155)

| EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|
| -0.10 (31) | -0.01 (20) | -0.08 (33) | -0.13 (40) | -0.08 (50) | -0.51 (37) | -0.05 (30) | -0.10 (24) | -0.37 (55) | -0.50 (73) | -0.05 (41) |

Ieșiri (STANDARD): SL 175, TRAIL 146, CHANNEL 108, TP 3, END 2.

**Decizie: REJECTED** — avg R -0.223 <= 0; CI90 inferior -0.3146 <= -0.05; PF 0.601 <= 1.15; doar 0/3 sub-perioade pozitive.

### ma-trend-v1/H1

TRAIN — grilă de parametri (ieșirea default a pluginului, costuri STANDARD):

| Parametri | n | avg R | CI90 | PF |
|---|---|---|---|---|
| ema_slow_n=100, sl_atr=2, trail_atr=2 | 1649 | -0.102 | [-0.138; -0.062] | 0.716 |
| ema_slow_n=100, sl_atr=3, trail_atr=3 | 1500 | -0.066 | [-0.099; -0.037] | 0.743 |
| ema_slow_n=200, sl_atr=2, trail_atr=2 | 1444 | -0.100 | [-0.136; -0.060] | 0.719 |
| ema_slow_n=200, sl_atr=3, trail_atr=3 | 1316 | -0.065 | [-0.103; -0.030] | 0.748 |

TRAIN — ieșiri pe configul cel mai bun:

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| E1-default `combo[trailing, signal_flip] trail_start_r=0, trail_atr_mult=3, trail_min_r=0` | 1316 | -0.065 | [-0.103; -0.030] | 0.748 | 0.19 |
| E2 `signal_flip` | 1191 | -0.064 | [-0.104; -0.022] | 0.759 | 0.18 |
| E3 `trailing trail_start_r=0, trail_atr_mult=3, trail_min_r=0` | 744 | -0.077 | [-0.140; -0.009] | 0.823 | 0.33 |
| E4 `channel channel_n=20` | 632 | -0.117 | [-0.200; -0.029] | 0.769 | 0.27 |

**Config selectat:** `strategy_name=ma-trend-v1`, `signal_tf=H1`, `strategy={"ema_slow_n": 200, "sl_atr": 3.0, "trail_atr": 3.0}`, `exit={"mode": "signal_flip", "params": {"flip_on_flat": 0}}`. TRAIN: 1191 tranzacții, avg R -0.064, CI90 [-0.104; -0.022], PF 0.759.

TEST (evaluat o singură dată):

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | net R | R/an | swap R |
|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | 924 | 0.18 | -0.066 | [-0.110; -0.024] | 0.755 | 70.9 | 30 | -61.2 | -46.1 | -10.0 |
| swap zero | 924 | 0.19 | -0.055 | [-0.100; -0.012] | 0.793 | 64.6 | 30 | -51.2 | -38.6 | +0.0 |
| ECN | 923 | 0.19 | -0.052 | [-0.097; -0.006] | 0.798 | 61.5 | 30 | -48.2 | -36.4 | -10.0 |

Pe an (STANDARD, avg R (n)): 2025: -0.119 (365) · 2026: -0.032 (559)

Sub-perioade egale: 2025-07-01→2025-12-09: -0.119 (322) · 2025-12-09→2026-05-20: -0.080 (328) · 2026-05-20→2026-10-28: +0.012 (274)

| EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|
| -0.16 (75) | -0.07 (60) | +0.10 (78) | -0.15 (83) | +0.09 (86) | -0.12 (66) | -0.11 (97) | +0.10 (61) | -0.18 (98) | -0.16 (162) | +0.12 (58) |

Ieșiri (STANDARD): FLIP 883, SL 36, TP 4, END 1.

**Decizie: REJECTED** — avg R -0.0662 <= 0; CI90 inferior -0.1098 <= -0.05; PF 0.755 <= 1.15; doar 1/3 sub-perioade pozitive.

### tsmom-v1/H1

TRAIN — grilă de parametri (ieșirea default a pluginului, costuri STANDARD):

| Parametri | n | avg R | CI90 | PF |
|---|---|---|---|---|
| lookback=63, min_z=0 | 2151 | -0.091 | [-0.125; -0.057] | 0.705 |
| lookback=63, min_z=0.5 | 854 | -0.115 | [-0.186; -0.039] | 0.774 |
| lookback=126, min_z=0 | 1482 | -0.067 | [-0.116; -0.015] | 0.799 |
| lookback=126, min_z=0.5 | 562 | -0.099 | [-0.216; +0.019] | 0.839 |
| lookback=252, min_z=0 | 1060 | -0.088 | [-0.149; -0.023] | 0.773 |
| lookback=252, min_z=0.5 | 373 | -0.122 | [-0.276; +0.045] | 0.830 |

TRAIN — ieșiri pe configul cel mai bun:

| Ieșire | n | avg R | CI90 | PF | win |
|---|---|---|---|---|---|
| E1-default `signal_flip` | 1482 | -0.067 | [-0.116; -0.015] | 0.799 | 0.26 |
| E2 `trailing trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 1031 | -0.084 | [-0.152; -0.018] | 0.857 | 0.42 |
| E3 `combo[signal_flip, trailing] trail_start_r=1, trail_atr_mult=3, trail_min_r=0` | 1954 | -0.070 | [-0.100; -0.036] | 0.799 | 0.33 |
| E4 `channel channel_n=20` | 1584 | -0.054 | [-0.099; -0.007] | 0.856 | 0.20 |

**Config selectat:** `strategy_name=tsmom-v1`, `signal_tf=H1`, `strategy={"lookback": 126, "min_z": 0.0}`, `exit={"mode": "channel", "params": {"channel_n": 20.0}}`. TRAIN: 1584 tranzacții, avg R -0.054, CI90 [-0.099; -0.007], PF 0.856.

TEST (evaluat o singură dată):

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | net R | R/an | swap R |
|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | 1206 | 0.21 | -0.095 | [-0.143; -0.045] | 0.757 | 127.2 | 24 | -114.0 | -85.9 | -24.6 |
| swap zero | 1206 | 0.22 | -0.074 | [-0.123; -0.024] | 0.805 | 107.5 | 24 | -89.4 | -67.4 | +0.0 |
| ECN | 1203 | 0.21 | -0.085 | [-0.133; -0.034] | 0.779 | 116.0 | 24 | -101.7 | -76.7 | -24.7 |

Pe an (STANDARD, avg R (n)): 2025: -0.165 (496) · 2026: -0.045 (710)

Sub-perioade egale: 2025-07-01→2025-12-09: -0.166 (439) · 2025-12-09→2026-05-20: -0.034 (380) · 2026-05-20→2026-10-28: -0.073 (387)

| EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|
| -0.02 (132) | -0.13 (133) | +0.14 (45) | -0.31 (108) | +0.18 (80) | +0.01 (86) | -0.12 (152) | +0.15 (40) | -0.18 (102) | -0.23 (207) | -0.05 (121) |

Ieșiri (STANDARD): CHANNEL 1039, SL 157, TP 8, END 2.

**Decizie: REJECTED** — avg R -0.0945 <= 0; CI90 inferior -0.1431 <= -0.05; PF 0.757 <= 1.15; doar 0/3 sub-perioade pozitive.

### trend-v1/H1 (referință, fără căutare)

**Config selectat:** `strategy_name=trend-v1`, `signal_tf=H1`, `strategy={}`, `exit={"mode": "base", "params": {}}`. TRAIN: 3706 tranzacții, avg R -0.153, CI90 [-0.184; -0.125], PF 0.736.

TEST (evaluat o singură dată):

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | net R | R/an | swap R |
|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | 2833 | 0.36 | -0.128 | [-0.164; -0.092] | 0.775 | 369.4 | 14 | -363.7 | -274.2 | -35.5 |
| swap zero | 2833 | 0.44 | -0.116 | [-0.151; -0.080] | 0.794 | 334.1 | 11 | -328.2 | -247.4 | +0.0 |
| ECN | 2871 | 0.29 | -0.098 | [-0.135; -0.057] | 0.827 | 295.1 | 18 | -280.4 | -211.4 | -35.9 |

Pe an (STANDARD, avg R (n)): 2025: -0.159 (1123) · 2026: -0.108 (1710)

Sub-perioade egale: 2025-07-01→2025-12-09: -0.148 (986) · 2025-12-09→2026-05-20: -0.109 (944) · 2026-05-20→2026-10-28: -0.127 (903)

| EURUSD | GBPUSD | USDJPY | AUDUSD | USDCAD | EURJPY | GBPJPY | NZDUSD | EURGBP | EURCHF | AUDJPY |
|---|---|---|---|---|---|---|---|---|---|---|
| -0.10 (451) | -0.01 (202) | -0.25 (157) | -0.19 (192) | -0.04 (220) | -0.11 (238) | -0.18 (309) | -0.26 (89) | -0.19 (235) | -0.22 (438) | +0.05 (302) |

Ieșiri (STANDARD): SL 1535, TP 767, BE 529, END 2.

## Concluzie

Mutarea pe timeframe-uri mari a redus costul de spread pe tranzacție (de la ~0.05–0.15R pe M5/M15 la ~0.04–0.10R aici, din care pe D1 cea mai mare parte este swap), dar nu a scos la iveală un avantaj: toate cele 9 configuri selectate pe TRAIN sunt negative pe TEST, iar rezultatul rămâne negativ fără swap și cu costuri ECN, deci problema nu sunt costurile, ci lipsa unui avantaj al regulilor pe acest istoric (2022–2026 pentru D1, 2024–2026 pentru H4, 2025–2026 pentru H1). Trend-following-ul clasic (Donchian, medii mobile, momentum pe serie de timp) are în literatură perioade lungi fără câștig, iar un portofoliu de 11 perechi FX puternic corelate (cu filtrul de corelație care lasă puține poziții simultan) nu diversifică suficient.

Pentru contul demo de 200 USD concluzia practică este dublă: (1) nicio strategie testată nu justifică bani reali; (2) pe D1 și în mare parte pe H4 contul nu poate nici măcar să respecte un risc de 1–2% pe tranzacție la lotul minim 0.01 — ar fi nevoie de ~1 400–3 300 USD (D1, 1% risc, mediană–P90) sau ~460–1 250 USD (H4). Setările live nu au fost modificate; pluginurile C2 rămân doar pentru cercetare.

Reproducere: `nice -n 10 .venv/bin/python walkforward_c2.py train` apoi `... walkforward_c2.py test` (pe o copie read-only a bazei de date, 3 procese), raport cu `mkreport_c2.py`; rezultatele brute în `c2_results.json`.

