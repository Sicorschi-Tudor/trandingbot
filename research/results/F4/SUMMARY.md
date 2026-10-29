# Etapa F — Familia F4: strategii de sesiune noi (`disc-f4-orb`, `disc-f4-sessmom`)

**Verdict: REJECTED (Poarta 1 — VALID), pe STANDARD și pe ECN.** Niciuna dintre cele 24 de configurații încercate pe TRAIN (5,6 ani × 12 instrumente, costuri STANDARD) nu are speranță pozitivă. Cea mai bună — `disc-f4-sessmom` pe H1, Londra (mișcarea orei 07:00–08:00 UTC ≥ 0,5×ATR, SL = TP = 3×ATR, închidere forțată la 16:00 UTC) — are pe TRAIN avg R = −0,042 (CI90 [−0,059; −0,024], PF 0,88, n = 5 084) și pe VALID avg R = −0,034 (CI90 [−0,065; −0,003], PF 0,90, n = 1 862), negativ în ambii ani (2019: −0,055; 2020: −0,013). Pe ECN: VALID −0,004 (CI90 [−0,033; +0,027], PF 0,99) — nici ECN nu trece. Spargerea intervalului de deschidere (ORB) este mai slabă decât momentumul de sesiune în toate variantele (H1 −0,064 … −0,120; M15 −0,087 … −0,113). Ca în familia F1, singurul efect sistematic din grilă este **dilutarea costului fix pe un SL mai larg** (avg R crește monoton cu lățimea SL, PF rămâne < 1), nu un avantaj.

Agent: F4 (cercetător cantitativ, sesiune Claude Code). Data: 2026-10-29. Toate rulările cu `research/run_bt.py --workers 1`; perioadele sunt cele fixate de runner (TRAIN 2013-06-01 → 2019-01-01, VALID 2019-01-01 → 2021-01-01). **TEST nu a fost atins.** Timp total de calcul ≈ 35 min (H1: 40–85 s/config; M15: 150–180 s/config; 3 rulări în paralel, fiecare cu `--workers 1`).

## 1. Protocolul urmat

- Protocol pre-înregistrat: `docs/research/STAGE_F_PROTOCOL.md` (citit înainte de orice rulare). Familia F4: `orb-v1` → implementat ca `disc-f4-orb`, `sessmom-v1` → implementat ca `disc-f4-sessmom` (pluginuri noi în `app/strategies/discovery/`, auto-descoperite, niciodată oferite motorului live / UI).
- Date: `data/research.db` (STANDARD) pentru toată grila; `data/research_ecn.db` (ECN) doar pentru cele mai bune 3 configurații pe TRAIN și pentru configurația aleasă pe VALID. Specificații: `data/research_specs.json`. Cost `stats` (spread modelat per simbol × oră UTC), slippage 1 punct, comision 7 USD/lot (runner-ul îl trece pe ambele baze), swap `off` (poziții intraday închise în aceeași zi, cel târziu la 16:00 UTC).
- Regula de selecție (protocol §4): configurația cu cel mai mare avg R pe STANDARD/TRAIN dintre cele cu ≥ 150 tranzacții; **exact 24 de configurații încercate** (limita protocolului): 10 `sessmom` H1, 10 `orb` H1, 4 `orb` M15 (sarcina permitea cel mult 6 pe M15). Rulările ECN ale celor 3 cele mai bune configurații și descompunerile pe ani ale configurației alese (§5) nu sunt configurații noi (aceeași configurație, altă bază / sub-interval), deci nu intră în numărătoare.
- VALID: **o singură rulare** a configurației alese pe fiecare bază (STANDARD + ECN). Nu a fost nevoie de re-rulare. Descompunerea pe ani (§5) = aceeași configurație rulată pe sub-intervalele 2019 și 2020 (vezi nota de la §5).
- Nicio modificare a fișierelor partajate (`app/backtest.py`, `app/strategy.py`, `app/risk.py`, `app/exits.py`, `app/labeler.py`, pluginurile existente, `app/strategies/__init__.py`, `_common.py`, `_htf.py`, `research/run_bt.py`). Fișiere noi: `app/strategies/discovery/disc_f4_orb.py`, `app/strategies/discovery/disc_f4_sessmom.py`, `tests/test_stage_f_f4.py` (14 teste: import prin registru + nelistat live, forma ieșirii identică cu trend-v1, o singură decizie pe simbol pe zi la barele UTC corecte, logica intervalului/spargerii pe bare sintetice, direcția nu depinde de cotație — fără lookahead, perioade fixe și refuzul TEST în runner, aplicarea RUN_DEFAULTS + sesiune în `backtest.resolve`, o rulare cap-coadă de 2 luni pe baza de cercetare cu verificarea orei de intrare 08:00 și a ieșirii ≤ 16:00). `pytest -q tests/test_stage_f_f4.py`: 14 passed.

