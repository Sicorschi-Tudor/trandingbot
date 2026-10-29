# Etapa F — Familia F5: meta-labeling ML (fs-v1 / xs-v1) pe candidații trend-v1 H1 și breakout-v1 H1

**Verdict: REJECTED (Poarta 1 — VALID, cont STANDARD).** Filtrul de intrare ML `fs-v1` (HistGradientBoosting + calibrare izotonă, prag ales pe validare) **nu schimbă semnul** rezultatului niciuneia dintre cele două strategii de bază. Pe TRAIN (walk-forward OOS în interiorul TRAIN-ului, 2013-06 → 2019-01) toate cele 18 configurații încercate au avg R negativ cu CI90 integral sub zero; cea mai bună după regula §4 — **breakout-v1 H1/H4 + HGB izoton antrenat pe candidații tranzacționați efectiv de backtest, acoperire ≥ 25 %** — are OOS −0,060 R (n 1 343, PF 0,89) față de −0,094 fără filtru. Pe VALID (2019-01-01 → 2021-01-01, STANDARD, backtest de portofoliu prin `run_bt.py` cu pluginul `disc-f5-ml`): **n = 1 366, avg R = −0,109, CI90 [−0,162; −0,059], PF 0,80**, negativ în ambii ani, față de −0,139 / PF 0,75 pentru breakout-v1 fără filtru. Pe ECN: n = 1 054, avg R −0,045, CI90 [−0,103; +0,011], PF 0,91 — tot negativ, deci nici `ECN_ONLY`. Modelul de ieșire `xs-v1` nu are putere predictivă (corelație ≈ 0 cu R rămas) și pe STANDARD nu găsește nicio marjă care să bată ieșirile de bază. Concluzia etapei D (3 ani, 11 perechi, date MetaQuotes 2023–2026) se confirmă pe 7,6 ani × 12 instrumente: **ML-ul sortează puțin mai bine tranzacțiile unor strategii care pierd (+0,03 … +0,07 R pe tranzacție), dar nu creează un avantaj acolo unde trăsăturile nu poartă unul** (AUC 0,52–0,60).

Agent: F5 (cercetător cantitativ, sesiune Claude Code). Data: 2026-10-29. **TEST nu a fost atins.** Toate fișierele: `research/results/F5/`.

## 1. Protocolul urmat

