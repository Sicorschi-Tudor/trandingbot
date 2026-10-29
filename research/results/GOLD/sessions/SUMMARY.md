# GOLD / agent „sessions” — ipotezele de sesiune și oră din zi, DOAR XAUUSD

**Întrebarea proprietarului:** „aurul are mișcări mai mari — ar merge botul mai bine dacă tranzacționează DOAR XAUUSD? Rezultate mai rapide, mai mari și mai corecte dacă analizezi un singur grafic?”

**Răspuns scurt: NU.** Cele 10 ipoteze de sesiune / oră din zi (fix Londra 16:00, NR7, zi de expansiune, sweep PDH/PDL, sweep PWH/PWL, fade închideri consecutive, numere rotunde la 10 USD, fakey, fade spike, squaring de vineri) rulate **doar pe aur**, pe 5,6 ani de TRAIN, dau **8 din 10 negative** (6 cu CI90 integral sub zero), iar cele două „pozitive” sunt +0,07 R pe 48 de tranzacții și +0,006 R pe 128 — zgomot. Configurația aleasă mecanic după regula pre-înregistrată (disc-H07, zi de expansiune pe D1, `m` 1,5, ținere 2 zile — a 12-a din 20) are pe VALID (2019–2020, cont STANDARD) **n = 39, avg R −0,061, CI90 [−0,378; +0,293], PF 0,89**: pică Poarta 1 la **toate** criteriile (avg R < 0, n < 40, CI90 inferior −0,38, PF < 1,10). Pe ECN e +0,085 (PF 1,19) tot pe 39 de tranzacții cu CI90 [−0,25; +0,46] — nu e `ECN_ONLY` (n și CI90 pică). **Verdict: REJECTED.** Un singur grafic nu dă rezultate „mai rapide și mai corecte”: dă de 12 ori **mai puține** tranzacții, deci intervale de încredere de 3–4 ori mai largi și concluzii **mai puțin** sigure, nu mai sigure. Pe aur costul modelat (30 pct = 0,30 USD, ×2,2–2,6 la rollover) mănâncă exact acelaşi tip de avantaj mic ca pe FX.

Toate rulările: `research/results/GOLD/sessions/*.json` (TRAIN 20 configurații STANDARD + config aleasă pe ECN; VALID o singură dată, STANDARD + ECN), listele complete de tranzacții `*.trades.jsonl`, jurnalul `_runlog.txt`, grila pre-înregistrată `_grid.sh`, wrapper-ul `_run_full.py`. Protocolul (`docs/research/STAGE_F_PROTOCOL.md`) nu a fost modificat; TEST nu a fost atins (fără `--allow-test`, fără `--start/--end`). Nimic comis în git.

## 1. Protocol urmat (și abaterile, explicit)

