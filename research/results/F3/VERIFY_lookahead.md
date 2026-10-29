# F3 / disc-h06 H4 (c 0,7 · range_mult 1,3 · close_frac 0,3) — verificare adversarială (a): lookahead și corectitudinea codului

Verificator independent, protocol §5(a). Lentilă: decizia folosește doar bare închise? cotația barei de decizie stabilește direcția? umplerea e la deschiderea barei următoare? SL/TP vin doar din date închise? Plus re-rularea comenzii VALID alese (o singură dată) și verificarea testelor de fum.

**Vot: NU RESPING pe această lentilă (refuted = false).** Nu există lookahead, iar rezultatul VALID se reproduce exact. Am găsit **o nealiniere de paritate live** în motor (filtrul de spread folosește ora de deschidere a barei de decizie, cu 4 h în urmă) care, pe TRAIN, **flatează modest** rezultatul; nu explică avantajul, dar poate conta la o Poartă 1 trecută cu marjă de 0,008 R — o las verificatorului (b)/coordonatorului. Nu am rulat nicio variantă pe VALID.

## 1. Re-rularea comenzii VALID alese (o singură rulare, STANDARD)

Comanda din SUMMARY §3 + `--split valid --workers 1`, ieșire în scratchpad (nu am suprascris JSON-ul familiei). Rezultat: **n = 149, avg R = 0,134, CI90 [−0,0124; +0,2777], PF 1,349, max DD 9,4 R, net +19,96 R, 2019 +0,148 / 2020 +0,118** — identic cifră cu cifră cu `valid_H06_N4_all.json`. Determinist (bootstrap cu seed fix).

## 2. Ce am citit, linie cu linie

- `app/strategies/discovery/disc_h06.py`: `signal(bars)` lucrează pe `sig_bars[-400:]` (bare închise). Compresia = ATR(5)/ATR(100) pe barele `n-6 … n-2` (cele 5 dinaintea trigger-ului); `atr_s = slow[n-2]` (ATR100 al barei precedente, trigger-ul nu-și umflă propriul etalon); trigger = `bars[-1]` (ultima bară închisă) cu `range ≥ 1,3 × atr_s` și `loc = (c − l)/range ≥ 0,7` (BUY) / `≤ 0,3` (SELL). **Direcția nu atinge cotația.** Cotația (`quote`) e folosită doar pentru prețul de intrare (ask/bid) și filtrul de spread din `_common.finish`. SL = extrema opusă a barei-trigger, distanța tăiată la [1; 2] × ATR100; TP = 2 × SL (prin `tp_r`). Fără referire la bare viitoare, fără `htf_bars` (NONE).
- `app/strategies/_htf.py::start`, `_common.py::finish`: doar verificări de date, podea `min_sl_points`, filtre de piață (spread > 30, spread > 20 % din SL, tick_age, stops_level) și `context_blockers`. Nicio sursă de date externă.
- `app/backtest.py` (faza 1 `scan_symbol`): pentru bara `i` decizia se face la `T = t_i + 4 h`, cu `sig[i-399 … i]` și `quote = {bid: c_i, ask: c_i + spread}`. Faza 2 `_simulate`: ordinea evenimentelor pe fiecare `t` este (1) umpleri și gestiunea pozițiilor pe bara care se deschide la `t`, apoi (2) decizii la **închiderea** barei `t` → ordinul PENDING se umple la grupul următor, adică la deschiderea barei următoare: `entry = o + spread(ora barei de umplere) + 1 pct` (BUY) / `o − 1 pct` (SELL). SL/TP sunt prețurile fixe din faza 1 (`sl0`/`tp0`); `r_dist` se recalculează din prețul real de umplere (ca în live: niveluri de la decizie, umplere la piață). Un gap peste SL/TP → `skipped_gap` (0 pe VALID).
- Ieșire: `default_exit` → `{"mode": "base", "max_hold_hours": 48}` ⇒ `SimPosition._step_base` (labeler): SL înainte de TP în aceeași bară, stop cu slippage 1 pct și umplere la deschidere dacă e gap, TP exact la nivel, BE dezactivat (`break_even_at_r` 0 din `RUN_DEFAULTS`), fără trailing/rescue. Time stop la `t + 4h − opened_t ≥ 48 h`, la **închiderea** barei (`c`, respectiv `c + spread` pentru SELL). Swap pe rollover-urile strict din interiorul deținerii; comision 7 USD/lot (implicitul runner-ului, aplicat și pe STANDARD — conservator).
- `load_bars` exclude barele cu `t + 4h > now_ts`; fereastra se termină la `end` (2021-01-01), nicio poziție închisă forțat (`closed_at_end` 0).

