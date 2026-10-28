# Stage D — primele modele ML (filtru de intrare + model de ieșire) pentru trend-v1

> Protocol pre-înregistrat: secțiunea „Protocol” de mai jos a fost scrisă în acest fișier ÎNAINTE de orice rulare și nu a fost modificată după vederea rezultatelor TEST. Rezultatele sunt adăugate dedesubt de scriptul de cercetare `stage_d.py` (rulat pe o copie a bazei de date).

## Protocol (pre-înregistrat)

- **Date**: `bars_archive` dintr-o COPIE a bazei de date (`scratchpad/ml/ml.db`, copiată cu `sqlite3 .backup` din `data/bot.db`). Serverul live (8787) nu este atins; nimic nu se scrie în `data/`. CPU: `nice -n 10`, ≤ 3 workeri / fire.
- **Strategie de bază**: `trend-v1` (pluginul live, fără modificări de parametri de semnal), 11 perechi: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, EURJPY, GBPJPY, NZDUSD, EURGBP, EURCHF, AUDJPY.
  - **H1 (principal, ~3 ani)**: semnal H1, HTF H4, `signal_expiry_minutes`=60, `max_hold_minutes`=1440 (time stop 24 h — aceeași orizontă ca eticheta intrării, ca eticheta și tranzacția simulată să fie aceeași tranzacție), BE la 1R (setarea live), fără sesiune, ieșiri `base`.
  - **M5 (informativ, ~4 luni / 120 zile)**: setările live (semnal M5, HTF H1, `max_hold_minutes` 240, expirare 15 min, BE 1R), fără sesiune, ieșiri `base`. Nu decide nimic.
- **Costuri STANDARD** (ca în C2): spread fix pe simbol (puncte) EURUSD 10, GBPUSD 14, USDJPY 12, AUDUSD 12, USDCAD 16, EURJPY 18, GBPJPY 28, NZDUSD 16, EURGBP 14, EURCHF 18, AUDJPY 18; slippage 1 punct; comision 0; swap `table` (implicit −0.5 pip/zi pe ambele direcții, miercuri ×3). Eticheta intrării = R net (spread + slippage + swap pe rollover-urile din intervalul deținut). Ieșirea anticipată a modelului de ieșire plătește în plus un cost de execuție de 0.01R.
- **Risc în backtest** (ca R să măsoare strategia, nu limitele contului): `size_mode=r_only`, max 6 poziții, max 1 poziție pe monedă+direcție, limitele zilnice/serii/risc total dezactivate.
- **Seturi de date** (`app/ml/dataset.py`): intrare = fiecare candidat trend-v1 fără blocaj de piață (luat sau blocat doar de context), trăsături `fs-v1` doar din bare închise la momentul deciziei, țintă `R_net > 0`; ieșire = un rând pe fiecare bară închisă a fiecărei tranzacții simulate (record_paths), trăsături `xs-v1`, țintă = R rămas dacă poziția e ținută (net).
- **Împărțire cronologică (o singură dată, fără căutare pe TEST)**: după timpul deciziei (intrare) / timpul deschiderii tranzacției (ieșire), pe cuantile de timp ale rândurilor: TRAIN = primele 60 %, CALIBRARE = următoarele 15 %, TEST = ultimele 25 %. **Purge**: un rând al unui segment anterior a cărui etichetă se termină după începutul segmentului următor este eliminat. **Embargo**: primele 48 h (H1) / 24 h (M5) ale CALIBRĂRII și TEST-ului sunt eliminate. Codificarea simbolului (target encoding) doar din TRAIN.
- **Modele de intrare**: LogisticRegression (imputare + standardizare; baseline) și HistGradientBoostingClassifier pentru P(R_net > 0) antrenate pe TRAIN; HGB calibrat izoton (model înghețat = `cv='prefit'`) pe CALIBRARE. Pragul P se alege DOAR pe CALIBRARE: pragul (cuantile 5 %…75 % ale P) care maximizează avg R al candidaților cu P > prag, cu acoperire ≥ 25 % și ≥ 30 de candidați; altfel 0 (= ia tot).
- **Model de ieșire**: HistGradientBoostingRegressor pentru R rămas, antrenat pe TRAIN; politica „închide la prima bară cu R rămas prezis < −marjă”; marja din {0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0} aleasă DOAR pe CALIBRARE (max avg R pe tranzacție; dacă nicio marjă nu bate ieșirile de bază pe CALIBRARE: modelul nu închide nimic).
- **Combinat**: tranzacțiile backtestului din TEST, păstrate doar dacă P(candidat) > prag, apoi gestionate de modelul de ieșire; comparat cu toate tranzacțiile backtestului din TEST cu ieșiri de bază.
- **Raport pe TEST**: Brier și log loss vs rata de bază (constantă = rata din TRAIN, și rata TEST doar ca referință), ECE (10 bin-uri), AUC, curba de calibrare; tranzacționare: n, avg R, CI90 bootstrap, PF, win rate, durată medie.
- **Evaluarea de tranzacționare principală a filtrului de intrare** = tranzacțiile reale ale backtestului (candidații efectiv luați de trend-v1) din TEST: „ia doar dacă P > prag” vs „ia tot”. Pe toți candidații (suprapuși, corelați) se raportează doar informativ.
- **Poartă de promovare (pre-înregistrată, H1, costuri STANDARD, pe TEST)** — o politică „trece” doar dacă îndeplinește TOATE:
  1. avg R > 0 după costuri;
  2. limita inferioară CI90 > −0.02;
  3. bate baseline-ul (ia tot / ieșiri de bază) cu > 0.05R avg R;
  4. calibrare: ECE < 0.05 (intrare; combinat folosește ECE-ul modelului de intrare); pentru ieșire: eroarea medie de calibrare pe decile < 0.05R;
  5. ≥ 150 tranzacții TEST.
  Se evaluează separat: (1) filtrul de intrare, (2) modelul de ieșire, (3) combinat. Chiar și o politică care trece rămâne `candidate` (apoi doar shadow); aprobarea pentru DEMO este exclusiv manuală, după ≥ 2 săptămâni / ≥ 100 decizii shadow. Niciun model nu este aprobat în această etapă.