- Date: `data/research.db` (STANDARD, aur ≈ 27–33 pct în Londra/NY, 39–42 în Asia, 66–78 la rollover 21–23 UTC) și `data/research_ecn.db` (ECN ≈ 12 pct + 7 USD/lot). Bare UTC. TRAIN 2013-06-01 → 2019-01-01, VALID 2019-01-01 → 2021-01-01. **Un singur simbol: `--symbols XAUUSD`**; `--swap-mode table`, `--workers 2`.
- Risc identic pentru toate rulările (decis înainte): `{"max_open_positions": 1, "max_trades_per_day": 20, "max_spread_points": 100, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000, "cooldown_after_loss_minutes": 0, "rollover_blackout_minutes": 0, "max_total_open_risk_pct": 100}` — protecțiile de cont dezactivate, pragul de spread ridicat la 100 (spread-ul **se plătește integral**; consecința pentru live e în §6).
- TF/HTF: `RUN_DEFAULTS` al fiecărui modul (runner-ul le trimite explicit prin `--tf/--htf`); ieșirea = `default_exit()` al pluginului. Hook-urile `time_exit`/`exit_deadline` din `_disc0/_disc3` **nu sunt armate** de `app.backtest` (limitare cunoscută din F3 §1), deci am folosit aceleași emulări ca F3: H05 → cădere `max_hold` 2 zile; H09 → `max_hold` 5 zile (mai lung decât „vineri la închidere”); H17 → `max_hold` 480 min (echivalent); **H08** → `--session-json 07:00–20:00 UTC L–V` + `{"close_at_session_end": true}`.
- **Plugin nou (esențial): `disc-gold-h17`** (`app/strategies/discovery/disc_gold_h17.py`, test `tests/test_gold_sessions.py`, 3 teste). `disc-H17` derivă pasul nivelurilor rotunde din point/digits: pe XAUUSD (digits 2) „00” = **1,00 USD**, „50” = 0,50 USD — zgomot pe un activ la 1 200–2 000 USD. Noul modul e același algoritm cu parametrul `step_price` în unități de preț (rulat cu **10 USD**; 50/100 nu au fost încercate — grila era limitată la 20). Fără alte fișiere noi; fără modificări la fișiere partajate.
- **Lista completă de tranzacții:** `run_bt.py` nu cere `all_trades`, deci `by_year`/listele erau trunchiate la ultimele 300 pentru n > 300. Am rulat prin `_run_full.py` (import `run_bt`, injectează `all_trades=True`; metricile agregate sunt identice — verificat pe cele 10 configurații din runda 1, rulate o dată fără și o dată cu wrapper-ul: aceleași n/avg R/CI90/PF). Nicio editare a runner-ului.
- Grila: **exact 20 de configurații** pe TRAIN/STANDARD. Runda 1 (10) = cele 10 ipoteze la parametrii impliciți, scrise în `_grid.sh` înainte de rulare. Runda 2 (10) **declarată după runda 1 și înainte de a o rula** (în același `_grid.sh`): 5 vecini ai H07 (cea mai mare avg R, dar n 48 < 60 → ineligibilă: `m` 1,5; `m` 1,5 + ținere 2 zile; ținere 2; ținere 3; `clv` 0,7), 3 vecini ai H15 (+0,006, n 128 < 150: `k` 0,75; `k` 0,5; intrare 15:00) și 2 ai h08 (cea mai bună **eligibilă** din runda 1: TP 2R; sweep ≥ 0,1×ATRd).
- Selecție (protocol §4): max `avg_r` pe STANDARD/TRAIN dintre cele cu **n ≥ 150 (H1) / ≥ 60 (H4, D1)**. VALID: **o singură rulare** a configurației alese, STANDARD + ECN. Nicio re-rulare. (O comandă shell greșită — JSON-ul de risc gol — a căzut la parsarea argumentelor **înainte** de orice backtest; nicio evaluare VALID nu a avut loc atunci; e în `_runlog.txt`.)

## 2. Grila pe TRAIN (XAUUSD, STANDARD, 2013-06 → 2019-01) — toate cele 20 de configurații

„elig.” = n ≥ 150 (H1) / ≥ 60 (H4, D1). Pe ani: avg R (n).