### 1.1 Pluginurile (ce se testează exact)

**`disc-f4-orb`** (spargerea intervalului de deschidere): interval = max/min al primelor `range_bars` bare ale TF-ului semnal care deschid la/după `open_hour_utc` (07:00 Londra, 13:00 New York); fereastra de intrare = barele care deschid în [sfârșitul intervalului, + `trade_window_hours`); intrare pe **prima bară închisă** a ferestrei cu închiderea în afara intervalului (oricare parte); orice bară anterioară din fereastră închisă în afara intervalului încheie ziua pentru acel simbol (o intrare pe zi, fără stare — abordarea din `breakout_v1.py`). Filtre: `min_range_atr`×ATR(14) ≤ lățime ≤ `max_range_atr`×ATR(14) (ATR pe TF-ul semnal, bare închise); `htf_filter` opțional (fără spargeri contra trendului EMA50 pe HTF). SL la partea opusă a intervalului (`sl_mode` opposite) sau la mijloc (mid), plafon opțional `sl_max_atr`; TP = `tp_range_mult`×lățime de la intrare; podeaua `min_sl_points` 60 din `_common.finish`.

**`disc-f4-sessmom`** (momentum de sesiune): la închiderea primelor `move_bars` bare H1 după `open_hour_utc` (Londra: bara 07:00–08:00, decizia la 08:00 UTC), direcția = semnul (close − open) dacă |mișcarea| ≥ `k_move`×ATR(14); SL = `k_sl`×ATR, TP = `k_tp`×ATR; opțional `min_body_frac`, `htf_filter`. Exact o bară de decizie pe simbol pe zi.

Ambele: direcția se decide exclusiv pe bare închise (`sig_bars[-1]`); cotația curentă setează doar prețul de umplere și filtrul de spread (test dedicat). HTF opțional (`--htf NONE` în grilă; H4 doar pentru variantele cu `htf_filter`).

### 1.2 Harness fix pentru toate configurațiile (decis înainte de prima rulare)

- `RUN_DEFAULTS` ale pluginurilor (aplicate de `backtest.resolve` dacă rularea nu suprascrie): `max_hold_minutes 0`, `break_even_at_r 0`, `be_lock_r 0`, `rescue_enabled false`, `close_at_session_end true`. Deci ieșirea = SL / TP / **închidere forțată la finalul sesiunii** (ora fixă cerută de sarcină), fără BE, fără RESCUE (model valid doar pe TF ≤ M5), fără time stop suplimentar.
- Sesiunea (`--session-json`): Londra `{"start":"07:00","end":"16:00","tz":"UTC","days":[MON..FRI]}` → deciziile doar la închideri de bară în 07:00–16:00, poziția închisă la deschiderea barei 16:00 (`SESSION_END`); New York `13:00–20:00 UTC` (închidere la 20:00, înainte de rollover). Ora deschiderii Londrei este fixă 07:00 UTC (fără ajustare DST; vezi §7).
- Riscul (`--risk-json`): `{"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2, "max_spread_points": 60, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000}` — primele trei chei cerute de sarcină; ultimele trei exact ca în harness-ul F1 (spread 60 pentru că GBPJPY are spread modelat 32 pct la 07:00 UTC și ar fi fost blocat permanent de limita live 30; limitele de cont dezactivate ca R să măsoare strategia, nu limitele contului — precedent C2/F1). Restul setărilor live rămân (risc 0,25 %/tranzacție, cooldown 30 min după pierdere, blackout rollover ±20 min, 5 semnale/simbol/sesiune, `min_sl_points` 60).
- Ieșirea `base` în toate rulările (nicio politică de ieșire din `app/exits.py` nu a fost încercată: bugetul de 24 a fost cheltuit pe parametrii de intrare/SL/TP ai celor două strategii noi).