- **Testare multiplă**: se antrenează 2 TF × (2 modele de intrare + 1 de ieșire) = 6 modele și 3 politici evaluate de poartă pe H1; M5 nu decide. TEST este evaluat o singură dată.

<!-- REZULTATE (generate de stage_d.py) -->

## Concluzie pe scurt

**Niciun model nu trece poarta pre-înregistrată: filtrul de intrare, modelul de ieșire și combinația lor sunt toate REJECTED pe H1 (TEST, costuri STANDARD).** Toate politicile rămân cu avg R negativ după costuri, iar limita inferioară CI90 este mult sub −0.02. Modelele fac strategia trend-v1 să piardă **mai puțin** (de la −0.103R la −0.050R pe tranzacție în varianta combinată), dar nu o fac profitabilă. Toate cele 4 modele sunt înregistrate doar ca `candidate` în copia bazei de date; **niciunul nu este aprobat, iar comportamentul live rămâne neschimbat.**

Motivul de fond: trăsăturile disponibile la momentul deciziei aproape nu conțin informație despre rezultat (AUC 0.548 pe H1, Brier 0.2185 față de 0.2201 pentru o constantă). **ML nu poate crea un avantaj (edge) acolo unde trăsăturile nu poartă unul**: poate doar să sorteze puțin mai bine tranzacțiile unei strategii care pierde. A mări sumele pe o strategie cu așteptare negativă mărește doar pierderile.