| # | id | Plugin | TF/HTF | Parametri (restul impliciți) | n | avg R | CI90 | PF | max DD (R) | elig. | Pe ani 13 / 14 / 15 / 16 / 17 / 18 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | S01 | disc-h02 | H1/D1 | implicit; reversal fix 16:00 Londra | 487 | −0,048 | [−0,085; −0,011] | 0,79 | 31,6 | da | −0,03 / −0,10 / −0,02 / −0,16 / −0,02 / +0,01 |
| 2 | S02 | disc-H05 | H1/D1 | implicit; NR7 inside-day | 82 | −0,084 | [−0,265; +0,102] | 0,83 | 12,9 | nu | +0,07 / +0,08 / −0,00 / −0,04 / −0,73 / −0,11 |
| 3 | S03 | disc-H07 | D1/NONE | implicit; zi de expansiune `m` 1,8, ținere 1 zi | 48 | +0,065 | [−0,110; +0,235] | 1,27 | 3,4 | nu | −0,04 / −0,13 / +0,11 / +0,65 / +0,03 / −0,10 |
| 4 | S04 | disc-h08 | H1/D1 | sweep PDH/PDL, TP mijloc, sesiune 07–20 + închidere la 20:00 | 859 | −0,045 | [−0,093; +0,007] | 0,89 | 56,7 | da | +0,16 / −0,15 / −0,01 / −0,02 / −0,13 / −0,02 |
| 5 | S05 | disc-H09 | H4/D1 | implicit; sweep PWH/PWL | 214 | −0,063 | [−0,182; +0,058] | 0,88 | 26,3 | da | −0,20 / +0,22 / −0,10 / −0,01 / −0,33 / −0,01 |
| 6 | S06 | disc-H11 | H4/NONE | implicit; fade 4 închideri consecutive | 427 | −0,072 | [−0,116; −0,032] | 0,70 | 31,6 | da | −0,09 / −0,06 / +0,02 / −0,04 / −0,10 / −0,16 |
| 7 | S07 | **disc-gold-h17** | H1/NONE | numere rotunde **pas 10 USD**, TP 1,5R, 8 bare | 429 | −0,131 | [−0,220; −0,052] | 0,76 | 60,2 | da | −0,12 / −0,33 / −0,16 / +0,05 / −0,10 / −0,15 |
| 8 | S08 | disc-h18 | H4/NONE | implicit; fakey | 519 | −0,099 | [−0,189; −0,002] | 0,84 | 65,0 | da | −0,01 / −0,22 / +0,04 / −0,14 / −0,11 / −0,17 |
| 9 | S09 | disc-h20 | H1/NONE | implicit; fade spike 2,5×ATR | 184 | −0,110 | [−0,194; −0,025] | 0,70 | 20,9 | da | −0,03 / −0,24 / +0,12 / −0,17 / −0,23 / −0,13 |
| 10 | S10 | disc-H15 | H1/D1 | implicit; squaring vineri 13:00, `k` 1,0 | 128 | +0,006 | [−0,077; +0,091] | 1,02 | 4,7 | nu | +0,01 / +0,02 / −0,02 / −0,04 / −0,01 / +0,11 |
| 11 | S11 | disc-H07 | D1/NONE | `m` 1,5 | 116 | −0,024 | [−0,144; +0,098] | 0,93 | 10,6 | da | −0,16 / −0,14 / −0,20 / +0,39 / +0,09 / −0,18 |
| **12** | **S12** | **disc-H07** | **D1/NONE** | **`m` 1,5, ținere 2 zile** | **111** | **+0,037** | **[−0,119; +0,199]** | **1,09** | **10,5** | **da — ALEASĂ** | **−0,39 / −0,04 / −0,14 / +0,44 / +0,31 / −0,05** |
| 13 | S13 | disc-H07 | D1/NONE | ținere 2 zile | 47 | +0,059 | [−0,144; +0,278] | 1,18 | 6,2 | nu | −0,46 / −0,29 / +0,42 / +0,57 / +0,19 / +0,14 |
| 14 | S14 | disc-H07 | D1/NONE | ținere 3 zile | 45 | +0,225 | [−0,054; +0,514] | 1,58 | 9,1 | nu | −0,29 / −0,60 / +1,02 / +1,00 / +0,66 / +0,37 |
| 15 | S15 | disc-H07 | D1/NONE | `clv` 0,7 | 57 | −0,036 | [−0,194; +0,135] | 0,88 | 4,7 | nu | −0,18 / −0,18 / +0,20 / +0,30 / +0,03 / −0,24 |
| 16 | S16 | disc-H15 | H1/D1 | `k` 0,75 | 164 | +0,012 | [−0,059; +0,089] | 1,05 | 5,1 | da | +0,06 / +0,01 / −0,02 / −0,01 / −0,02 / +0,09 |
| 17 | S17 | disc-H15 | H1/D1 | `k` 0,5 | 203 | −0,028 | [−0,093; +0,038] | 0,89 | 8,8 | da | +0,04 / +0,01 / −0,06 / −0,06 / −0,08 / +0,04 |
| 18 | S18 | disc-H15 | H1/D1 | intrare 15:00 | 133 | −0,040 | [−0,105; +0,024] | 0,80 | 9,0 | nu | +0,10 / −0,08 / −0,03 / −0,17 / −0,09 / +0,11 |
| 19 | S19 | disc-h08 | H1/D1 | ca S04, TP 2R | 862 | −0,042 | [−0,099; +0,015] | 0,91 | 56,2 | da | +0,11 / −0,13 / −0,09 / +0,03 / −0,08 / −0,02 |
| 20 | S20 | disc-h08 | H1/D1 | ca S04, sweep ≥ 0,1×ATRd | 582 | −0,036 | [−0,092; +0,017] | 0,89 | 36,6 | da | +0,15 / −0,19 / −0,06 / −0,05 / +0,03 / −0,03 |