## 3. Re-derivare independentă a tuturor celor 149 de tranzacții (fără cod din `app/`)

Script propriu (scratchpad) care citește `bars_archive` H4 direct din `data/research.db`, reimplementează ATR Wilder și modelul de spread (`SPREAD_PTS × HOUR_MULT` din `build_research_db.py`) și verifică fiecare tranzacție din lista `--trades-out` a re-rulării:

| Verificare | Rezultat (149 tranzacții) |
|---|---|
| Bara de decizie se închide exact la `signal_t` și este **strict înaintea** barei de umplere; umplerea e **bara imediat următoare** (`entry_t − signal_t = 0 h` pentru toate) | 149 / 149 |
| Regula (compresie ≥ 3/5, range ≥ 1,3 × ATR100 al barei precedente, închidere în 30 % extreme) reprodusă doar din bare închise, direcția identică | 149 / 149 |
| SL și TP reproduse din cotația de decizie (`c` + spread-ul orei barei) și extrema barei-trigger, cu plafonul [1; 2] × ATR100 (toleranță 1,5 pct) | 149 / 149 |
| Prețul de umplere = `o` bara următoare + spread(ora umplerii) + 1 pct slippage (BUY) / `o` − 1 pct (SELL) | 149 / 149 |
| Re-simularea traseului (SL întâi, apoi TP, time stop 48 h la închidere, SELL închis la ask) reproduce motivul, momentul și prețul ieșirii și `gross_r` (±0,02) | 149 / 149 |
| Gap deschidere-umplere vs. închiderea barei de decizie | medie +0,2 pct, min −20, max +17 pct — fără umpleri „norocoase” |

Alinierea datelor: pentru EURUSD, GBPJPY, XAUUSD, USDCAD pe 2019–2020, **fiecare bară H4 este exact agregatul barelor H1 de la `t … t+3h`** (open, high, low, close) → `t` este ora de deschidere, nu de închidere (ipoteza „etichetă = ora închiderii”, care ar fi însemnat lookahead de 4 h, dă 0 potriviri). Grila H4 este cea a brokerului NY-close (deschideri la 1/5/9/13/17/21 UTC vara, 2/6/10/14/18/22 iarna), goluri doar la weekend.

Descompunerea rezultatului VALID după ieșire: **TIME 82 tranzacții, +20,54 R (medie +0,25)**; SL 44, −45,56 R (medie −1,04, include spread/slippage/swap/comision); TP 23, +44,99 R (medie +1,96). Tot avantajul net (+19,96 R) vine din tranzacțiile închise pe timp; SL și TP se anulează. Niciun R > 2,03 sau < −1,11 — fără outlieri de date.

## 4. Teste de fum

- `tests/test_stage_f_f3.py` — 8 passed. **Nu testează regulile disc-h06**: doar import + rezolvare prin registru + tabelul TF/HTF pinat; testele de formă/lookahead/runner din fișier privesc wrapper-ul `disc-f3-h12`.
- Regulile H06 sunt acoperite de testul etapei D `tests/test_discovery_1.py` (12 passed): rezolvare registru, invarianță la coadă (doar ultimele 400 de bare contează) și ignorarea barelor viitoare (`test_decisions_ignore_future_bars_and_extra_history` parametrizat pe disc-h06), scenariu construit BUY/SELL/mijloc cu SL în [1; 2] × ATR100 și TP = 2 × SL.
- Observație: protocolul cere test de fum per familie care să exercite plugin-ul folosit; pentru configurația aleasă acoperirea există, dar în alt fișier (etapa D).

## 5. Ce am găsit în afara lookahead-ului (paritate live / artefacte)