## 2. Grila pe TRAIN (STANDARD) — toate cele 24 de configurații

Proiectare: (1) coarse — `sessmom` S01–S06 (k_move 0,5/0,8; SL 1/1,5×ATR; TP 1,5/3×ATR; New York; 2 bare de mișcare) și `orb` H1 O01–O06 (SL opus/mijloc; TP 1/1,5/2,5×interval; New York; interval de 2 bare) + prima configurație M15 (M01); (2) rafinare în jurul direcțiilor care au ajutat: `sessmom` S07–S09 (prag mai mic, SL mai larg, filtru HTF), `orb` O07–O09 (interval îngust, filtru HTF, fereastră 2 h), M15 M02–M04 (interval de 4 bare, TP 2,5); (3) ultimele două: O10 (combinarea celor două îmbunătățiri ORB) și S10 (continuarea lărgirii SL la 3×ATR). Fiecare rulare: 12 simboluri, 2013-06-01 → 2019-01-01 (~2 038 zile). Ordinea din tabel este alfabetică (ordinea rulării: S01–S06, O01–O06, M01, M02–M04, S07–S09, O07–O09, O10, S10).

| # | run | strategie | TF/HTF | parametri | sesiune | n | avg R | CI90 | PF | max DD (R) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `M01_orb_M15_LON_rb2_opp_tp1.5` | disc-f4-orb | M15/NONE | `{"open_hour_utc":7,"range_bars":2,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":3.0,"sl_mode":"opposite","tp_range_mult":1.5}` | LON | 7706 | -0.1083 | [-0.1285; -0.0885] | 0.801 | 851.4 |
| 2 | `M02_orb_M15_LON_rb4_opp_tp1.5` | disc-f4-orb | M15/NONE | `{"open_hour_utc":7,"range_bars":4,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":4.0,"sl_mode":"opposite","tp_range_mult":1.5}` | LON | 7311 | -0.0902 | [-0.1081; -0.0718] | 0.822 | 667.3 |
| 3 | `M03_orb_M15_LON_rb2_opp_tp2.5` | disc-f4-orb | M15/NONE | `{"open_hour_utc":7,"range_bars":2,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":3.0,"sl_mode":"opposite","tp_range_mult":2.5}` | LON | 7188 | -0.1132 | [-0.1361; -0.0894] | 0.817 | 824.9 |
| 4 | `M04_orb_M15_LON_rb4_opp_tp2.5` | disc-f4-orb | M15/NONE | `{"open_hour_utc":7,"range_bars":4,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":4.0,"sl_mode":"opposite","tp_range_mult":2.5}` | LON | 6891 | -0.0865 | [-0.1082; -0.0634] | 0.843 | 614.0 |
| 5 | `O01_orb_H1_LON_rb1_opp_tp1.5` | disc-f4-orb | H1/NONE | `{"open_hour_utc":7,"range_bars":1,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":3.0,"sl_mode":"opposite","tp_range_mult":1.5}` | LON | 6943 | -0.0691 | [-0.0863; -0.0526] | 0.842 | 490.8 |
| 6 | `O02_orb_H1_LON_rb1_mid_tp1.5` | disc-f4-orb | H1/NONE | `{"open_hour_utc":7,"range_bars":1,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":3.0,"sl_mode":"mid","tp_range_mult":1.5}` | LON | 7278 | -0.1078 | [-0.1294; -0.0849] | 0.812 | 793.7 |
| 7 | `O03_orb_H1_LON_rb1_opp_tp1.0` | disc-f4-orb | H1/NONE | `{"open_hour_utc":7,"range_bars":1,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":3.0,"sl_mode":"opposite","tp_range_mult":1.0}` | LON | 7528 | -0.0814 | [-0.0956; -0.0667] | 0.794 | 622.5 |
| 8 | `O04_orb_H1_LON_rb1_opp_tp2.5` | disc-f4-orb | H1/NONE | `{"open_hour_utc":7,"range_bars":1,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":3.0,"sl_mode":"opposite","tp_range_mult":2.5}` | LON | 6509 | -0.0714 | [-0.0941; -0.0518] | 0.849 | 475.7 |
| 9 | `O05_orb_H1_NY_rb1_opp_tp1.5` | disc-f4-orb | H1/NONE | `{"open_hour_utc":13,"range_bars":1,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":3.0,"sl_mode":"opposite","tp_range_mult":1.5}` | NY | 6594 | -0.1204 | [-0.1341; -0.1075] | 0.635 | 796.1 |
| 10 | `O06_orb_H1_LON_rb2_opp_tp1.5` | disc-f4-orb | H1/NONE | `{"open_hour_utc":7,"range_bars":2,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":4.0,"sl_mode":"opposite","tp_range_mult":1.5}` | LON | 6551 | -0.0732 | [-0.0906; -0.0569] | 0.807 | 481.0 |
| 11 | `O07_orb_H1_LON_rb1_opp_tp2_rmax2` | disc-f4-orb | H1/NONE | `{"open_hour_utc":7,"range_bars":1,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":2.0,"sl_mode":"opposite","tp_range_mult":2.0}` | LON | 6602 | -0.0767 | [-0.0962; -0.0570] | 0.838 | 517.3 |
| 12 | `O08_orb_H1_LON_rb1_opp_tp1.5_htfH4` | disc-f4-orb | H1/H4 | `{"open_hour_utc":7,"range_bars":1,"trade_window_hours":4,"min_range_atr":0.5,"max_range_atr":3.0,"sl_mode":"opposite","tp_range_mult":1.5,"htf_filter":true}` | LON | 5606 | -0.0653 | [-0.0843; -0.0458] | 0.852 | 368.9 |
| 13 | `O09_orb_H1_LON_rb1_opp_tp1.5_win2` | disc-f4-orb | H1/NONE | `{"open_hour_utc":7,"range_bars":1,"trade_window_hours":2,"min_range_atr":0.5,"max_range_atr":3.0,"sl_mode":"opposite","tp_range_mult":1.5}` | LON | 5814 | -0.0657 | [-0.0864; -0.0458] | 0.852 | 407.9 |
| 14 | `O10_orb_H1_LON_rb1_opp_tp1.5_win2_htfH4` | disc-f4-orb | H1/H4 | `{"open_hour_utc":7,"range_bars":1,"trade_window_hours":2,"min_range_atr":0.5,"max_range_atr":3.0,"sl_mode":"opposite","tp_range_mult":1.5,"htf_filter":true}` | LON | 4524 | -0.0644 | [-0.0865; -0.0431] | 0.857 | 293.0 |
| 15 | `S01_sessmom_LON_k0.5_sl1_tp1.5` | disc-f4-sessmom | H1/NONE | `{"open_hour_utc":7,"k_move":0.5,"k_sl":1.0,"k_tp":1.5}` | LON | 4680 | -0.1254 | [-0.1549; -0.0963] | 0.805 | 595.5 |
| 16 | `S02_sessmom_LON_k0.8_sl1_tp1.5` | disc-f4-sessmom | H1/NONE | `{"open_hour_utc":7,"k_move":0.8,"k_sl":1.0,"k_tp":1.5}` | LON | 3243 | -0.1559 | [-0.1905; -0.1227] | 0.764 | 509.7 |
| 17 | `S03_sessmom_LON_k0.5_sl1.5_tp1.5` | disc-f4-sessmom | H1/NONE | `{"open_hour_utc":7,"k_move":0.5,"k_sl":1.5,"k_tp":1.5}` | LON | 4993 | -0.1069 | [-0.1282; -0.0840] | 0.795 | 548.4 |
| 18 | `S04_sessmom_LON_k0.5_sl1_tp3` | disc-f4-sessmom | H1/NONE | `{"open_hour_utc":7,"k_move":0.5,"k_sl":1.0,"k_tp":3.0}` | LON | 4680 | -0.0920 | [-0.1267; -0.0565] | 0.871 | 452.7 |
| 19 | `S05_sessmom_NY_k0.5_sl1_tp1.5` | disc-f4-sessmom | H1/NONE | `{"open_hour_utc":13,"k_move":0.5,"k_sl":1.0,"k_tp":1.5}` | NY | 4895 | -0.1444 | [-0.1716; -0.1196] | 0.751 | 732.7 |
| 20 | `S06_sessmom_LON_mb2_k0.7_sl1_tp1.5` | disc-f4-sessmom | H1/NONE | `{"open_hour_utc":7,"move_bars":2,"k_move":0.7,"k_sl":1.0,"k_tp":1.5}` | LON | 4853 | -0.1272 | [-0.1548; -0.0977] | 0.801 | 643.0 |
| 21 | `S07_sessmom_LON_k0.3_sl1.5_tp3` | disc-f4-sessmom | H1/NONE | `{"open_hour_utc":7,"k_move":0.3,"k_sl":1.5,"k_tp":3.0}` | LON | 5564 | -0.0769 | [-0.1038; -0.0498] | 0.866 | 440.0 |
| 22 | `S08_sessmom_LON_k0.5_sl2_tp3` | disc-f4-sessmom | H1/NONE | `{"open_hour_utc":7,"k_move":0.5,"k_sl":2.0,"k_tp":3.0}` | LON | 5064 | -0.0662 | [-0.0884; -0.0427] | 0.861 | 350.5 |
| 23 | `S09_sessmom_LON_k0.5_sl1.5_tp3_htfH4` | disc-f4-sessmom | H1/H4 | `{"open_hour_utc":7,"k_move":0.5,"k_sl":1.5,"k_tp":3.0,"htf_filter":true}` | LON | 3598 | -0.0859 | [-0.1197; -0.0514] | 0.855 | 324.4 |
| 24 | `S10_sessmom_LON_k0.5_sl3_tp3` | disc-f4-sessmom | H1/NONE | `{"open_hour_utc":7,"k_move":0.5,"k_sl":3.0,"k_tp":3.0}` | LON | **5084** | **-0.0415** | **[-0.0589; -0.0237]** | **0.877** | 218.2 |