Fișiere: `train_<id>.json`, `train_<id>.trades.jsonl`.

Observații pe TRAIN:
- **Prioritățile din F3 (rândul XAUUSD din rulările pe 12 instrumente) se confirmă** cu eșantion singular mai mare: H07 era +0,10 (44) → aici +0,065 (48); H09 +0,007 (71) → −0,063 (214); toate celelalte negative acolo și aici. Singurul simbol nu schimbă semnul niciunei ipoteze.
- **Numerele rotunde la 10 USD pe aur** (ipoteza „corectată” pentru metal) sunt cea mai proastă configurație eligibilă: −0,131 R pe 429 de tranzacții, CI90 integral negativ, 5 ani din 6 negativi. Prima atingere a nivelurilor xx10/xx20 nu este respinsă sistematic pe aur.
- **Fix-ul de la 16:00 Londra** (h02): −0,048 pe 487, CI90 sub zero. Intrările la ora 15 UTC (iarnă) sunt ≈ 0 (+0,013, n 295), cele la 16 UTC (vară) −0,14 (n 192) — diferență de sezon, nu avantaj.
- **H07 (zi de expansiune) este singura familie cu avg R pozitiv, dar instabil:** `m` 1,8 → +0,065 (48); `m` 1,5 → −0,024 (116); `m` 1,5 + 2 zile → +0,037 (111); 1,8 + 2 zile → +0,059 (47); 1,8 + 3 zile → +0,225 (45); `clv` 0,7 → −0,036 (57). Vecinii la ±20 % schimbă semnul; configurațiile cu avg R mare au 45–48 de tranzacții în 5,6 ani (≈ 8/an). Rezultatul vine din **2016–2017** (toate cele 6 variante pozitive atunci, 2013–2015 negative în toate).
- **H15 (squaring vineri):** +0,006 / +0,012 / −0,028 / −0,040 — toate în banda ±0,04 cu CI90 care include zero: efect nul după costuri.
- **h08 (sweep PDH/PDL):** −0,036 … −0,045 pe 582–862 de tranzacții în toate 3 variantele; schimbarea TP sau a pragului de sweep mută doar costul.

## 3. Configurația aleasă și de ce

Regula §4: max `avg_r` pe TRAIN/STANDARD cu n ≥ 60 (D1) / 150 (H1). Eligibile pozitive: **S12 +0,037 (n 111)** și S16 +0,012 (n 164). → **S12: `disc-H07`, D1, XAUUSD, `h07_m` 1,5, `h07_hold_days` 2 (restul implicit: ATR20, `clv` 0,8, TP plasă 20×ATR), ieșire implicită `signal_flip(flip_on_flat=1)` = închiderea celei de-a 2-a bare D1, SL la mijlocul barei semnal, swap `table`.**