| Politică (H1, TEST 2026-01-28 → 2026-10-28) | n | avg R | CI90 | PF | win | durată medie | vs baseline | Poartă |
|---|---|---|---|---|---|---|---|---|
| Baseline: toate tranzacțiile trend-v1, ieșiri base | 1662 | −0.103 | [−0.149; −0.058] | 0.812 | 0.39 | 9.96 h | — | — |
| (1) Filtru intrare HGB (P > 0.333) | 799 | −0.064 | [−0.135; +0.003] | 0.876 | 0.43 | 10.81 h | +0.039 | **REJECTED** |
| (1b) Filtru intrare LogReg (baseline ML, P > 0.354) | 664 | −0.063 | [−0.139; +0.008] | 0.880 | 0.43 | 9.86 h | +0.040 | REJECTED |
| (2) Model ieșire (închide dacă R rămas prezis < −0.10) | 1662 | −0.067 | [−0.101; −0.032] | 0.820 | 0.44 | 5.14 h | +0.036 | **REJECTED** |
| (3) Combinat (filtru HGB + model ieșire) | 799 | −0.050 | [−0.104; +0.004] | 0.867 | 0.46 | 5.75 h | +0.053 | **REJECTED** |

Verificarea porții (✓ / ✗):

| Criteriu | (1) Intrare | (2) Ieșire | (3) Combinat |
|---|---|---|---|
| avg R > 0 după costuri | ✗ (−0.064) | ✗ (−0.067) | ✗ (−0.050) |
| CI90 inferior > −0.02 | ✗ (−0.135) | ✗ (−0.101) | ✗ (−0.104) |
| bate baseline cu > 0.05R | ✗ (+0.039) | ✗ (+0.036) | ✓ (+0.053) |
| calibrare (ECE < 0.05 / ieșire: eroare decile < 0.05R) | ✓ (0.006) | ✗ (0.080R) | ✓ (0.006) |
| ≥ 150 tranzacții TEST | ✓ (799) | ✓ (1662) | ✓ (799) |
| **Decizie** | **REJECTED** | **REJECTED** | **REJECTED** |

## Date și împărțire

| | H1 (principal) | M5 (informativ) |
|---|---|---|
| Perioadă date | 2023-10-29 → 2026-10-28 | 2026-07-01 → 2026-10-28 (arhiva M5 începe în iulie) |
| Candidați trend-v1 (dataset intrare) | 142 797 | 23 563 |
| Tranzacții backtest (record_paths) | 6 792 | 2 032 |
| Backtest complet: avg R / CI90 / PF | −0.137 / [−0.160; −0.118] / 0.754 | −0.190 / [−0.229; −0.153] / 0.673 |
| Rânduri dataset ieșire (bare de tranzacție) | 46 028 (0 tranzacții eliminate la reluare) | 16 495 (0 eliminate) |
| Rata de câștig a etichetei (R_net > 0) / R mediu / swap mediu | 0.331 / −0.124 / −0.009R | 0.321 / −0.196 / −0.003R |
| TRAIN | 2023-10-29 → 2025-08-14 (85 599 candidați) | 2026-07-01 → 2026-09-18 (14 123) |
| CALIBRARE | 2025-08-16 → 2026-01-26 (21 024) | 2026-09-19 → 2026-10-01 (3 469) |
| TEST | 2026-01-28 → 2026-10-28 (35 279 candidați, 1 662 tranzacții) | 2026-10-02 → 2026-10-28 (5 301 candidați, 490 tranzacții) |
| Embargo / purge | 48 h + purge pe sfârșitul etichetei | 24 h + purge |

Notă: pe H1 s-a folosit un time stop de 24 h (pre-înregistrat, ca eticheta intrării să fie exact tranzacția simulată); referința C2 (fără time stop) avea −0.128R pe TEST-ul ei — aceeași concluzie.

## 1) Modelul de intrare (fs-v1) — calitatea probabilităților pe TEST

| H1, TEST (35 279 candidați, rata de bază 0.327) | Brier | log loss | AUC | ECE |
|---|---|---|---|---|
| Constantă = rata din TRAIN (0.331) | 0.2201 | 0.6321 | 0.500 | — |
| Constantă = rata TEST (doar referință, folosește viitorul) | 0.2200 | 0.6319 | 0.500 | 0 |
| LogisticRegression | 0.2193 | 0.6302 | 0.541 | 0.013 |
| HGB necalibrat | 0.2187 | 0.6285 | 0.550 | 0.021 |
| **HGB + izoton (modelul salvat)** | **0.2185** | **0.6286** | **0.548** | **0.006** |

Curba de calibrare (HGB + izoton, TEST H1):