Toate rulările: `--htf NONE` cu excepția #12, #14, #23 (`--htf H4`, `htf_filter true`); ieșire `base` (SL/TP + `SESSION_END`); risc și sesiune ca în §1.2. Fișiere: `research/results/F4/<run>_train_std.json` (+ `.trades.jsonl` pentru rulările de după M01 — trunchiat la ultimele 300 tranzacții de runner, vezi §5). Jurnalul tuturor rulărilor (inclusiv comanda exactă): `research/results/F4/grid_log.jsonl`.

**Observații pe grilă**

- Toate cele 24 de configurații au avg R negativ **și limita superioară CI90 sub zero**. Niciuna nu are PF ≥ 0,88.
- `sessmom`: pragul de mișcare mai mare strică (k 0,5 → 0,8: −0,125 → −0,156; k 0,3: −0,077 vs −0,086/−0,066 la aceiași SL/TP), deci „mișcarea mare din prima oră” nu continuă mai des decât una mică — nu există un efect de momentum de sesiune peste zgomot. New York este mai slab decât Londra (−0,144 vs −0,125). Două bare de mișcare (decizia la 09:00) nu ajută (−0,127). Filtrul de trend H4 taie 28 % din tranzacții și înrăutățește (S09 −0,086 vs S03/S08 fără filtru).
- Singurul efect sistematic este **lățimea SL** (același fenomen ca în F1): la TP 3×ATR, SL 1×ATR −0,092 → 2×ATR −0,066 → 3×ATR −0,042. Cu SL 3×ATR pe H1 și închidere forțată după cel mult 8 ore, 56 % din ieșiri sunt `SESSION_END` (2 832 / 5 084), SL și TP sunt atinse aproape la fel de des (1 174 vs 1 078): strategia devine „ține direcția primei ore până la 16:00”, cu un cost fix (spread + slippage ≈ 0,04–0,07 R la SL 3×ATR) care explică integral rezultatul negativ. Nu e un avantaj care se apropie de zero, e zgomot minus cost.
- `orb`: SL la partea opusă este clar mai bun decât la mijloc (−0,069 vs −0,108: costul fix pe un SL de două ori mai larg); TP 1,5–2,5× interval ≈ egal (−0,069/−0,071), TP 1× mai slab (−0,081); interval de 2 ore ≈ 1 oră; New York mult mai slab (−0,120, PF 0,64); filtrul H4 și fereastra de 2 h aduc câte ~0,004 R (în marja de zgomot), iar combinația lor (O10) −0,064 este cea mai bună variantă ORB — tot negativă.
- M15 (interval 07:00–07:30 sau 07:00–08:00, 4 configurații din cele 6 permise) este mai slab decât H1 (−0,087 … −0,113 vs −0,064 … −0,081): SL mai strâns în puncte ⇒ raport spread/SL mai mare. Nu s-au mai cheltuit configurații pe M15 pentru că direcția era clară după 4.