- Protocol pre-înregistrat: `docs/research/STAGE_F_PROTOCOL.md`, citit înainte de orice rulare. Familia F5: filtrul de intrare `fs-v1` (HistGradientBoostingClassifier + calibrare izotonă pe un segment separat, prag ales pe segmentul de validare; baseline LogisticRegression) pe candidații strategiilor de bază **trend-v1 H1/H4** și **breakout-v1 H1/H4** (parametri de semnal impliciți), plus modelul de ieșire `xs-v1` (HistGradientBoostingRegressor pentru R rămas) acolo unde pipeline-ul îl oferă gratis.
- Date: `data/research.db` (STANDARD) pentru toată grila; `data/research_ecn.db` (ECN) pentru cele mai bune 3 configurații pe TRAIN și pentru configurația aleasă pe VALID. 12 instrumente, bare UTC, specificații `data/research_specs.json`, cost `stats` (spread modelat per simbol × oră UTC), slippage 1 punct, comision 7 USD/lot (implicitul `app.backtest`), swap off (poziții ≤ 24 h). TRAIN 2013-06-01 → 2019-01-01, VALID 2019-01-01 → 2021-01-01. **TEST nu a fost atins**: nici prin `run_bt.py` (fără `--allow-test`, fără `--start/--end` după 2021-01-01), nici prin seturile de date (`end_ts` = 2021-01-01, deci nicio bară din TEST nu intră în etichete).
- Infrastructura ML existentă, **nemodificată**: `app/ml/dataset.py` (`build`: un singur backtest `run_cached` cu `record_paths`; eticheta = R net al candidatului simulat cu `labeler.simulate` la deschiderea barei următoare, cu spread-ul modelat, slippage și comision), `app/ml/features.py` (`fs-v1`: 28 trăsături doar din barele închise, spread-ul cotației și nivelurile candidatului; `xs-v1`: 14 trăsături de stare), `app/ml/train.py` (`fit_entry`, `evaluate_entry`, `fit_exit`, `evaluate_exit`, `purged_segments`, `final_split`, `choose_threshold`, `policy_stats`, `classif_metrics`, `regress_metrics`). CLI-ul `python -m app.ml train` folosește `--days` față de ceasul real (2026), deci nu vede datele 2012–2022: scriptul nou **`research/f5_ml.py`** apelează `dataset.build(db, params)` cu perioadele explicite cerute de sarcină (`start_ts` 2013-06-01, `end_ts` 2021-01-01, `now_ts` 2022-03-10, `size_mode r_only`, `session None`, `cost {"mode": "stats"}`, `workers 2`) și funcțiile din `train.py` cu tăieturi **pe timp**.
- Tăierea pe timp (singura abatere față de `train.walk_forward`, care taie pe cuantile ale întregii perioade și nu poate alinia exact ultimul bloc la 2019-01-01 — documentată aici): rândurile TRAIN = decizie `t` < 2019-01-01 **și** sfârșitul etichetei `t_end` < 2019-01-01 (purge: etichetele care intră în VALID sunt eliminate); rândurile VALID = `t` ≥ 2019-01-01 + 48 h (embargo) și `t` < 2021-01-01. În interiorul TRAIN: (a) **metrica de selecție** = walk-forward OOS `train.evaluate_entry` (4 blocuri de test expandante pe a doua jumătate a TRAIN-ului ≈ 2016-03 → 2019-01; înaintea fiecăruia train | calibrare | validare cu purge și embargo 48 h; pragul ales pe segmentul de validare al fiecărui fold); (b) **modelul final** = `train.final_split` pe rândurile TRAIN (train 70 % | calibrare 15 % | validare 15 %, pe timp), pragul ales doar pe segmentul de validare din TRAIN, salvat ca bundle joblib. Comanda `train` nu citește rândurile VALID.
- Harness fix pentru toate configurațiile (decis înainte de prima rulare; identic cu F1 `T01` / `B01`, care sunt astfel referințele în format runner ale celor două strategii de bază pe TRAIN): risc `{"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2}` (cerut de sarcină) **+** `max_spread_points` 60 (la 30, GBPJPY și XAUUSD ar fi fost blocate aproape permanent de filtrul de spread, deci ar fi lipsit din setul de candidați; protocolul cere 12 instrumente) **+** `max_daily_loss_pct` 100 și `max_consecutive_losses` 1000 (limitele de cont ar trunchia eșantionul după ordinea întâmplătoare a pierderilor; R măsoară strategia); strategie `{"rescue_enabled": false}` (ieșirea RESCUE live este un model pentru TF ≤ M5); ieșire `base` cu `max_hold_hours` 24 (precedentul etapei D pentru H1: eticheta intrării = exact tranzacția simulată); fără sesiune. Parametrii de semnal ai strategiilor de bază = impliciții pluginurilor.
- Evaluarea politicii ML pe VALID, în două feluri (ambele decise înainte de a vedea orice număr VALID): (1) **la nivel de portofoliu, prin runner** — pluginul nou `disc-f5-ml` (`app/strategies/discovery/disc_f5_ml.py`) rulează strategia de bază și blochează (blocaj de piață) candidații cu P ≤ prag, folosind bundle-ul salvat; rulat cu `research/run_bt.py --split valid` pe STANDARD și ECN — **acesta este numărul pentru Poarta 1**, pentru că include efectele de portofoliu (locurile eliberate de candidații refuzați sunt ocupate de alții); (2) **la nivel de rând**, pe setul de date (`f5_ml.py valid`): toți candidații VALID (suprapuși, corelați — CI-urile sunt prea înguste; informativ) și candidații pe care backtestul de bază i-a tranzacționat efectiv (`taken`), cu Brier / log loss / AUC / ECE. Pentru ECN, modelul a fost **re-antrenat pe rândurile TRAIN ale bazei ECN** (aceeași configurație, alte costuri în etichete și în trăsătura `spread_atr`), nu refolosit de pe STANDARD.
- Selecție (protocol §4): configurația cu cel mai mare avg R OOS pe TRAIN (STANDARD) dintre cele cu n ≥ 150. O „configurație” = (strategie de bază, model, regula pragului / rândurile de antrenare). Modelul HGB și baseline-ul LogReg ies din aceeași rulare `fit_entry`, dar sunt numărate separat. **18 configurații** încercate pe TRAIN (16 filtre de intrare + 2 modele de ieșire), sub limita de 24.
- Fișiere partajate neatinse: `app/backtest.py`, `app/strategy.py`, `app/risk.py`, `app/exits.py`, `app/labeler.py`, `app/ml/*`, pluginurile existente, `app/strategies/__init__.py`, `_common.py`, `_htf.py`, `research/run_bt.py`. Fișiere noi: `research/f5_ml.py`, `app/strategies/discovery/disc_f5_ml.py`, `tests/test_stage_f_f5.py` (8 teste, toate trec: import prin registru și forma ieșirii identică cu trend-v1 pe bare sintetice H1/H4; prag 0 = decizia strategiei de bază, prag 1 = veto total fără inversarea direcției; blocajul ML nu este în setul neutru de context (deci backtestul îl respectă); direcția nu depinde de cotație; perioadele fixe și tăieturile purge/embargo; runner-ul refuză TEST și acceptă harness-ul; construcția unui set de date mic pe baza de cercetare). Fără commit.

## 2. Grila pe TRAIN (STANDARD) — toate cele 18 configurații