| bin P | n | P mediu | frecvență reală |
|---|---|---|---|
| 0.1–0.2 | 217 | 0.158 | 0.088 |
| 0.2–0.3 | 7 895 | 0.274 | 0.274 |
| 0.3–0.4 | 27 128 | 0.337 | 0.344 |
| 0.4–0.5 | 37 | 0.400 | 0.432 |

Modelul este bine calibrat, dar aproape toate probabilitățile stau între 0.27 și 0.34: nu are ce separa. Câștigul de Brier față de o constantă este de ~0.7 %.

Cele mai importante trăsături (permutare pe TEST, scădere de AUC): `hour_sin` 0.019, `weekday` 0.011, codificarea simbolului 0.011, `ema50_slope_atr` 0.004, restul < 0.002. Adică modelul învață mai ales **ora din zi, ziua săptămânii și perechea** (efecte de calendar și de cost relativ), nu structura prețului — exact tipul de regularitate care e cel mai puțin stabil în timp.

Pragul a fost ales doar pe CALIBRARE: 0.333 (acoperire 47 %; pe CALIBRARE avg R −0.145 → −0.108). Pe TEST, la nivel de tranzacție: −0.103 → −0.064 (+0.039R). Față de un filtru ALEATOR care păstrează tot 799 de tranzacții, câștigul are p ≈ 0.09 (unilateral) — nu este semnificativ, mai ales după corecția pentru testare multiplă.

Informativ, pe toți candidații din TEST (suprapuși, puternic corelați — CI-urile sunt prea înguste): ia tot 35 279 / −0.137; HGB 17 422 / −0.085; LogReg 13 240 / −0.090.

## 2) Modelul de ieșire (xs-v1)

| H1, TEST | valoare |
|---|---|
| Marja aleasă pe CALIBRARE | 0.10 (CAL: −0.031 vs ieșiri base −0.099 pe tranzacțiile cu bare) |
| Calitatea regresiei R rămas (11 636 bare TEST) | R² −0.014, corelație 0.045, MAE 0.83R, eroare de calibrare pe decile 0.080R |
| Ieșiri anticipate (ML_EXIT) | 878 din 1 662 tranzacții |
| Ieșiri base: avg R / CI90 / PF / durată | −0.103 / [−0.149; −0.058] / 0.812 / 9.96 h |
| Politica model: avg R / CI90 / PF / durată | −0.067 / [−0.101; −0.032] / 0.820 / 5.14 h |

Reperul naiv (fără model; aceeași contabilitate a costurilor) — „închide toate tranzacțiile la închiderea barei k”:

| Regulă naivă (TEST H1) | bara 1 | bara 2 | bara 3 | bara 6 |
|---|---|---|---|---|
| avg R | −0.096 | −0.095 | −0.087 | −0.089 |

Cea mai mare parte a „îmbunătățirii” vine din faptul că **a ține o poziție a unei strategii perdante costă**: orice ieșire mai devreme pierde mai puțin. Modelul bate regula naivă cu ~0.02–0.03R, dar predicțiile lui de R rămas aproape nu corelează cu realitatea (corelație 0.045), deci nu decide „momentul optim” de închidere pe baza unei informații reale.

## 3) Combinat (filtru intrare + model ieșire)

Pe TEST H1: 799 tranzacții, −0.050R, CI90 [−0.104; +0.004], PF 0.867, durată medie 5.75 h, față de baseline 1 662 / −0.103R. Bate baseline-ul cu +0.053R (singurul criteriu de „bătut baseline-ul” îndeplinit, la limită), dar rămâne negativ → REJECTED.

## M5 (informativ, 4 luni — nu decide)

| M5, TEST 2026-10-02 → 10-28 | n | avg R | CI90 | PF |
|---|---|---|---|---|
| Baseline (toate tranzacțiile, ieșiri base) | 490 | −0.226 | [−0.305; −0.148] | 0.615 |
| Filtru HGB (P > 0.325) | 173 | −0.218 | [−0.357; −0.090] | 0.640 |
| Filtru LogReg | 186 | −0.193 | [−0.325; −0.056] | 0.663 |
| Model ieșire (marjă 0.5; 77 ieșiri anticipate) | 490 | −0.224 | [−0.298; −0.150] | 0.593 |
| Combinat | 173 | −0.200 | [−0.322; −0.078] | 0.640 |