### 2.1 Cele mai bune 3 configurații pe TRAIN — STANDARD vs ECN

| config | n STD | avg R STD | CI90 STD | PF STD | n ECN | avg R ECN | CI90 ECN | PF ECN |
|---|---|---|---|---|---|---|---|---|
| #24 `S10` sessmom H1 Londra, k 0,5, SL = TP = 3×ATR, închidere 16:00 | 5084 | −0,0415 | [−0,0589; −0,0237] | 0,877 | 5103 | −0,0166 | [−0,0326; −0,0000] | 0,949 |
| #14 `O10` orb H1 Londra, interval 07–08, SL opus, TP 1,5×, fereastră 2 h, filtru H4 | 4524 | −0,0644 | [−0,0865; −0,0431] | 0,857 | 4643 | −0,0249 | [−0,0485; −0,0035] | 0,943 |
| #12 `O08` orb H1 Londra, interval 07–08, SL opus, TP 1,5×, fereastră 4 h, filtru H4 | 5606 | −0,0653 | [−0,0843; −0,0458] | 0,852 | 5752 | −0,0255 | [−0,0453; −0,0064] | 0,941 |

Pe ECN (spread ≈ 1/5 + comision) rezultatul se apropie de zero, dar rămâne negativ cu limita superioară CI90 ≤ 0 la toate trei: diferența STD − ECN (≈ 0,025–0,040 R) este exact costul spread-ului, iar ceea ce rămâne pe ECN (−0,017 … −0,026) este comisionul + slippage-ul. Fără costuri, strategiile ar fi ≈ 0 R: nu există avantaj brut.

