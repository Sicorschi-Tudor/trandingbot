# GOLD / intraday — „Botul ar merge mai bine dacă tranzacționează DOAR aurul (XAUUSD)?”

**Verdict: REJECTED la Poarta 1 (VALID).** Configurația aleasă pe TRAIN (breakout-v1 pe H1 cu filtru de trend H4, SL la partea opusă a intervalului asiatic, TP 2,5 × lățimea intervalului, time stop 24 h, **numai XAUUSD**) are pe TRAIN avg R = +0,070 (n 513, CI90 [−0,014; +0,153], PF 1,16), dar pe VALID (2019-01-01 → 2021-01-01, cont STANDARD) **n = 187, avg R = +0,009, CI90 [−0,131; +0,153], PF 1,02; 2019 −0,168 / 2020 +0,188**. Pe ECN: +0,031, CI90 [−0,109; +0,175], PF 1,07. Trece criteriile de semn (avg R > 0, n ≥ 100, un an pozitiv), dar **pică la limita inferioară CI90 > −0,02 (are −0,131) și la PF > 1,10 (are 1,02)**. Tot câștigul net de pe VALID (+1,6 R) vine din 2020 (+17,5 R din raliul aurului), care acoperă exact pierderea din 2019 (−15,8 R); ianuarie 2020 singur (+9,1 R din 5 tranzacții) valorează mai mult decât întreg rezultatul net pe 2 ani.

**Răspunsul scurt pentru proprietar:** nu. Aurul are într-adevăr mișcări mai mari, dar pe el **și spread-ul este mai mare** (30 pct = 0,30 USD la ora de bază, 66–78 pct la rollover). Pe 5,6 ani de TRAIN, 20 de configurații, strategiile intraday existente dau pe aur rezultate între −0,09 și +0,07 R pe tranzacție — ceva mai bune decât pe cele 12 instrumente (unde toate erau negative), dar niciuna nu are CI90 integral peste zero, iar cea mai bună pică pe cei 2 ani de validare la zero (+0,009 R) și e negativă în anul fără raliu. Rezultate „mai rapide” nu există: pe un singur instrument sunt **de 10–20× mai puține tranzacții** (187 în 2 ani vs 3 047 pentru 12 instrumente în F1), deci concluziile statistice sunt **mai lente și mai zgomotoase**, nu mai rapide. Mai multe poziții simultan pe același grafic nu ajută: a doua poziție pe XAUUSD este același pariu (corelație 1) — înmulțește riscul, nu avantajul; motorul (live și backtest) permite oricum o singură poziție pe simbol și **nu a fost modificat**.

Agent: `intraday` (cercetător cantitativ, sesiune Claude Code). Data: 2026-10-29. Protocol: `docs/research/STAGE_F_PROTOCOL.md` (citit înainte de orice rulare). **TEST (2021-01-01 → 2022-03-04) nu a fost atins.** Nu s-a făcut niciun commit.

## 1. Protocolul urmat și harness-ul