TRAIN STANDARD: n 111, avg R +0,037, CI90 [−0,119; +0,199], PF 1,09, win 46 %, max DD 10,5 R, net +4,1 R (swap −0,5 R); 56 BUY (+0,004) / 55 SELL (+0,070); 73 ieșiri la termen (FLIP), 38 SL; ≈ 20 tranzacții/an. TRAIN ECN: +0,069, CI90 [−0,091; +0,237], PF 1,18.

Aleasă **mecanic**, cu următoarele semnalate **înainte** de VALID:
- **Pe ani (TRAIN):** 2013 −0,39 (15), 2014 −0,04 (24), 2015 −0,14 (18), **2016 +0,44 (19), 2017 +0,31 (19)**, 2018 −0,05 (16). Doi ani din șase pozitivi aduc +14,2 R; ceilalți patru −10,2 R.
- **Concentrare:** top 5 % din tranzacții (6 din 111) = **+15,7 R = 386 % din netul de +4,1 R**; mediana tranzacției **−0,03 R**. Fără cele 6 tranzacții, avg R = −0,11.
- **Toate intrările sunt la 21:00/22:00 UTC** (deschiderea barei D1 = rollover-ul New York), unde spread-ul STANDARD modelat este **66–78 pct** (0,66–0,78 USD), de 2,2–2,6× cel din Londra. Pragul live al botului e 30 pct → **un bot live cu setările actuale nu ar fi deschis niciuna din aceste tranzacții** (vezi §6). Diferența STANDARD → ECN (+0,03 R pe TRAIN, +0,15 R pe VALID) este tocmai acest spread.
- Frecvența ≈ 20/an ⇒ ≈ 40 pe VALID — chiar la pragul n ≥ 40 al Porții 1 (regula nu are criteriu de frecvență; nu l-am adăugat după vederea grilei).

Comanda exactă (VALID STANDARD; ECN = `--db data/research_ecn.db`):
```
/tmp/claude-0/venv/bin/python research/results/GOLD/sessions/_run_full.py --db data/research.db --strategy disc-H07 --tf D1 --htf NONE \
  --symbols XAUUSD --split valid --swap-mode table --workers 2 --strategy-json '{"h07_m": 1.5, "h07_hold_days": 2}' \
  --risk-json '{"max_open_positions": 1, "max_trades_per_day": 20, "max_spread_points": 100, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000, "cooldown_after_loss_minutes": 0, "rollover_blackout_minutes": 0, "max_total_open_risk_pct": 100}' \
  --json-out research/results/GOLD/sessions/valid_S12_H07_m15_hold2.json --trades-out research/results/GOLD/sessions/valid_S12_H07_m15_hold2.trades.jsonl
```
(`_run_full.py` = `research/run_bt.py` cu `all_trades=True`; argumentele sunt identice cu ale runner-ului.)

## 4. VALID (2019-01-01 → 2021-01-01) — o singură rulare

| Cont | n | avg R | CI90 | PF | win | max DD (R) | net R | 2019 | 2020 | Ieșiri |
|---|---|---|---|---|---|---|---|---|---|---|
| **STANDARD** | **39** | **−0,061** | **[−0,378; +0,293]** | **0,89** | 36 % | 7,6 | −2,4 | **+0,156 (20)** | **−0,289 (19)** | 20 FLIP / 19 SL |
| ECN | 39 | +0,085 | [−0,249; +0,459] | 1,19 | 41 % | 5,5 | +3,3 | +0,384 (20) | −0,231 (19) | 23 FLIP / 16 SL |

Fișiere: `valid_S12_H07_m15_hold2.json` (+ `_ecn`), `*.trades.jsonl`.