## 3. Configurația aleasă și de ce

Regula protocolului §4: cel mai mare avg R pe STANDARD/TRAIN dintre configurațiile cu ≥ 150 tranzacții (toate cele 24 au n ≥ 3 243). Aleasă: **#24 `S10_sessmom_LON_k0.5_sl3_tp3`** (avg R −0,0415; următoarea, O10, −0,0644).

Comanda exactă (fără `--split`):

```
/tmp/claude-0/venv/bin/python research/run_bt.py --db data/research.db --strategy disc-f4-sessmom --tf H1 --htf NONE \
  --strategy-json '{"open_hour_utc": 7, "k_move": 0.5, "k_sl": 3.0, "k_tp": 3.0}' \
  --risk-json '{"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2, "max_spread_points": 60, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000}' \
  --session-json '{"start": "07:00", "end": "16:00", "tz": "UTC", "days": ["MON", "TUE", "WED", "THU", "FRI"]}' --workers 1
```

(ECN: același cu `--db data/research_ecn.db`.) Ieșirea `base` implicită + `RUN_DEFAULTS` ale pluginului (fără BE, fără RESCUE, închidere la finalul sesiunii = 16:00 UTC).

## 4. VALID (2019-01-01 → 2021-01-01) — o singură rulare pe fiecare bază

| Costuri | n | win | avg R | CI90 | PF | max DD (R) | serie max pierderi | net R | ieșiri TP / SL / SESSION_END |
|---|---|---|---|---|---|---|---|---|---|
| STANDARD | 1862 | 0,490 | **−0,0341** | [−0,0648; −0,0028] | **0,903** | 84,1 | 11 | −63,4 | 449 / 473 / 940 |
| ECN | 1862 | 0,508 | −0,0038 | [−0,0326; +0,0265] | 0,989 | 65,5 | 9 | −7,1 | 475 / 450 / 937 |

Fișiere: `research/results/F4/S10_sessmom_LON_k0.5_sl3_tp3_valid_std.json`, `..._valid_ecn.json`.