- Date: `data/research.db` (STANDARD, spread modelat XAUUSD 27–45 pct în orele de sesiune, 66–78 pct la 21–23 UTC) și `data/research_ecn.db` (ECN ≈ 1/5 + comision 7 USD/lot). Cost `stats`, slippage 1 pct, comision implicit al runner-ului (7 USD/lot) pe ambele baze (același harness ca F1; pe STANDARD comisionul face costurile ușor conservatoare). Swap: implicit `off` (poziții intraday, time stop 24 h).
- Perioade fixe ale runner-ului: TRAIN 2013-06-01 → 2019-01-01 (grila, ≤ 20 configurații), VALID 2019-01-01 → 2021-01-01 (o singură rulare a configurației alese, pe ambele baze).
- `--symbols XAUUSD` în toate rulările; `--workers 2`.
- `--risk-json` identic în toate rulările (cerut de sarcină): `{"max_open_positions": 1, "max_trades_per_day": 20, "max_spread_points": 100, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000, "cooldown_after_loss_minutes": 0, "rollover_blackout_minutes": 0}`. Spread-ul modelat al aurului depășește pragul live de 30 pct la multe ore (vezi mai sus), deci cu 30 filtrul ar fi blocat strategia aproape permanent; **spread-ul este plătit integral** la intrare, deci limita 100 nu face costurile mai optimiste. Filtrul „spread > 20 % din distanța SL” din `_common.finish` rămâne activ. Pentru configurația aleasă, `counts.blocked_market` = 0 pe TRAIN și pe VALID — nici `min_sl_points` 60 (= 0,60 USD pe aur, mult sub SL-ul de câțiva USD), nici filtrul de spread nu au blocat nimic.
- `--strategy-json` conține `"rescue_enabled": false` în toate rulările (ieșirea RESCUE live este un model pentru TF ≤ M5). Restul setărilor live ale pluginurilor rămân (BE la 1R cu `be_lock_r` 0,1, `min_sl_points` 60).
- Ieșiri: `base` cu `max_hold_hours` 24 (referința F1), `trailing` (1R / 1,5×ATR / 0,5R) 24 h, `combo [trailing, stall]` cu `stall_minutes` 240, 24 h. Pentru `disc-f4-sessmom` / `disc-f4-orb`: `RUN_DEFAULTS` ale pluginurilor (SL/TP + închidere la finalul sesiunii), cu sesiunea Londra 07:00–16:00 UTC ca în F4 (ora fixă de ieșire face parte din ipoteză).
- Sesiune: `{"start":"07:00","end":"17:00","tz":"UTC","days":[MON..FRI]}` pentru variantele „cu sesiune” ale trend-v1 / pullback-v1. breakout-v1 are propria fereastră (interval 00–06 UTC, intrări 06–10 UTC) și nu a primit filtru de sesiune.
- **Abatere tehnică (documentată, fără efect pe rezultate):** `research/run_bt.py` din acest checkout nu cere lista completă de tranzacții (`all_trades`), deci `by_year` din JSON acoperea doar ultimele 300 de tranzacții (= doar 2018 pe TRAIN). Cum nu am voie să modific runner-ul, toate rulările au fost lansate printr-un wrapper de 10 linii din scratchpad (`run_bt_full.py`) care importă `research/run_bt.py` **neschimbat** (aceleași perioade, aceeași blocare a TEST-ului, același `compact`) și doar setează `all_trades=True` înainte de `backtest.run`. Sumarul (n, avg R, CI90, PF, max DD) este identic cu al runner-ului (verificat: primele 7 configurații au fost rulate în ambele moduri, cifre identice); diferă doar completitudinea `by_year` și a fișierelor `.trades.jsonl`.
- Buget: **20 configurații pe TRAIN/STANDARD** (exact limita), + 3 rulări ECN pe TRAIN ale celor mai bune 3 (informative, nu sunt configurații noi), + 2 rulări VALID (STANDARD și ECN) ale configurației alese + 1 re-rulare TRAIN a configurației alese cu `--trades-out` (aceeași configurație, pentru metricile de concentrare). Jurnal complet cu comenzile exacte: `research/results/GOLD/intraday/grid_log.jsonl`; JSON-urile: `research/results/GOLD/intraday/*.json`.

## 2. Grila pe TRAIN (STANDARD), XAUUSD, 2013-06-01 → 2019-01-01 — toate cele 20 de configurații, în ordinea rulării