Clasificare M5 TEST: AUC 0.546 (LogReg 0.502), Brier 0.2184 vs 0.2189 constantă, ECE 0.009. Regresia de ieșire: R² −0.10, corelație −0.005, eroare de calibrare 0.21R (inutilizabilă). Filtrul aleator cu 173 de tranzacții: p ≈ 0.46. Pe M5 nu există nici măcar o îmbunătățire relativă credibilă.

## Testare multiplă și limite

- Pe H1 s-au evaluat pe TEST 3 politici + 1 variantă LogReg (4 comparații) și, informativ, aceleași pe M5; înainte de această etapă, Stage C/C2 au încercat ~80+ configuri de reguli. Cu atâtea încercări, o diferență de +0.04–0.05R cu p ≈ 0.09 este de așteptat și din noroc; după o corecție Bonferroni nimic nu este semnificativ.
- Evaluarea filtrului la nivel de tranzacție este aproximativă: o tranzacție respinsă ar fi eliberat un loc (max 6 poziții, 1 per monedă+direcție) pentru alt candidat, ceea ce nu s-a simulat.
- Ieșirea anticipată plătește spread (închidere la bid/ask) + 0.01R; swap-ul întregii tranzacții inițiale este scăzut chiar și la ieșirea anticipată (conservator, efect mic: −0.009R în medie).
- Totul este backtest pe date MetaQuotes-Demo cu costuri modelate; TEST-ul H1 acoperă ~9 luni.

## Ce înseamnă pentru întrebarea „poate botul să învețe după fiecare tranzacție, să pună sume mai mari și să închidă la momentul optim?”

- **Infrastructura există și e sigură**: modelele învață separat de tranzacționare (reantrenare nocturnă pe o copie), pornesc ca `candidate` → `shadow` (doar scor + jurnal în `ml_decisions`, nu tranzacționează), iar aprobarea pentru DEMO e doar manuală, cu confirmare „APROB” și poarta de mai sus. Un model aprobat poate doar să refuze intrări, să scaleze riscul în [0.5×, max 2×] sub toate limitele Risk Engine și să închidă mai devreme — niciodată să lărgească SL.
- **Rezultatul onest de azi**: pe datele noastre, ce știe botul în momentul deciziei nu prezice rezultatul tranzacției suficient de bine. Modelul de ieșire nu „vede” momentul optim de închidere (corelație 0.045), iar filtrul de intrare nu transformă trend-v1 într-o strategie profitabilă. **Mărirea sumelor nu este justificată**: pe o așteptare negativă (−0.05…−0.10R pe tranzacție), un risc dublu înseamnă pierderi duble.
- **Recomandare**: modelele H1 pot fi puse în **shadow** (fără efect pe tranzacții) ca să strângem dovezi live; nu se aprobă nimic. Progresul real trebuie să vină dintr-o sursă de avantaj nouă (alte date/trăsături, alte piețe/orizonturi, costuri mai mici), nu din mai mult ML pe aceleași trăsături.

## Artefacte

- Modele (toate `candidate`, niciunul aprobat), în copia `scratchpad/ml/ml.db`, tabel `ml_models`; artefacte joblib în `scratchpad/ml/models/` (sha256 verificat la încărcare):
  - `entry-trend-v1-h1-20261028191541-af07e3` (fs-v1, dataset `ds-95230a2537d1`, prag 0.333, antrenat până la 2026-01-26)
  - `exit-trend-v1-h1-20261028191541-94038d` (xs-v1, marjă 0.10)
  - `entry-trend-v1-m5-20261028191541-4e485a` (informativ, prag 0.325)
  - `exit-trend-v1-m5-20261028191541-867538` (informativ, marjă 0.5)
- Rezultate complete: `scratchpad/ml/d_results.json`; scripturi: `scratchpad/ml/stage_d.py`, `scratchpad/ml/naive.py`.
- Serverul live nu a fost atins; `data/` nu a fost scris; niciun commit.