Seturi de date (`f5_ml.py build`, 12 instrumente, candidați 2013-06-01 → 2021-01-01, etichete pe bare < 2021-01-01):

| set | candidați (toți) | rânduri TRAIN (după purge) | rânduri VALID (după embargo) | tranzacții backtest de bază | rânduri ieșire (bare de tranzacție) |
|---|---|---|---|---|---|
| `ds_trend_std` (trend-v1, STANDARD) | 259 672 | 193 035 | 66 515 | 22 195 | 149 581 (110 359 în TRAIN) |
| `ds_breakout_std` (breakout-v1, STANDARD) | 9 072 | 6 778 | 2 294 | 6 553 | 34 156 (26 017 în TRAIN) |
| `ds_breakout_ecn` (breakout-v1, ECN) | 10 138 | 7 753 | 2 652 | 7 243 | 34 537 |
| `ds_trend_ecn` (trend-v1, ECN) | 289 874 | — | — | 22 093 | construit, nefolosit (top 3 pe TRAIN sunt toate breakout); șters (59 MB); se reface cu `f5_ml.py build --db data/research_ecn.db --base trend-v1` |

Referințe runner pe TRAIN (F1, același harness): trend-v1 `T01` n 16 290 / avg R −0,125 / CI90 [−0,139; −0,111] / PF 0,77; breakout-v1 `B01` n 4 847 / −0,081 / [−0,109; −0,053] / PF 0,85. Rândurile `taken` din TRAIN ale setului breakout sunt exact 4 847 (același backtest).

Metrica: walk-forward OOS în TRAIN (4 blocuri, ≈ 2016-03 → 2019-01). `cov` = acoperirea minimă impusă pragului ales pe validare (`choose_threshold`; implicit 25 %). „ia tot” = aceiași rânduri OOS fără filtru. Pentru configurațiile `taken`, modelul este antrenat și evaluat **doar pe candidații tranzacționați efectiv** de backtestul de bază (rânduri nesuprapuse), deci „ia tot” este acolo baseline-ul tranzacțiilor reale.

| # | run | bază | rânduri antrenare | model | cov | n OOS | avg R | CI90 | PF | ia tot: n / avg R / PF | AUC OOS | prag final |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `trend_cov25` | trend-v1 | toți candidații | HGB+izoton | 25 % | 47 083 | −0,0801 | [−0,088; −0,072] | 0,845 | 97 648 / −0,1177 / 0,786 | 0,518 | 0,2969 |
| 2 | `trend_cov25` | trend-v1 | toți candidații | LogReg | 25 % | 54 699 | −0,1084 | [−0,117; −0,100] | 0,797 | 97 648 / −0,1177 / 0,786 | 0,520 | 0,3202 |
| 3 | `trend_cov10` | trend-v1 | toți candidații | HGB+izoton | 10 % | 45 168 | −0,0803 | [−0,089; −0,072] | 0,845 | 97 648 / −0,1177 / 0,786 | 0,518 | 0,2969 |
| 4 | `trend_cov10` | trend-v1 | toți candidații | LogReg | 10 % | 44 905 | −0,1013 | [−0,110; −0,093] | 0,808 | 97 648 / −0,1177 / 0,786 | 0,520 | 0,3202 |
| 5 | `trend_cov50` | trend-v1 | toți candidații | HGB+izoton | 50 % | 79 764 | −0,1049 | [−0,112; −0,098] | 0,806 | 97 648 / −0,1177 / 0,786 | 0,518 | 0,2829 |
| 6 | `trend_cov50` | trend-v1 | toți candidații | LogReg | 50 % | 73 428 | −0,1109 | [−0,118; −0,104] | 0,795 | 97 648 / −0,1177 / 0,786 | 0,520 | 0,2737 |
| 7 | `trend_taken_cov25` | trend-v1 | doar `taken` | HGB+izoton | 25 % | 5 032 | −0,1018 | [−0,126; −0,076] | 0,808 | 8 087 / −0,1211 / 0,779 | 0,511 | 0,2832 |
| 8 | `trend_taken_cov25` | trend-v1 | doar `taken` | LogReg | 25 % | 3 502 | −0,1215 | [−0,153; −0,089] | 0,771 | 8 087 / −0,1211 / 0,779 | 0,511 | 0,3104 |
| 9 | `breakout_cov25` | breakout-v1 | toți candidații | HGB+izoton | 25 % | 1 765 | −0,0773 | [−0,121; −0,037] | 0,854 | 3 493 / −0,0997 / 0,819 | 0,581 | 0,3065 |
| 10 | `breakout_cov25` | breakout-v1 | toți candidații | LogReg | 25 % | 994 | −0,0920 | [−0,146; −0,040] | 0,815 | 3 493 / −0,0997 / 0,819 | 0,593 | 0 (= ia tot) |
| 11 | `breakout_cov10` | breakout-v1 | toți candidații | HGB+izoton | 10 % | 1 757 | −0,0769 | [−0,122; −0,031] | 0,855 | 3 493 / −0,0997 / 0,819 | 0,581 | 0,3065 |
| 12 | `breakout_cov10` | breakout-v1 | toți candidații | LogReg | 10 % | 994 | −0,0920 | [−0,146; −0,040] | 0,815 | 3 493 / −0,0997 / 0,819 | 0,593 | 0 (= ia tot) |
| 13 | `breakout_cov50` | breakout-v1 | toți candidații | HGB+izoton | 50 % | 3 085 | −0,0882 | [−0,121; −0,051] | 0,838 | 3 493 / −0,0997 / 0,819 | 0,581 | 0,2453 |
| 14 | `breakout_cov50` | breakout-v1 | toți candidații | LogReg | 50 % | 2 126 | −0,1016 | [−0,138; −0,062] | 0,810 | 3 493 / −0,0997 / 0,819 | 0,593 | 0 (= ia tot) |
| **15** | **`breakout_taken_cov25`** | **breakout-v1** | **doar `taken`** | **HGB+izoton** | **25 %** | **1 343** | **−0,0601** | **[−0,109; −0,009]** | **0,886** | 2 425 / −0,0942 / 0,827 | 0,564 | **0,2979** |
| 16 | `breakout_taken_cov25` | breakout-v1 | doar `taken` | LogReg | 25 % | 910 | −0,0751 | [−0,132; −0,015] | 0,851 | 2 425 / −0,0942 / 0,827 | 0,598 | 0,2216 |
| 17 | `trend_cov25` (ieșire) | trend-v1 | bare de tranzacție | xs-v1 HGB-regresor, marjă pe validare | — | 7 339 tranz. | −0,0710 | [−0,089; −0,052] | 0,834 | ieșiri base: 7 339 / −0,0710 / 0,862 | corelație 0,004, R² −0,014 | marjă finală 0,05 |
| 18 | `breakout_cov25` (ieșire) | breakout-v1 | bare de tranzacție | xs-v1 HGB-regresor, marjă pe validare | — | 2 029 tranz. | +0,0102 | [−0,026; +0,047] | 1,026 | ieșiri base: 2 029 / +0,0256 / 1,054 | corelație 0,001, R² −0,060 | nicio marjă (nu bate base) |