**Poarta 1 (STANDARD):** avg R > 0 → **NU** (−0,061); CI90 inferior > −0,02 → **NU** (−0,378); PF > 1,10 → **NU** (0,89); n ≥ 40 → **NU** (39); un an pozitiv → da (2019). **Pică 4 din 5 criterii → REJECTED.** ECN: avg R și PF trec, n (39) și CI90 (−0,25) pică → **nu** este `ECN_ONLY`.

Detalii, pentru onestitate în ambele direcții:
- **Dependența de 2020:** opusă celei de la F6/GOLD-trend. Raliul aurului din 2020 **a dăunat** acestei strategii (−0,29 R pe 19 tranzacții; SELL −0,31 R pe 17 pe tot VALID-ul — zilele de expansiune în jos închise lângă minim au fost cumpărate în 2020). 2019 e pozitiv (+0,16). Semnul se inversează de la an la an, ca pe TRAIN (2016–2017 pozitivi, restul negativi).
- **Concentrare:** top 2 tranzacții = +6,9 R (4,67 R și 2,19 R) la un net de −2,4 R; **mediana tranzacției −0,52 R**; 19 din 39 ating SL complet. Profilul „loterie”: multe pierderi de −1 R, rar un TP la termen de +2…+5 R.
- **STANDARD → ECN = +0,145 R pe tranzacție**, de 4–5× diferența tipică de +0,03 de pe FX: pe D1 aur intrarea la rollover plătește 66–78 pct față de ≈ 26 pe ECN, iar SL-ul (jumătatea barei semnal, ≈ 1–1,5 % din preț) e mic față de spread. Costul contează enorm, dar nici pe ECN nu există semnificație (CI90 [−0,25; +0,46]).

## 5. Studiu de diagnostic: avg R pe ora UTC a intrării (nu e strategie)

Din listele complete ale celor 10 rulări din runda 1 (toate TF-urile, inclusiv D1, ale cărui intrări sunt doar la 21/22 UTC), **3 377 de tranzacții** pe aur, TRAIN, STANDARD. Doar orele cu n ≥ 40:

| Oră UTC | n | avg R | | Oră UTC | n | avg R |
|---|---|---|---|---|---|---|
| 01 | 179 | **−0,189** | | 12 | 81 | −0,120 |
| 02 | 133 | +0,035 | | 13 | 366 | −0,021 |
| 05 | 186 | −0,085 | | 14 | 193 | −0,100 |
| 06 | 111 | +0,003 | | 15 | 417 | +0,023 |
| 07 | 311 | +0,008 | | 16 | 300 | −0,119 |
| 08 | 114 | −0,088 | | 17 | 221 | −0,114 |
| 09 | 185 | −0,144 | | 18 | 91 | −0,191 |
| 10 | 145 | −0,150 | | 21 | 117 | −0,021 |
| 11 | 70 | **−0,345** | | 22 | 58 | +0,017 |

Pe sesiuni: Asia 00–06 UTC −0,066 (652), Londra 07–12 −0,099 (906), New York 13–20 −0,061 (1 631), rollover 21–23 −0,024 (188). **Nicio oră și nicio sesiune nu are avg R pozitiv semnificativ**; orele 02, 06, 07, 15, 22 sunt la ≈ 0 (+0,003…+0,035, cu n 58–417 → CI90 larg, include zero), iar orele 01, 09–11 și 16–18 sunt clar negative. Cu toate cele 20 de rulări (5 697 tranzacții) imaginea e identică (07 UTC: +0,028 pe 736; 09–11 UTC: −0,16…−0,31). Spread-ul STANDARD modelat pe oră (median din tranzacții): 27 pct la 09–18 UTC, 30–33 la 07–08 și 19–20, 39–42 la 03–06, **57–78 la 00–02 și 21–23**. Orele cele mai negative (09–11, 16–18) au spread-ul **cel mai mic** — deci nu costul le strică, ci lipsa de avantaj brut a semnalelor din acel interval (sesiunea Londra/NY, unde se concentrează h08, h02, h20). Diagnosticul este **post-hoc și pe aceleași date de TRAIN**, deci nu poate fi folosit pentru a construi un filtru de oră fără a pre-înregistra o ipoteză nouă (ar fi selecție pe aceleași date).