**(5.1) Filtrul de spread folosește ora deschiderii barei de decizie, nu ora deciziei.** În `scan_symbol`, `quote["spread_points"] = spreads[i]`, iar `spreads` sunt indexate pe `hour(t_i)` = ora **deschiderii** barei `i`; decizia se ia însă la `t_i + 4 h`. Umplerea plătește corect spread-ul orei de umplere (verificat 149/149), dar filtrul `spread > 30` se aplică unui spread **vechi de 4 h**. Pe H4 asta blochează sistematic trigger-ele date de bara de rollover (deschidere 21/22 UTC, multiplicator ×2,2/×2,6), pe care un bot live, citind spread-ul la 01/02 UTC (×1,4/×1,3), le-ar accepta parțial. Pe VALID: 26 candidați blocați din 228 (`blocked_market`); pe ECN efectul nu există (1 blocat).
Cuantificare pe **TRAIN** (nu pe VALID), aceeași configurație cu `max_spread_points` 100 (filtrul ridicat): n 370 → **418**, avg R +0,0533 → **+0,0356**, PF 1,121 → 1,078, CI90 [−0,051; +0,128]; cele 48 de tranzacții suplimentare ≈ −0,10 R fiecare; 2018 −0,19. Rezultatul cu paritate live stă între cele două (filtrul la ora deciziei ar mai bloca GBPJPY/XAUUSD la ×1,4). Nu e lookahead (informație veche, nu viitoare) și **nu explică avantajul** (ECN, fără acest efect, rămâne +0,106 pe VALID), dar e un artefact care **flatează** rezultatul STANDARD și care, la o marjă de 0,008 R pe CI90, poate decide Poarta 1 — de verificat de lentila (b) sau de coordonator (necesită modificarea fișierului partajat `app/backtest.py`: spread-ul cotației de decizie ar trebui luat la `hour(t_i + sec)`).

**(5.2) Time stop fără slippage**: ieșirea TIME e la prețul de închidere al barei (BUY) / închidere + spread (SELL), fără cele 1 pct de slippage aplicate stopurilor; live ar închide la piață imediat după închiderea barei. Pe 82 de tranzacții efectul e de ordinul 0,001 R/tranzacție — imaterial.

**(5.3) Comision 7 USD/lot și pe STANDARD** (implicitul runner-ului, deși `research.db` e „fără comision”): conservator pentru rezultat; îl semnalez doar pentru consistența raportării costurilor.

**(5.4) Nealiniere semnal/umplere la weekend**: hold-ul de 48 h e wall-clock, deci o intrare joi/vineri după-amiază iese la prima bară de după weekend (6 ore efective de piață). Nu e lookahead; e o particularitate de design pe care live o reproduce identic.

## 6. Concluzie pe lentila (a)

- Lookahead: **nu**. Direcția, SL și TP depind doar de bara-trigger închisă și de barele precedente; cotația stabilește doar prețul de intrare și filtrul de spread; umplerea e la deschiderea barei următoare cu spread-ul acelei ore; ieșirile folosesc doar barele de după umplere; etichetele de timp ale datelor sunt ore de deschidere.
- Corectitudinea codului: motorul face ce descrie; un singur defect de paritate live (5.1), cu efect modest și în defavoarea rezultatului live.
- Reproducere: VALID identic (149 / +0,134 / [−0,012; +0,278] / 1,349).
- **Vot: nu resping.** Rezerve (pentru lentilele b și c): Poarta 1 e trecută cu 0,008 R; filtrul de spread decalat flatează STANDARD-ul; tot avantajul vine din ieșirile pe timp; 2018 negativ în toate variantele; a 24-a configurație din 24.

Fișiere produse: doar acest raport. Re-rularea VALID și scriptul de re-derivare sunt în scratchpad-ul sesiunii (`/tmp/claude-0/-home-user-trandingbot/005b2e16-62ce-5573-b4b2-692c31da423d/scratchpad/verify/`): `valid_rerun.json`, `valid_trades.jsonl`, `rederive.py`, `train_sp100.json`. Niciun fișier partajat modificat; nimic comis.