Proiectare (decisă înainte de rulări): (1) #1–7 fiecare plugin din scop pe H1 cu parametrii de referință din F1 (trend-v1 implicit și varianta câștigătoare F1 SL=TP=3×ATR + sesiune; breakout-v1 cu SL la partea opusă — cel mai bun prior pe XAUUSD din F1, +0,05 — cu `base` și `combo`; pullback-v1 cu/fără sesiune; meanrev-v1 implicit); (2) #8–9 cele două strategii de sesiune din F4 (sessmom câștigătoarea F4; orb cu filtru H4 — prior XAUUSD +0,03); (3) #10–14 efectul sesiunii și al politicilor de ieșire (`trailing`, `combo`) pe trend-v1, breakout-v1, pullback-v1; (4) #15–16 un slot „aur” fiecare: TP mai larg la breakout (mișcări mai mari) și `bb_k` 2,5 la meanrev (prior XAUUSD +0,05); (5) #17–18 cele mai bune 2 strategii pe M15; (6) #19–20 vecinii TP 2,0 / 3,5 ai celei mai bune (verificare de robustețe).

| # | run | strategie | TF/HTF | parametri | sesiune | ieșire | DB | n | avg R | CI90 | PF | max DD (R) | avg R pe ani (13…18) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `G01_trend_def_base24h` | trend-v1 | H1/H4 | `{}` | fără | `base {"max_hold_hours": 24}` | STD | 2884 | −0,0454 | [−0,080; −0,013] | 0,913 | 165,6 | +0,037 / −0,038 / −0,063 / −0,045 / +0,007 / −0,130 |
| 2 | `G02_trend_sl3tp3_sess_base24h` | trend-v1 | H1/H4 | `{"atr_sl_mult": 3.0, "atr_tp_mult": 3.0}` | 07–17 | `base {"max_hold_hours": 24}` | STD | 1547 | −0,0022 | [−0,038; +0,031] | 0,994 | 33,7 | +0,034 / +0,027 / −0,006 / −0,007 / +0,053 / −0,099 |
| 3 | `G03_breakout_slopp_base24h` | breakout-v1 | H1/H4 | `{"sl_mode": "opposite"}` | fără | `base {"max_hold_hours": 24}` | STD | 518 | +0,0309 | [−0,034; +0,102] | 1,071 | 17,4 | +0,070 / +0,130 / −0,018 / −0,031 / +0,199 / −0,112 |
| 4 | `G04_breakout_slopp_combo24h` | breakout-v1 | H1/H4 | `{"sl_mode": "opposite"}` | fără | `combo {"modes": ["trailing","stall"], "stall_minutes": 240, "max_hold_hours": 24}` | STD | 524 | +0,0126 | [−0,044; +0,070] | 1,043 | 20,2 | −0,044 / +0,206 / −0,067 / −0,045 / +0,111 / −0,070 |
| 5 | `G05_pullback_def_sess_base24h` | pullback-v1 | H1/H4 | `{}` | 07–17 | `base {"max_hold_hours": 24}` | STD | 355 | +0,0384 | [−0,058; +0,145] | 1,084 | 13,6 | −0,004 / +0,130 / +0,027 / −0,061 / −0,011 / +0,124 |
| 6 | `G06_pullback_def_base24h` | pullback-v1 | H1/H4 | `{}` | fără | `base {"max_hold_hours": 24}` | STD | 500 | −0,0151 | [−0,101; +0,069] | 0,968 | 38,1 | +0,016 / −0,029 / −0,022 / −0,091 / −0,103 / +0,158 |
| 7 | `G07_meanrev_def_base24h` | meanrev-v1 | H1/H4 | `{}` | fără | `base {"max_hold_hours": 24}` | STD | 150 | −0,0879 | [−0,234; +0,069] | 0,833 | 24,3 | +0,181 / +0,161 / +0,052 / −0,314 / +0,176 / −0,438 |
| 8 | `G08_sessmom_LON_k0.5_sl3_tp3` | disc-f4-sessmom | H1/NONE | `{"k_move": 0.5, "k_sl": 3.0, "k_tp": 3.0}` | 07–16 | `RUN_DEFAULTS` (SL/TP + SESSION_END) | STD | 619 | −0,0234 | [−0,071; +0,024] | 0,928 | 17,3 | −0,040 / −0,060 / +0,033 / −0,034 / −0,039 / −0,010 |
| 9 | `G09_orb_LON_rb1_opp_tp1.5_htfH4` | disc-f4-orb | H1/H4 | `{"range_bars": 1, "sl_mode": "opposite", "tp_range_mult": 1.5, "htf_filter": true}` | 07–16 | `RUN_DEFAULTS` (SL/TP + SESSION_END) | STD | 692 | +0,0110 | [−0,049; +0,068] | 1,025 | 19,4 | +0,087 / −0,083 / +0,047 / −0,069 / +0,110 / −0,018 |
| 10 | `G10_trend_sl3tp3_nosess_base24h` | trend-v1 | H1/H4 | `{"atr_sl_mult": 3.0, "atr_tp_mult": 3.0}` | fără | `base {"max_hold_hours": 24}` | STD | 1829 | −0,0178 | [−0,051; +0,013] | 0,953 | 51,0 | +0,027 / +0,016 / −0,042 / −0,033 / +0,034 / −0,085 |
| 11 | `G11_trend_sl3tp3_sess_trail24h` | trend-v1 | H1/H4 | `{"atr_sl_mult": 3.0, "atr_tp_mult": 3.0}` | 07–17 | `trailing {"max_hold_hours": 24}` | STD | 1547 | −0,0022 | [−0,038; +0,031] | 0,994 | 33,7 | +0,034 / +0,027 / −0,006 / −0,007 / +0,053 / −0,099 |
| 12 | `G12_breakout_slopp_trail24h` | breakout-v1 | H1/H4 | `{"sl_mode": "opposite"}` | fără | `trailing {"max_hold_hours": 24}` | STD | 518 | +0,0237 | [−0,042; +0,095] | 1,054 | 17,5 | +0,057 / +0,128 / −0,013 / −0,042 / +0,191 / −0,125 |
| 13 | `G13_pullback_def_sess_combo24h` | pullback-v1 | H1/H4 | `{}` | 07–17 | `combo {"modes": ["trailing","stall"], "stall_minutes": 240, "max_hold_hours": 24}` | STD | 381 | +0,0030 | [−0,083; +0,080] | 1,008 | 19,9 | +0,005 / +0,075 / +0,006 / −0,143 / +0,001 / +0,067 |
| 14 | `G14_pullback_def_sess_trail24h` | pullback-v1 | H1/H4 | `{}` | 07–17 | `trailing {"max_hold_hours": 24}` | STD | 356 | +0,0242 | [−0,066; +0,125] | 1,053 | 20,2 | +0,011 / +0,136 / +0,024 / −0,095 / −0,089 / +0,146 |
| **15** | **`G15_breakout_slopp_tp2.5_base24h`** | **breakout-v1** | **H1/H4** | **`{"sl_mode": "opposite", "tp_range_mult": 2.5}`** | fără | **`base {"max_hold_hours": 24}`** | STD | **513** | **+0,0697** | **[−0,014; +0,153]** | **1,157** | 21,4 | +0,051 / +0,075 / +0,104 / +0,057 / +0,292 / −0,129 |
| 16 | `G16_meanrev_bbk2.5_base24h` | meanrev-v1 | H1/H4 | `{"bb_k": 2.5}` | fără | `base {"max_hold_hours": 24}` | STD | 100 | −0,0201 | [−0,196; +0,160] | 0,959 | 15,7 | +0,781 / +0,249 / +0,224 / −0,232 / +0,259 / −0,519 |
| 17 | `G17_breakout_slopp_tp2.5_base24h_M15` | breakout-v1 | M15/H4 | `{"sl_mode": "opposite", "tp_range_mult": 2.5}` | fără | `base {"max_hold_hours": 24}` | STD | 587 | +0,0230 | [−0,085; +0,127] | 1,044 | 31,0 | −0,057 / −0,052 / −0,139 / +0,138 / +0,174 / +0,032 |
| 18 | `G18_pullback_def_sess_base24h_M15` | pullback-v1 | M15/H4 | `{}` | 07–17 | `base {"max_hold_hours": 24}` | STD | 1194 | −0,0647 | [−0,119; −0,008] | 0,879 | 85,0 | −0,098 / −0,042 / +0,018 / −0,054 / −0,108 / −0,124 |
| 19 | `G19_breakout_slopp_tp2.0_base24h` | breakout-v1 | H1/H4 | `{"sl_mode": "opposite", "tp_range_mult": 2.0}` | fără | `base {"max_hold_hours": 24}` | STD | 516 | +0,0566 | [−0,026; +0,134] | 1,127 | 15,6 | +0,023 / +0,063 / +0,051 / +0,021 / +0,241 / −0,059 |
| 20 | `G20_breakout_slopp_tp3.5_base24h` | breakout-v1 | H1/H4 | `{"sl_mode": "opposite", "tp_range_mult": 3.5}` | fără | `base {"max_hold_hours": 24}` | STD | 508 | +0,0660 | [−0,018; +0,157] | 1,148 | 22,8 | +0,063 / +0,063 / +0,106 / +0,027 / +0,333 / −0,159 |