Pe aceiași rânduri OOS, politicile de intrare la nivelul tranzacțiilor reale ale backtestului (`taken`, informativ pentru #1–#6 și #9–#14): trend HGB cov25 3 746 / −0,099 față de 8 087 / −0,121; breakout HGB cov25 1 220 / −0,067 față de 2 425 / −0,094. Numere complete: `train_<run>.json` (`walk_forward_oos`, `folds`), ordinea rulărilor și timpii: `grid_log.jsonl`.

**Observații pe grilă**

- Toate cele 16 filtre de intrare au avg R OOS negativ cu limita superioară CI90 sub zero. Filtrul HGB îmbunătățește baseline-ul cu +0,02 … +0,04 R pe tranzacție (trend) și +0,01 … +0,03 R (breakout), tăind 40–55 % din candidați, dar rămâne la −0,06 … −0,10 R. LogReg este sistematic mai slab decât HGB ca politică, deși are AUC egal sau mai mare: pragul ales pe validare nu se transferă.
- Puterea de discriminare este aproape nulă pe trend-v1 (AUC 0,518 pe 97 648 candidați OOS; Brier 0,2098 față de 0,2093 pentru constanta = rata de bază — modelul este **mai slab** decât o constantă pe Brier) și mică pe breakout-v1 (AUC 0,56–0,59; Brier 0,2103 față de 0,2123 pentru constantă).
- Regula de acoperire (cov 10 % / 25 % / 50 %) schimbă puțin: la 10 % și 25 % se alege același prag (~0,30); la 50 % filtrul devine aproape inert. Antrenarea doar pe rândurile `taken` ajută breakout-v1 (−0,077 → −0,060) și strică trend-v1 (−0,080 → −0,102): efect de eșantion, nu de metodă.
- Modelul de ieșire xs-v1 nu are informație: corelația predicție–R rămas este 0,004 (trend) și 0,001 (breakout), R² negativ. Pe trend alege marja 0,05 și obține exact același avg R ca ieșirile de bază (−0,071, PF mai mic); pe breakout nicio marjă nu bate ieșirile de bază pe validare → modelul nu închide nimic.
- Folds ale configurației alese (#15; n test / avg R politică / avg R ia-tot): f0 607 / −0,040 / −0,072; f1 585 / −0,049 / −0,109; f2 600 / **+0,003** / −0,097; f3 633 / −0,106 / −0,100. Un singur fold marginal pozitiv; în ultimul fold (≈ 2018) filtrul este **mai slab** decât ia-tot — instabilitate în timp a pragului.

### 2.1 Cele mai bune 3 configurații pe TRAIN — STANDARD vs ECN (același config, re-antrenat pe rândurile TRAIN ale bazei ECN)

| config | STD: n / avg R / CI90 / PF | STD ia tot | ECN: n / avg R / CI90 / PF | ECN ia tot | AUC ECN |
|---|---|---|---|---|---|
| #15 breakout `taken` HGB cov25 | 1 343 / −0,0601 / [−0,109; −0,009] / 0,886 | 2 425 / −0,0942 / 0,827 | 1 836 / −0,0571 / [−0,101; −0,014] / 0,892 | 2 651 / −0,0858 / 0,843 | 0,571 |
| #11 breakout HGB cov10 | 1 757 / −0,0769 / [−0,122; −0,031] / 0,855 | 3 493 / −0,0997 / 0,819 | 2 175 / −0,0596 / [−0,100; −0,024] / 0,886 | 3 838 / −0,0735 / 0,865 | 0,567 |
| #9 breakout HGB cov25 | 1 765 / −0,0773 / [−0,121; −0,037] / 0,854 | 3 493 / −0,0997 / 0,819 | 2 175 / −0,0596 / [−0,100; −0,024] / 0,886 | 3 838 / −0,0735 / 0,865 | 0,567 |

Nici pe ECN (spread ≈ 1/5 + comision) nu există configurație cu avg R OOS > 0 pe TRAIN. (Informativ, ECN #18 ieșire: politica +0,031 față de base +0,052 — modelul strică.)

## 3. Configurația aleasă și de ce

Regula protocolului §4 (cel mai mare avg R OOS pe STANDARD/TRAIN, n ≥ 150, ≤ 24 configurații) alege **#15**: breakout-v1 H1/H4 cu parametrii impliciți, filtru HGB + izoton `fs-v1` antrenat pe candidații tranzacționați de backtest (TRAIN final: 3 378 antrenare | 701 calibrare | 760 validare, pe timp; antrenat până la 2018-12-31), prag P > **0,2979** (acoperire pe validare ≈ 60 %), harness §1. Este cea mai bună **doar relativ**: −0,060 R OOS, CI90 integral sub zero, PF 0,89 — rezultatul TRAIN deja o respinge în fond; a fost rulată pe VALID pentru că protocolul cere o rulare a configurației selectate.

Bundle-uri: `bundle_breakout_taken_cov25_hgb.joblib` (STANDARD), `bundle_ecn_breakout_taken_cov25_hgb.joblib` (ECN, prag 0,300). Comanda runner (fără `--split`):

```
/tmp/claude-0/venv/bin/python research/run_bt.py --db data/research.db --strategy disc-f5-ml --tf H1 --htf H4 \
  --strategy-json '{"rescue_enabled": false, "ml_base": "breakout-v1", "ml_bundle": "research/results/F5/bundle_breakout_taken_cov25_hgb.joblib"}' \
  --risk-json '{"max_open_positions": 4, "max_trades_per_day": 20, "max_same_currency_direction": 2, "max_spread_points": 60, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000}' \
  --exit base --exit-params '{"max_hold_hours": 24}' --workers 1 --json-out research/results/F5/<run>.json
```
(pe ECN: `--db data/research_ecn.db` și `ml_bundle` = `bundle_ecn_breakout_taken_cov25_hgb.joblib`; baseline-ul = aceeași comandă cu `--strategy breakout-v1` și `--strategy-json '{"rescue_enabled": false}'`.)

## 4. VALID (2019-01-01 → 2021-01-01) — o singură rulare per configurație și bază

### 4.1 Portofoliu (runner `run_bt.py --split valid`, 12 instrumente, max 4 poziții) — **numerele Porții 1**

| bază | politică | n | win | avg R | CI90 | PF | max DD (R) | net R | ieșiri SL / BE / TP / TIME | blocaje de piață (din 2 662 candidați) |
|---|---|---|---|---|---|---|---|---|---|---|
| STANDARD | breakout-v1 fără filtru (`valid_std_base_breakout-v1.json`) | 1 706 | 0,437 | −0,1387 | [−0,185; −0,093] | 0,753 | 241,5 | −236,6 | 894 / 301 / 405 / 106 | 368 |
| STANDARD | **filtru ML #15** (`valid_std_ml_breakout-v1.json`) | **1 366** | 0,452 | **−0,1092** | **[−0,162; −0,059]** | **0,799** | 157,2 | −149,1 | 694 / 213 / 359 / 100 | 1 007 (≈ 639 veto ML) |
| ECN | breakout-v1 fără filtru (`valid_ecn_base_breakout-v1.json`) | 1 953 | 0,439 | −0,1106 | [−0,153; −0,065] | 0,799 | 221,3 | −216,0 | 990 / 392 / 463 / 108 | 10 |
| ECN | filtru ML #15 re-antrenat ECN (`valid_ecn_ml_breakout-v1.json`) | 1 054 | 0,477 | −0,0445 | [−0,103; +0,011] | 0,913 | 84,0 | −46,9 | 503 / 154 / 316 / 81 | 1 472 (≈ 1 462 veto ML) |

Pe simboluri (STANDARD, filtru ML, avg R / n): **toate cele 12 negative** — AUDUSD −0,014 (84), AUDJPY −0,016 (88), XAUUSD −0,039 (95), GBPJPY −0,049 (151), EURJPY −0,051 (146), EURGBP −0,056 (121), USDCAD −0,124 (116), USDJPY −0,141 (101), EURUSD −0,151 (151), USDCHF −0,186 (95), EURCHF −0,237 (61), GBPUSD −0,241 (157). Pe ECN cu filtru: 4 pozitive (GBPJPY +0,219 / 89, AUDUSD +0,205 / 67, USDJPY +0,109 / 72, AUDJPY +0,035 / 59), 8 negative.

### 4.2 Nivel de rând (setul de date, `f5_ml.py valid`; CI-urile pe „toți candidații” sunt prea înguste — rândurile se suprapun)

STANDARD (`valid_std_breakout_taken_cov25.json`): 2 294 candidați VALID (2019-01-03 → 2020-12-31), rata de bază P(R > 0) = 0,287.

| politică | rânduri | n | avg R | CI90 | PF | 2019: n / avg R | 2020: n / avg R |
|---|---|---|---|---|---|---|---|
| ia tot | toți candidații | 2 294 | −0,1392 | [−0,178; −0,100] | 0,754 | — | — |
| ia tot | `taken` (tranzacțiile backtestului) | 1 706 | −0,1380 | [−0,181; −0,092] | 0,755 | 808 / −0,224 | 898 / −0,061 |
| HGB+izoton, P > 0,2979 (acoperire 72 %) | toți candidații | 1 655 | −0,1275 | [−0,171; −0,081] | 0,769 | 797 / −0,194 | 858 / −0,065 |
| **HGB+izoton, P > 0,2979** | **`taken`** | **1 229** | **−0,1027** | **[−0,158; −0,051]** | **0,810** | 609 / −0,187 | 620 / −0,020 |
| LogReg, P > 0,2216 (acoperire 79 %) — informativ | `taken` | 1 342 | −0,1342 | [−0,183; −0,085] | 0,758 | 641 / −0,235 | 701 / −0,043 |

Concordanță: „ia tot” pe rândurile `taken` (eticheta `labeler.simulate`) = 1 706 / −0,138, iar backtestul de portofoliu = 1 706 / −0,139 — eticheta setului de date și tranzacția simulată sunt aceeași tranzacție.

Calitatea probabilităților pe VALID (2 294 candidați):

| model | Brier | Brier constantă (rata VALID) | log loss | AUC | ECE |
|---|---|---|---|---|---|
| HGB + izoton (ales) | 0,2023 | 0,2048 | 0,598 | 0,588 | 0,042 |
| LogReg (informativ) | 0,1990 | 0,2048 | 0,586 | 0,612 | 0,029 |

Curba de calibrare HGB (bin P: n / P mediu / frecvență reală): 0,1–0,2: 515 / 0,196 / 0,227; 0,2–0,3: 824 / 0,296 / 0,249; 0,3–0,4: 590 / 0,356 / 0,331; 0,4–0,5: 303 / 0,458 / 0,396; 0,5–0,6: 47 / 0,571 / 0,447. Modelul este rezonabil calibrat (ECE 0,04), ușor supra-încrezător peste 0,4, dar aproape toate probabilitățile stau între 0,2 și 0,45: câștig de Brier față de constantă ≈ 1,2 %. **La RR ≈ 1,5 (TP = 1,5 × lățimea intervalului) o rată de câștig de 0,29–0,40 nu poate da speranță pozitivă după costuri; filtrul ar trebui să separe P ≈ 0,45+ pentru a schimba semnul și nu o face.**

ECN (`valid_ecn_breakout_taken_cov25.json`, model re-antrenat pe ECN TRAIN, prag 0,300, acoperire 45 %): 2 652 candidați, rata de bază 0,285. Ia tot `taken` 1 953 / −0,113 / [−0,157; −0,069] / 0,797 (2019: 967 / −0,176; 2020: 986 / −0,052). HGB `taken` **862 / −0,048 / [−0,112; +0,015] / 0,906** (2019: 420 / −0,131; 2020: 442 / **+0,030**); HGB toți candidații 1 190 / −0,056 / [−0,110; −0,002] / 0,893. AUC 0,599, Brier 0,2013 (constantă 0,2036), ECE 0,026. LogReg (informativ) `taken` 1 718 / −0,086 / 0,841, AUC 0,628.

### 4.3 Modelul de ieșire xs-v1 pe VALID (informativ; configurațiile #18 / ECN #18, evaluate o singură dată)

Rândurile de ieșire există doar pentru tranzacțiile cu cel puțin o bară închisă în poziție (cele închise în bara de intrare nu au nimic de decis), de aceea subsetul este mai bun decât întregul: STANDARD 1 427 tranzacții, ieșiri base −0,008 / PF 0,98. Modelul STANDARD nu a găsit marjă pe validare → **nu închide nimic**: politica = base (−0,008). Regresia pe VALID: R² −0,038, corelație −0,009, eroare de calibrare pe decile 0,127 R (prag Stage D: < 0,05) — inutilizabilă. ECN (marjă 0,0, 1 580 tranzacții): politica +0,047 / CI90 [+0,014; +0,077] / PF 1,18 față de base +0,039 / [−0,012; +0,084] / PF 1,08; R² −0,022, corelație 0,030, calibrare 0,092 R. Diferența de +0,008 R vine din a închide mai repede pozițiile unei strategii fără avantaj (aceeași observație ca în etapa D, regula naivă „închide la bara k”), nu dintr-o predicție reală (corelație 0,03). Nu schimbă verdictul: tranzacțiile cu ≥ 1 bară sunt 73–81 % din total, iar întregul rămâne negativ (−0,045 pe ECN cu filtru).

## 5. Pe ani

`run_bt.py` calculează `by_year` din lista de tranzacții, pe care `app.backtest` o trunchiază la ultimele 300 (`MAX_TRADES_OUT`; valabil și pentru `--trades-out`), deci valorile `by_year` din JSON-urile principale acoperă doar coada eșantionului și **nu sunt folosite**. Tabelul de portofoliu vine din rulări ale aceleiași configurații pe fiecare an calendaristic (`--start/--end`, fișiere `byyear/<run>_<an>.json`; sub-intervale fixe ale VALID, nu o optimizare; pozițiile deschise la 31 decembrie sunt închise forțat, de aceea suma pe ani diferă marginal de rularea întreagă). Nivelul de rând (din §4.2) este dat pentru comparație.

| an | set | bază | politică | n | avg R | CI90 | PF | net R |
|---|---|---|---|---|---|---|---|---|
| 2019 | VALID | STANDARD | breakout-v1 fără filtru | 808 | −0,224 | [−0,287; −0,163] | 0,632 | −181,2 |
| 2020 | VALID | STANDARD | breakout-v1 fără filtru | 898 | −0,062 | [−0,123; +0,000] | 0,881 | −55,4 |
| **2019** | **VALID** | STANDARD | **filtru ML #15** | 661 | **−0,178** | [−0,249; −0,108] | 0,693 | −117,5 |
| **2020** | **VALID** | STANDARD | **filtru ML #15** | 705 | **−0,045** | [−0,110; +0,026] | 0,912 | −31,6 |
| 2019 | VALID | ECN | breakout-v1 fără filtru | 967 | −0,177 | [−0,240; −0,118] | 0,698 | −171,4 |
| 2020 | VALID | ECN | breakout-v1 fără filtru | 986 | −0,045 | [−0,109; +0,017] | 0,912 | −44,5 |
| 2019 | VALID | ECN | filtru ML #15 (re-antrenat ECN) | 513 | −0,130 | [−0,210; −0,047] | 0,768 | −66,8 |
| 2020 | VALID | ECN | filtru ML #15 (re-antrenat ECN) | 541 | +0,037 | [−0,045; +0,116] | 1,078 | +19,8 |

TRAIN (walk-forward OOS, configurația #15, rânduri `taken`, din `train_breakout_taken_cov25.json`): bloc ≈ 2016-03 → 2016-11: 206 / −0,040; ≈ 2016-11 → 2017-07: 254 / −0,049; ≈ 2017-07 → 2018-04: 311 / +0,003; ≈ 2018-04 → 2019-01: 572 / −0,106. Nivel de rând VALID (`taken`, §4.2): 2019 609 / −0,187; 2020 620 / −0,020 (STANDARD); ECN 2019 420 / −0,131; 2020 442 / +0,030.

**0 din 2 ani pozitivi pe STANDARD** (cu sau fără filtru); 2019 este un an foarte slab pentru breakout-v1 pe toate bazele (−0,18 … −0,22 fără filtru), iar filtrul reduce pierderea cu 0,03–0,05 R pe tranzacție fără s-o anuleze. Pe ECN cu filtru, 2020 este marginal pozitiv (+0,037, CI90 [−0,045; +0,116], PF 1,08) — nesemnificativ și pe o singură jumătate a eșantionului.

## 6. Verdict față de Poarta 1 (protocol §4, STANDARD, portofoliu §4.1)

| criteriu | prag | VALID STANDARD, filtru ML #15 | trece? |
|---|---|---|---|
| avg R | > 0 | −0,1092 | **NU** |
| limita inferioară CI90 | > −0,02 | −0,162 | **NU** |
| PF | > 1,10 | 0,799 | **NU** |
| n (intraday) | ≥ 100 | 1 366 | da |
| avg R > 0 în cel puțin un an | — | 2019 și 2020 negative (§5) | **NU** |

**REJECTED.** Aceeași concluzie la nivel de rând (−0,103 pe `taken`, CI90 [−0,158; −0,051], PF 0,81). Pe ECN rezultatul este tot negativ (portofoliu −0,045, PF 0,91, CI90 [−0,103; +0,011]; 2019 negativ, 2020 marginal pozitiv la nivel de rând), deci nu este nici `ECN_ONLY`. Nu există nimic de trimis la verificarea adversarială (§5 din protocol) sau la TEST (Poarta 2); TEST nu a fost rulat.

Răspunsul la întrebarea familiei: **nu** — filtrul fs-v1 nu transformă candidații trend-v1 sau breakout-v1 într-o politică cu speranță pozitivă după costuri. Îmbunătățirea relativă (+0,03 R pe STANDARD, +0,07 R pe ECN, prin refuzul a 40–75 % din candidați) e de același ordin ca în etapa D (+0,04 R) și, pe ECN, se concentrează pe 4 perechi (GBPJPY, AUDUSD, USDJPY, AUDJPY) cu 59–89 tranzacții fiecare — exact genul de regularitate care nu rezistă. Trăsăturile `fs-v1` (momentum, distanțe la EMA, RSI, MACD, ATR, oră, zi, spread, nivelurile candidatului) nu conțin informație despre rezultat (AUC 0,52 pe trend-v1 pe 97 648 candidați OOS; Brier mai slab decât o constantă). Mai mult ML pe aceleași trăsături și aceleași strategii de bază nu este o direcție de cercetare productivă.

## 7. Caveat-uri

1. **Spread modelat, nu istoric** (`research.db` nu are spread real); comision 7 USD/lot pe ambele baze (pe STANDARD o mică dublă contorizare, conservator); fără calendar economic (rezultatele intraday sunt, dacă ceva, optimiste); swap off cu time stop 24 h (TIME = 6–7 % din ieșiri la breakout). Toate în aceeași direcție: realitatea ar fi mai slabă, nu mai bună.
2. **Harness** fixat înainte de prima rulare și identic cu F1 (`max_spread_points` 60, limite de cont dezactivate, rescue off, `base` 24 h). Abatere față de risc-ul minimal din sarcină: `max_spread_points` 60 (justificare §1). Limitele de cont live ar reduce numărul tranzacțiilor fără a schimba semnul speranței.
3. **Tăierea pe timp** (nu `train.walk_forward` pe cuantile) pentru alinierea exactă la 2019-01-01 și **48 h embargo** (etapa D folosea 48 h pe H1); `train.MAX_THREADS` redus la 2 și `choose_threshold` parametrizat prin `functools.partial` în `f5_ml.py` (fără a modifica `app/ml/train.py`). Hiperparametrii HGB sunt cei din `train.HGB_PARAMS` (300 iterații, lr 0,05, 15 frunze, min 40/frunză, L2 1,0), neoptimizați — o grilă de hiperparametri nu ar schimba o AUC de 0,52–0,60 în ceva tranzacționabil.
4. **Rulări VALID**: configurația aleasă (#15) a fost evaluată o singură dată pe fiecare bază și la fiecare nivel (portofoliu + rând). Suplimentar, declarate: (a) LogReg (#16) și modelul de ieșire (#18) au fost evaluate pe VALID la nivel de rând **informativ**, în aceeași comandă (decizie luată la scrierea scriptului, înainte de orice număr VALID), nu ca selecție; (b) cele 4 rulări runner VALID au fost **repetate identic** o dată cu `--trades-out` pentru lista de tranzacții (rezultate identice verificate; lista s-a dovedit trunchiată la 300, fișiere șterse, log păstrat: `valid_trades_rerun.log`), apoi rulate pe cele două sub-intervale anuale (§5). Nicio alegere nu a fost făcută pe baza vreunui număr VALID.
5. **Evaluarea la nivel de rând** pe toți candidații este corelată (candidați pe bare consecutive, aceleași mișcări); de aceea Poarta 1 folosește backtestul de portofoliu, care simulează și locurile eliberate de veto-uri. Cele două evaluări concordă în semn și ordin de mărime.
6. Setul de date `ds_trend_std.joblib` (54 MB) este păstrat pentru reproducere; `ds_trend_ecn` (59 MB) a fost șters pentru că nu a fost folosit (top 3 pe TRAIN sunt toate breakout). Toate seturile se refac determinist cu `research/f5_ml.py build` (≈ 6,5 min trend, 2 min breakout).
7. Pluginul `disc-f5-ml` este doar de cercetare (pachetul `discovery`, nelistat de `names()` / UI / engine live). Nu a fost înregistrat niciun model în `ml_models`; nimic nu afectează comportamentul live.