Pe simbol (VALID, avg R / n):

| Costuri | AUDJPY | AUDUSD | EURCHF | EURGBP | EURJPY | EURUSD | GBPJPY | GBPUSD | USDCAD | USDCHF | USDJPY | XAUUSD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | −0,04 (76) | +0,04 (123) | −0,18 (76) | +0,07 (96) | +0,03 (231) | −0,03 (246) | −0,07 (211) | −0,01 (300) | −0,11 (65) | −0,11 (65) | −0,12 (160) | −0,02 (213) |
| ECN | +0,03 (73) | +0,06 (159) | −0,08 (72) | +0,09 (140) | +0,04 (225) | +0,01 (300) | −0,01 (162) | −0,01 (280) | −0,07 (77) | −0,04 (68) | −0,08 (173) | −0,08 (133) |

(`by_symbol` din JSON numără cel mult 300 tranzacții per simbol — GBPUSD/EURUSD sunt la plafon; valorile sunt orientative.) Niciun simbol nu are un avantaj consistent: cele pozitive pe VALID (EURGBP, AUDUSD, EURJPY) erau negative sau neutre pe TRAIN (pe ultimele 300 tranzacții TRAIN: EURGBP +0,24, dar AUDUSD −0,27).

## 5. Pe ani

`run_bt.py` calculează `by_year` din lista de tranzacții a rezultatului, pe care `app.backtest` o trunchiază la ultimele 300 (`MAX_TRADES_OUT`; `--trades-out` scrie aceeași listă trunchiată), așa că valorile `by_year` din JSON-urile principale acoperă doar coada eșantionului și **nu sunt folosite**. Tabelul de mai jos vine din rulări separate ale aceleiași configurații pe fiecare an calendaristic (`--start/--end`, fișiere în `research/results/F4/byyear/`, jurnal `log.txt`); pozițiile sunt închise zilnic la 16:00, deci sub-intervalele sunt exacte (933 + 929 = 1 862 = n VALID). Nu este o optimizare: aceeași configurație, intervale fixe.

| An | set | costuri | n | avg R | CI90 | PF | net R |
|---|---|---|---|---|---|---|---|
| 2013 (iun–dec) | TRAIN | STANDARD | 532 | −0,055 | [−0,109; −0,006] | 0,840 | −29,3 |
| 2014 | TRAIN | STANDARD | 911 | −0,063 | [−0,102; −0,025] | 0,814 | −57,3 |
| 2015 | TRAIN | STANDARD | 925 | −0,053 | [−0,095; −0,010] | 0,850 | −48,7 |
| 2016 | TRAIN | STANDARD | 900 | +0,023 | [−0,018; +0,061] | 1,075 | +20,4 |
| 2017 | TRAIN | STANDARD | 900 | −0,053 | [−0,089; −0,013] | 0,846 | −47,3 |
| 2018 | TRAIN | STANDARD | 916 | −0,054 | [−0,099; −0,012] | 0,847 | −49,0 |
| **2019** | **VALID** | **STANDARD** | 933 | **−0,055** | [−0,099; −0,015] | 0,850 | −51,1 |
| **2020** | **VALID** | **STANDARD** | 929 | **−0,013** | [−0,054; +0,030] | 0,961 | −12,3 |
| 2019 | VALID | ECN | 932 | −0,026 | [−0,066; +0,015] | 0,926 | −24,1 |
| 2020 | VALID | ECN | 930 | +0,018 | [−0,024; +0,061] | 1,056 | +16,9 |

Un singur an pozitiv din 8 pe STANDARD (2016, CI90 include zero); pe VALID ambii ani negativi pe STANDARD. Pe ECN 2020 este marginal pozitiv (+0,018, CI90 include zero), 2019 negativ.

## 6. Verdict față de Poarta 1 (protocol §4)

| Criteriu (VALID, STANDARD) | Prag | Obținut | Trece? |
|---|---|---|---|
| avg R | > 0 | −0,0341 | **NU** |
| limita inferioară CI90 | > −0,02 | −0,0648 | **NU** |
| PF | > 1,10 | 0,903 | **NU** |
| n (intraday) | ≥ 100 | 1 862 | da |
| avg R > 0 în cel puțin un an | — | 2019: −0,055; 2020: −0,013 | **NU** |