Rulări ECN pe TRAIN ale celor mai bune 3 (informative):

| run | DB | n | avg R | CI90 | PF | avg R pe ani (13…18) |
|---|---|---|---|---|---|---|
| `G15_breakout_slopp_tp2.5_base24h_ECN` | ECN | 512 | +0,1185 | [+0,042; +0,204] | 1,280 | +0,077 / +0,151 / +0,171 / +0,142 / +0,327 / −0,116 |
| `G03_breakout_slopp_base24h_ECN` | ECN | 517 | +0,0693 | [+0,002; +0,136] | 1,167 | +0,100 / +0,169 / +0,049 / +0,031 / +0,214 / −0,090 |
| `G05_pullback_def_sess_base24h_ECN` | ECN | 357 | +0,0666 | [−0,037; +0,172] | 1,152 | +0,002 / +0,152 / +0,072 / −0,025 / +0,014 / +0,148 |

**Observații pe grilă**

- Pe aur singur, 11 din 20 de configurații au avg R pozitiv pe TRAIN (pe 12 instrumente, în F1/F4, 0 din 48). Dar **nicio configurație STANDARD nu are CI90 integral peste zero**; cele trei cu limita inferioară aproape de zero sunt toate breakout-v1 cu SL la partea opusă a intervalului asiatic (#15, #19, #20). Pe ECN, G15 și G03 au CI90 peste zero pe TRAIN (+0,119 / +0,069) — diferența STANDARD → ECN este ≈ +0,04–0,05 R pe tranzacție, adică exact spread-ul modelat, ca în toate familiile F.
- trend-v1 (strategia live a botului) rămâne negativă pe aur în toate cele 4 variante (−0,045 … −0,002); SL-ul mai larg (3×ATR) și sesiunea 07–17 doar diluează costul (−0,045 → −0,018 → −0,002), ca în F1. `trailing` pe trend-v1 cu TP = SL este inert (#11 identic cu #2, 1 547 tranzacții, pentru că TP la 1R coincide cu pragul de pornire a trailing-ului).
- Politicile de ieșire nu ajută nicio strategie: `trailing` și `combo` scad avg R la breakout (+0,031 → +0,024 / +0,013) și la pullback (+0,038 → +0,024 / +0,003). Ieșirea `base` cu time stop 24 h rămâne cea mai bună peste tot.
- meanrev-v1 are prea puține tranzacții pe un singur instrument (100–150 în 5,6 ani) și intervale de încredere de ±0,15–0,18 R: neinformativ.
- M15 este mai slab decât H1 pentru ambele finaliste (breakout +0,070 → +0,023; pullback +0,038 → −0,065): SL mai strâns în puncte ⇒ raport spread/SL mai mare, același fenomen ca în F1/F4.
- Vecinii TP ai configurației alese: TP 2,0 → +0,057, 2,5 → +0,070, 3,5 → +0,066 (n 508–516): un platou, nu un vârf izolat. Dar **2018 este negativ în toate variantele breakout** (−0,06 … −0,16), iar 2017 singur aduce 28,6 R din cele 35,8 R nete ale G15 (80 %).

## 3. Configurația aleasă (regula §4: cea mai mare avg R pe STANDARD/TRAIN cu n ≥ 150 pe H1/M15)

**#15 `G15`: breakout-v1, H1 cu filtru de trend H4, `sl_mode` "opposite" (SL la partea opusă a intervalului asiatic 00–06 UTC), `tp_range_mult` 2,5, intrări 06–10 UTC, ieșire `base` cu time stop 24 h, BE la 1R, fără sesiune, numai XAUUSD.** Aplicată mecanic; nu am ajustat nimic după vederea VALID-ului.

```
/tmp/claude-0/venv/bin/python research/run_bt.py --db data/research.db --strategy breakout-v1 --tf H1 --htf H4 \
  --symbols XAUUSD --split valid --workers 2 \
  --strategy-json '{"rescue_enabled": false, "sl_mode": "opposite", "tp_range_mult": 2.5}' \
  --risk-json '{"max_open_positions": 1, "max_trades_per_day": 20, "max_spread_points": 100, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000, "cooldown_after_loss_minutes": 0, "rollover_blackout_minutes": 0}' \
  --exit base --exit-params '{"max_hold_hours": 24}' \
  --json-out research/results/GOLD/intraday/VALID_G15_STANDARD.json
```
(rulată prin wrapper-ul `run_bt_full.py` descris în §1; pentru ECN: `--db data/research_ecn.db`, `VALID_G15_ECN.json`).

TRAIN (STANDARD): n 513, avg R +0,0697, CI90 [−0,014; +0,153], PF 1,157, win 48,5 %, max DD 21,4 R, net +35,8 R. Ieșiri: SL 207 (−210,1 R), TP 104 (+195,4 R), TIME 132 (+47,2 R), BE 69 (+3,6 R), END 1. Pe ani: 2013 +0,051 (50), 2014 +0,075 (78), 2015 +0,104 (91), 2016 +0,057 (79), **2017 +0,292 (98)**, **2018 −0,129 (117)**. Pe direcție: BUY +0,041 (239), SELL +0,095 (274). **Top 5 % din tranzacții (26) = 62,2 R = 24 % din câștigul brut și 1,74 × câștigul net; fără ele avg R = −0,054.**

Observație făcută înainte de VALID: ≈ 90 tranzacții/an ⇒ pe VALID erau de așteptat ≈ 180 (peste pragul n ≥ 100), dar un CI90 de ±0,14 R — adică Poarta 1 (limita inferioară > −0,02) nu putea fi trecută decât cu avg R ≳ +0,12 pe VALID, aproape dublul TRAIN-ului. Lipsa de putere statistică a unui singur instrument era previzibilă.

## 4. VALID (2019-01-01 → 2021-01-01) — o singură rulare pe fiecare bază

| Cont | n | avg R | CI90 | PF | win % | max DD (R) | net R | 2019 (n / avg R / sum) | 2020 (n / avg R / sum) | ieșiri |
|---|---|---|---|---|---|---|---|---|---|---|
| **STANDARD** | **187** | **+0,0088** | **[−0,131; +0,153]** | **1,019** | 49,7 | 18,5 | +1,6 | 94 / **−0,168** / −15,8 | 93 / **+0,188** / +17,5 | SL 79, TIME 41, TP 35, BE 32 |
| ECN | 187 | +0,0311 | [−0,109; +0,175] | 1,069 | 50,3 | 15,4 | +5,8 | 94 / −0,134 / −12,6 | 93 / +0,198 / +18,4 | SL 77, TIME 43, TP 35, BE 32 |

**Poarta 1 (STANDARD):** avg R > 0 DA (+0,009) · CI90 inferior > −0,02 NU (**−0,131**) · PF > 1,10 NU (**1,02**) · n ≥ 100 DA (187) · un an pozitiv DA (2020). **→ REJECTED.** ECN pică la fel (CI90 inferior −0,109, PF 1,07) — nu este nici `ECN_ONLY`.

**Dependența de 2020 (raliul aurului):** întregul rezultat net pozitiv vine din 2020. 2019 este clar negativ (−0,168 R, PF 0,67, n 94), 2020 clar pozitiv (+0,188, PF 1,46, n 93). Luna ianuarie 2020 singură: +9,1 R din 5 tranzacții = 5,5 × netul pe doi ani. Fără 2020, strategia pierde ≈ 16 R în 94 de tranzacții; fără ianuarie 2020, VALID-ul întreg este negativ (−7,5 R). **Top 5 % din tranzacții (9) = 22,7 R = 26 % din câștigul brut și 13,7 × câștigul net; fără ele avg R = −0,118.** Direcția s-a inversat față de TRAIN: pe TRAIN SELL era mai bun (+0,095 vs +0,041), pe VALID BUY +0,167 (114) / SELL **−0,238** (73) — câștigul este „a fi long pe aur în 2020”, nu un avantaj al breakout-ului.

**Ora din zi (intrări UTC, umplere la deschiderea barei după decizie):** breakout-v1 decide pe barele 06–09 UTC și umple la 07/08/09/10 UTC (fereastra de intrare 06–10 UTC, interval 00–06 UTC; orele sunt fixe UTC, fără ajustare DST, deci deschiderea Londrei cade la 07 sau 08 UTC în funcție de sezon). TRAIN: 07h +0,110 (203), 08h 0,000 (153), 09h +0,048 (100), 10h +0,151 (57). VALID STANDARD: 07h +0,113 (71), 08h +0,015 (55), **09h −0,181 (46)**, 10h +0,074 (15); în 2019 toate orele 07–09 sunt negative, în 2020 07h/08h +0,31 și 09h/10h ≈ 0. Spread-ul modelat la aceste ore (28,5–30 pct = 0,29–0,30 USD) este 2–6 % din SL (lățimea intervalului asiatic, tipic 5–15 USD), deci costul nu este cauza; ora „bună” (07) nu e stabilă pe ani și nu justifică un filtru post-hoc (ar fi a 21-a configurație, aleasă după VALID).

## 5. Răspuns la întrebarea proprietarului

1. **„Aurul are mișcări mai mari ⇒ rezultate mai mari?”** Mișcările sunt mai mari în USD, dar R se măsoară față de SL, care crește proporțional cu volatilitatea; ce contează este raportul cost/SL, iar spread-ul aurului (30 pct = 0,30 USD, ×2,2–2,6 la rollover) este și el mai mare. Pe TRAIN, pluginurile intraday dau pe aur rezultate ușor mai bune decât pe medie (11/20 pozitive față de 0/48 pe 12 instrumente), **dar niciuna semnificativ pozitivă**, iar pe VALID cea mai bună dă +0,009 R (≈ zero), negativă în anul fără raliu.
2. **„Mai rapide / mai corecte?”** Invers. Un singur instrument înseamnă ≈ 90 tranzacții/an (față de ≈ 1 500/an pe 12 instrumente la aceeași strategie): intervalele de încredere sunt de 3–4× mai largi, iar un an bun al aurului (2017, 2020) sau un an prost (2018, 2019) decide semnul. Cu n = 187 pe VALID, nici +0,009 nu este distins de −0,13 sau +0,15.
3. **„Mai multe tranzacții simultan pe același grafic?”** A doua poziție pe XAUUSD în aceeași direcție este același pariu (corelație 1): dublează pierderea așteptată (negativă sau zero) și volatilitatea, nu creează avantaj. Motorul permite o poziție pe simbol; nu a fost modificat și nu recomand modificarea.
4. **Singurul semnal interesant** (nu dovadă): breakout-v1 cu SL la partea opusă a intervalului asiatic este pe aur, pe TRAIN, pozitiv în 5 din 6 ani și pe ECN cu CI90 peste zero (+0,119 R). A picat pe VALID din cauza lui 2019 (−0,168). Dacă ideea se reia, trebuie pre-înregistrată ca ipoteză nouă (ex. doar BUY? doar 07–08 UTC?) — **nu** pe baza acestor numere, care sunt deja „văzute”.

## 6. Caveats

- **Selecție pe univers:** alegerea aurului s-a făcut pentru că F1/F2/F6 l-au arătat ca instrumentul cel mai „bun” din 12 — exact forma de selecție pe care VALID trebuie s-o verifice; rezultatul VALID (≈ 0) este consistent cu un efect de selecție pe TRAIN.
- **Putere statistică:** CI90 ±0,14 R pe VALID; Poarta 1 nu putea fi trecută decât cu un avg R de ≈ +0,12, ceea ce un singur instrument intraday nu a arătat nicăieri în etapele C–F.
- **Concentrare:** 2017 = 80 % din netul TRAIN; 2020 = 100 % + din netul VALID; top 5 % din tranzacții = 1,7× (TRAIN) și 13,7× (VALID) netul. Rezultatul este „câteva mișcări mari ale aurului prinse întâmplător”, nu o distribuție stabilă.
- **Costuri modelate**, nu istorice: spread-ul aurului variază mult între brokeri (20–50 pct STANDARD) și crește brusc la știri (NFP, FOMC), care cad exact în fereastra 12:30–18:00 UTC când pozițiile deschise la 07–10 UTC sunt încă deschise (time stop 24 h) — nu există calendar economic în backtest, deci rezultatele intraday sunt, dacă ceva, optimiste.
- `max_spread_points` 100 în loc de 30 (live): cu 30, filtrul live ar fi blocat majoritatea intrărilor pe aur (spread modelat 28,5–30 la 07–10 UTC, exact la prag). Pentru live, pe aur, pragul de spread ar trebui oricum exprimat relativ la SL, nu absolut în puncte.
- Comisionul de 7 USD/lot a fost aplicat și pe STANDARD (implicitul runner-ului, ca în F1): pe un SL de ≈ 10 USD cu 0,03 loturi, ≈ 0,02 R pe tranzacție — mic, dar face STANDARD ușor conservator.
- Fără calendar economic, fără ajustare DST a orelor de sesiune (fix UTC), fără goluri de weekend modelate explicit (breakout-ul de luni folosește intervalul 00–06 UTC de luni, nu golul).
- TRAIN a fost rulat cu `all_trades` prin wrapper (vezi §1); cifrele de sumar sunt identice cu ale runner-ului.

## 7. Fișiere

- `research/results/GOLD/intraday/grid_log.jsonl` — jurnalul tuturor rulărilor (comanda exactă, rezultat, durată).
- `research/results/GOLD/intraday/G01…G20*.json` — grila TRAIN (STANDARD) + `*_ECN.json` pentru top 3.
- `research/results/GOLD/intraday/G15_breakout_slopp_tp2.5_base24h_trades.json` + `.trades.jsonl` — configurația aleasă pe TRAIN cu lista completă de tranzacții.
- `research/results/GOLD/intraday/VALID_G15_STANDARD.json` / `.trades.jsonl`, `VALID_G15_ECN.json` / `.trades.jsonl` — cele două rulări VALID.
- Timp total de calcul: ≈ 4 minute (H1 3–13 s/configurație, M15 10–25 s).