## 6. Caveats

1. **Spread-ul este modelat, nu istoric**, și tocmai la orele în care intră configurația aleasă (21–22 UTC) e cel mai incert (66–78 pct STANDARD). Cu pragul live de 30 pct **toate** cele 111 + 39 de tranzacții ar fi fost blocate de bot; rezultatul e deci o „strategie” pe care botul actual nu o poate executa, iar un prag ridicat la 100 ar lăsa în live spread-uri reale posibil și mai mari.
2. **Hook-urile de ieșire la oră fixă** (H05 sfârșitul zilei următoare, H09 vineri) nu sunt armate de `app.backtest`; emulările prin `max_hold` sunt aproximative (H09 poate ține și în săptămâna următoare). H08 a fost emulat exact prin sesiune. Nu afectează concluzia (toate negative cu CI90 larg sub zero sau n mic).
3. **Numere rotunde:** doar pasul 10 USD a fost testat (grila de 20). 50/100 USD ar da de 5–10× mai puține tranzacții (≈ 40–90 în 5,6 ani) — un studiu viitor ar putea pre-înregistra pasul 50, dar prior-ul (−0,13 pe pasul 10, −0,17 pe FX în F3) e puternic negativ.
4. **Fără calendar economic** în backtest: pe aur (NFP, FOMC, CPI) spike-urile de știri sunt frecvente; strategiile intraday sunt, dacă ceva, **optimiste** aici. Swap-ul XAUUSD e tabel implicit (−5 pct/zi/direcție), nu al brokerului.
5. **Testare multiplă:** 20 de configurații pe un singur simbol; cele 3 agenți GOLD (trend, intraday, sessions) adaugă încă ~40 pe aceleași date de TRAIN. Chiar dacă o configurație ar fi trecut Poarta 1 la limită, ar fi avut nevoie de verificarea adversarială §5 și de TEST (neatins).
6. **„Mai multe tranzacții simultan pe același grafic”** (cerința proprietarului): motorul permite o poziție per simbol, iar o a doua poziție pe acelaşi instrument e același pariu cu corelație 1 — înmulțește riscul (și drawdown-ul de 7,6–10,5 R de aici) fără să schimbe avg R. Nu s-a modificat nimic în această direcție, conform cerinței.
7. Doi dintre cei 6 ani de TRAIN (2016–2017) și unul din cei 2 de VALID (2019) poartă tot plusul configurației alese; **2020 (raliul aurului) e negativ** pentru ea. „Aurul are mișcări mari” e adevărat, dar mișcările mari sunt și **împotriva** poziției — pe un singur simbol nu există diversificare care să le medieze.

## 7. Concluzie pentru proprietar

Analiza unui singur grafic nu face rezultatele mai rapide, mai mari sau mai corecte. Pe aur singur: (i) de ~12× mai puține tranzacții ⇒ CI90 de ±0,3 R pe VALID, adică nu se poate distinge nici +0,1 de −0,1 R după 2 ani; (ii) cele 10 ipoteze de sesiune/oră au același semn negativ ca pe FX; (iii) singura familie cu plus pe TRAIN (zi de expansiune D1) e negativă pe VALID și dependentă de 2 ani din 6 și de 6 tranzacții din 111; (iv) orele de intrare ale ei sunt exact cele blocate de pragul de spread al botului live. **Verdict: REJECTED. Nimic nu se activează live.** Direcțiile cu șanse reale rămân cele din `docs/PLAN_PROFITABILITATE.md`, Etapa 2 (univers mai larg, costuri ECN măsurate, informație dincolo de preț), nu restrângerea la un singur grafic.