**REJECTED.** Pe ECN: avg R −0,0038 (nu), CI90 inferior −0,0326 (nu), PF 0,989 (nu), 2020 pozitiv (da) → nici `ECN_ONLY`. Nu există nicio configurație F4 care să meargă la verificarea adversarială sau la TEST. Pentru bani: nicio strategie din această familie nu merită testul forward (PAPER/DEMO).

## 7. Caveats și abateri

1. **Dilutarea costului, nu un avantaj.** Configurația „cea mai bună” a fost găsită prin lărgirea SL (1 → 3×ATR), care reduce costul fix exprimat în R; același mecanism ca în F1. În termeni absoluți (puncte), S10 pierde la fel ca celelalte variante; PF < 1 peste tot și 56 % din tranzacții se închid la ora fixă. Pe un cont real, un SL de 3×ATR(H1) pe 12 instrumente la 0,25 % risc înseamnă poziții foarte mici (sub lotul minim pe un cont mic — vezi F1/C2) — irelevant, dat fiind verdictul.
2. **Ora deschiderii Londrei fixă la 07:00 UTC** (specificația sarcinii). Iarna, Londra deschide la 08:00 UTC, deci jumătate din an „prima oră” este de fapt ora de dinaintea deschiderii. O variantă cu ajustare DST nu a fost încercată (bugetul de 24); având în vedere că nici varianta New York (13:00 UTC) nu e mai bună, nu e de așteptat o schimbare de semn.
3. **`by_year` din runner** este calculat din ultimele 300 de tranzacții (limitare în `app.backtest`, fișier partajat, neatins); `--trades-out` scrie aceeași listă trunchiată. Descompunerea pe ani a fost făcută prin rulări pe sub-intervale (§5). Fișierele `*.trades.jsonl` din dosar sunt deci parțiale (ultimele 300 tranzacții) și păstrate doar ca diagnostic.
4. **Harness.** Spread maxim 60 (vs 30 live), limitele de cont dezactivate, BE și RESCUE oprite, cooldown/blackout rollover live păstrate — decizii luate înainte de prima rulare și identice pentru toate cele 24 de configurații (consistență cu F1). Comisionul de 7 USD/lot se aplică și pe STANDARD (runner-ul îl trece pe ambele baze), deci STANDARD este, dacă ceva, conservator.
5. **Fără calendar economic** (blocările pe știri nu sunt simulate — rezultatele intraday sunt optimiste); spread modelat per oră, nu real; umplere la deschiderea barei următoare (+ spread + 1 pct slippage); SL înainte de TP în aceeași bară; zilele fără bara 07:00 sunt sărite de plugin.
6. **Testare multiplă.** 24 configurații pe TRAIN pentru două strategii; cea mai bună pe TRAIN este optimistă prin construcție — aici irelevant, pentru că toate sunt negative cu CI90 superior < 0, iar VALID confirmă semnul.
7. **Politici de ieșire** din `app/exits.py` (trailing, stall, giveback etc.) nu au fost încercate pentru F4: bugetul a fost cheltuit pe parametrii de intrare. F1 a arătat că nicio politică de ieșire nu schimbă semnul pe un setup fără avantaj brut.
8. VALID a fost rulat **o singură dată** pe fiecare bază; nicio re-rulare. TEST nu a fost atins. Nicio altă abatere de la protocol.

## 8. Fișiere

- `app/strategies/discovery/disc_f4_orb.py`, `app/strategies/discovery/disc_f4_sessmom.py` — pluginurile noi (research only).
- `tests/test_stage_f_f4.py` — 14 teste de fum (toate trec).
- `research/results/F4/<run>_train_std.json` (24) — grila TRAIN; `..._train_ecn.json` (3) — ECN pentru top 3; `S10_sessmom_LON_k0.5_sl3_tp3_valid_{std,ecn}.json` — VALID; `*.trades.jsonl` — ultimele 300 tranzacții ale rulărilor (diagnostic).
- `research/results/F4/grid_log.jsonl` — o linie per rulare (config, comandă, rezultat compact, durată).
- `research/results/F4/byyear/*.json` + `log.txt` — descompunerea pe ani (§5).
