# F3 / disc-h06 H4 (c 0,7 · range_mult 1,3 · close_frac 0,3) — verificare adversarială (b): realismul costurilor și artefacte de date

Verificator independent, protocol §5(b). Lentilă: spread-ul modelat (simbol × oră UTC), slippage, swap, goluri și umpleri la deschiderea barei, ore de rollover / deschiderea de duminică / sărbători, concentrare pe simbol / an / lună / câteva tranzacții, știri (nesimulate). Re-rulat **o singură dată** comanda VALID aleasă (STANDARD, cu `--trades-out`) și **o singură dată** comanda VALID raportată pe ECN (aceeași configurație, `--trades-out`, pentru a identifica tranzacțiile diferite). Nicio variantă de configurație nu a fost rulată pe VALID; toate cuantificările de mai jos sunt aritmetică pe cele două liste de tranzacții.

**Vot: RESPING (refuted = true).** Datele sunt curate și nivelul spread-ului modelat nu explică avantajul, dar trecerea Porții 1 (marjă 0,008 R pe CI90) se sprijină pe un **artefact al modelului de cost din backtest**: filtrul `max_spread_points` este evaluat cu spread-ul orei de **deschidere** a barei de decizie (vechi de 4 h), nu cu spread-ul de la momentul deciziei, cum face botul live. Pe STANDARD acest filtru blochează sistematic trigger-ele de la deschiderea Asiei (umplere 01/02 UTC) și o parte din cele de la 09/10 UTC — exact tranzacțiile pierzătoare ale strategiei. Cu paritate live (filtrul la ora deciziei), VALID STANDARD devine **n ≈ 165, avg R ≈ +0,077, CI90 ≈ [−0,058; +0,213], PF ≈ 1,19 → Poarta 1 NU trece** (criteriul CI90 inferior > −0,02). Rularea ECN — care nu are artefactul (1 blocaj) — confirmă independent: +0,106, CI90 inferior −0,026, nu trece nici ea, deși costurile ECN sunt mai mici.

## 1. Reproducere

| Rulare | n | avg R | CI90 | PF | max DD | net R | pe ani |
|---|---|---|---|---|---|---|---|
| VALID STANDARD, re-rulare (`scratchpad/vcosts/valid_rerun.json`) | 149 | +0,134 | [−0,0124; +0,2777] | 1,349 | 9,4 | +19,96 | 2019 +0,148 / 2020 +0,118 |
| VALID ECN, re-rulare (`scratchpad/vcosts/valid_ecn_rerun.json`) | 167 | +0,106 | [−0,0261; +0,2344] | 1,266 | 10,1 | +17,72 | 2019 +0,108 / 2020 +0,104 |

Identice cifră cu cifră cu `valid_H06_N4_all.json` / `valid_ecn_H06_N4_all.json`. Determinist.

## 2. Ce NU explică rezultatul (verificat, în favoarea strategiei)

- **Integritatea datelor**: pentru toate cele 149 de tranzacții bara-trigger H4 este exact agregatul celor 4 bare H1 (O/H/L/C, toleranță 2 puncte) — 149/149, toate cele 12 instrumente. În VALID nu există bare lipsă în timpul săptămânii pe FX (singurele salturi ≠ 4 h sunt weekend-urile și 4 zile de sărbătoare); XAUUSD are 11 salturi de 8 h (ore închise la aur) și niciunul nu atinge o bară-trigger. O singură tranzacție are bara dinaintea trigger-ului la 52 h distanță (weekend + luni, USDJPY 2019-04-08) — normal.
- **Umplerea la deschidere**: golul deschidere-umplere față de închiderea barei de decizie e în medie +0,2 puncte (min −20, max +17) → fără umpleri „norocoase”; `skipped_gap` 0.
- **Outlieri**: max R +2,03, min R −1,11 (TP 2R, SL −1R + costuri). Top 5 % (7 tranzacții) = +13,9 R = 70 % din net, dar este structural pentru un sistem cu TP la 2R (cele 23 TP dau +45,0 R, cele 44 SL −45,6 R; **tot netul vine din 82 ieșiri pe timp, +20,5 R, medie +0,25**). Nu există tranzacții de date aberante.
- **Ore de rollover / duminică / sărbători**: 0 intrări la 21–23 UTC (decizia barei 17–21 cade pe blackout-ul de rollover ±20 min, ca în live), 0 intrări la deschiderea de duminică sau vineri seara. Intrări la 01/05 UTC: 7 tranzacții, +0,13 R total. Perioada 24 dec – 2 ian: 2 tranzacții (+0,86, −1,05).
- **COVID (spread-uri reale de 3–10× în martie 2020, nemodelate)**: 0 tranzacții în martie 2020 (fără compresie), 7 tranzacții pe 20 feb – 15 apr 2020 cu −0,95 R total → regimul nu contribuie la rezultat.
- **Nivelul spread-ului**: SL mediu 643 puncte, spread mediu plătit 19,1 puncte → spread/SL = 3,9 % (median 3,2 %, max 13,8 %). Stres aritmetic pe lista de tranzacții: spread ×1,5 → avg +0,114; ×2 → +0,095; ×3 → +0,056; slippage suplimentar 5 puncte pe ambele picioare → +0,112; 10 puncte → +0,090. Rezultatul **nu** depinde de un spread optimist la primul ordin.
- **Swap**: −3,34 R total (−5 puncte/zi/direcție, triplu miercuri); ordinul de mărime e corect pentru FX și aur pe 2 zile; nu e decisiv. Comisionul de 7 USD/lot e aplicat și pe STANDARD (conservator).
- **Știri** (calendar aproximativ, construit manual: NFP, FOMC, BCE, BoE, BoC 2019–2020; nu există calendar istoric în `research.db`): 8 tranzacții au bara-trigger pe un eveniment al monedei lor, total **−0,82 R**; fără ele avg R urcă la +0,147. Blackout-ul live ±30 min ar elimina 2 intrări (NFP 5 iul 2019, 13:00 UTC = exact 30 min după publicare; +0,26 R). 13 poziții sunt ținute **peste** un eveniment (+8,06 R) — permis și în live (motorul mută la BE doar pozițiile cu ≥ 0,3 R la 5 min de eveniment, nesimulat; efect necunoscut, probabil mic). **Știrile nu explică avantajul**, dar barele-trigger de la 13/14 UTC sunt barele datelor americane de 8:30 ET și botul live va intra în majoritatea cazurilor la 30 min + câteva secunde după publicare (la limita blackout-ului).

## 3. Ce EXPLICĂ trecerea Porții 1: filtrul de spread evaluat cu spread-ul orei greșite

**Mecanism** (`app/backtest.py`, `scan_symbol`, linia ~414): `quote["spread_points"] = spreads[i]`, iar `spreads[sym][i]` = `spread_stats[(sym, hour(t_i))]`, unde `t_i` este ora **deschiderii** barei de decizie. Decizia se ia însă la `t_i + 4 h`. Umplerea plătește spread-ul corect (al barei următoare, verificat de lentila (a) 149/149), dar filtrul `spread > 30` din `_common.finish` / `strategy.py:206-208` vede un spread vechi de 4 h. În live (`app/strategy.py:206-208`) spread-ul vine din cotația curentă, la momentul analizei.

Pe H4 efectul e sistematic, nu zgomot:
- bara 21–01 UTC (deschisă la rollover, ×2,2 / ×2,6): decizia la 01/02 UTC e filtrată cu spread ×2,2 → blocată pentru 9 din 12 instrumente pe STANDARD; live, la 01/02 UTC spread-ul modelat e ×1,4 / ×1,3 → trec 8 din 12 instrumente (toate cu bază ≤ 21 puncte);
- bara 05–09 UTC (×1,2): GBPJPY (38,4) și AUDJPY (31,2) blocate pe STANDARD; live, la 09 UTC (×0,9) → 28,8 / 23,4, trec.

**Cuantificare pe VALID**, fără nicio rulare nouă de configurație: lista ECN (unde filtrul blochează 1 candidat) are 21 de tranzacții în plus față de STANDARD (umplere: 6 la 01 UTC, 6 la 02 UTC, 5 la 09 UTC, 2 la 10 UTC, 1 la 05, 1 la 17) cu **−6,74 R** pe ECN; 3 tranzacții sunt doar pe STANDARD (+1,46 R, efect de portofoliu). Aplicând filtrul live (spread STANDARD la ora deciziei ≤ 30) → **16 din 21 ar fi fost luate și în live pe STANDARD**; recostate cu spread STANDARD (diferența de spread / distanța SL) dau **−7,31 R** (10 SL, 6 TIME, 0 TP). Lista STANDARD + aceste 16 tranzacții:

| Varianta | n | avg R | CI90 (bootstrap, 4 000) | PF | Poarta 1 |
|---|---|---|---|---|---|
| Raportată (filtru cu spread vechi de 4 h) | 149 | +0,134 | [−0,012; +0,278] | 1,35 | trece (marjă 0,008) |
| **Paritate live (16 tranzacții adăugate)** | **165** | **+0,077** | **[−0,058; +0,213]** | **1,19** | **NU trece (CI90 inf. −0,058 < −0,02)** |
| Toate cele 21 adăugate (limită superioară a efectului) | 170 | +0,074 | [−0,054; +0,209] | — | NU trece |
| ECN raportat (fără artefact, costuri mai mici) | 167 | +0,106 | [−0,026; +0,234] | 1,27 | NU trece |

Estimarea e aproximativă (interacțiuni de portofoliu: cu pozițiile suplimentare deschise, 2–3 dintre tranzacțiile STANDARD ar putea fi blocate de `max_same_currency_direction` sau de limita de poziții), dar marja e mare (−0,058 vs −0,020) și semnul e confirmat de două surse independente: rularea ECN pe VALID și rularea TRAIN cu filtrul ridicat (lentila (a): +0,053 → +0,036 cu 48 de tranzacții în plus la ≈ −0,10 R). Trigger-ele de la deschiderea Asiei sunt **pierzătoare în mod consecvent** pe TRAIN și pe VALID; rezultatul STANDARD raportat le exclude din cauza unui detaliu al implementării backtest-ului, nu al strategiei.

Nu este lookahead (informație veche, nu viitoare) — de aceea lentila (a) nu a respins. Este însă exact un artefact de **realism al costurilor**: modelul de cost al backtest-ului filtrează mai strict decât va filtra botul live, și o face selectiv asupra celor mai slabe tranzacții.

## 4. Concentrare (agravează, nu decide singură)

- **Pe lună**: iulie 2020 = **+10,12 R din +19,96 (51 %)** pe 12 tranzacții; fără iulie 2020 avg R = +0,072 (n 137). 21 iul 2020 (mișcare USD după acordul UE): AUDUSD, XAUUSD, AUDJPY toate TP în aceeași zi ≈ +6 R = 30 % din net, trei poziții corelate pe aceeași mișcare.
- **Pe zi a săptămânii**: intrările de **marți** sunt 66 din 149 (44 %, prin construcție: barele de luni sunt comprimate) și aduc **+14,0 R (70 %)**; joi +8,5 R; **luni, miercuri, vineri împreună −2,6 R**.
- **Pe oră**: umplerile de 13/17 UTC (grila de vară) 78 tranzacții +14,7 R; umplerile echivalente de iarnă 14/18 UTC 39 tranzacții +1,2 R; 09 UTC 23 tranzacții +0,03 R.
- **Pe simbol**: fără GBPJPY avg R = +0,083 (n 136); fără GBPUSD +0,102; fără USDCAD +0,107; fără USDJPY +0,200. Trei instrumente aduc tot netul (identic cu SUMMARY §4).
- **Pe ieșire**: SL și TP se anulează; avantajul = drift mediu +0,25 R pe 82 ieșiri pe timp la 48 h.

Un avantaj care trăiește într-o singură lună, într-o singură zi a săptămânii și pe 3 instrumente din 12, pe un eșantion de 149, nu e separabil de zgomot — dar asta e lentila (c); aici o notez doar ca context.

## 5. Concluzie pe lentila (b)

- Date: curate (H4 ≡ agregat H1, fără goluri anormale, fără outlieri, fără umpleri la gol).
- Nivelul costurilor: rezultatul supraviețuiește spread ×2 și slippage +10 puncte; swap și comision modelate conservator; COVID și sărbătorile nu contribuie; știrile nu explică avantajul.
- **Dar**: filtrul de spread al backtest-ului folosește spread-ul orei de deschidere a barei de decizie (vechi de 4 h) și blochează pe STANDARD exact tranzacțiile de la deschiderea Asiei / 09 UTC pe care botul live le-ar lua și care pierd consecvent (−0,35 R/tranzacție pe VALID, −0,10 pe TRAIN). Corectat, VALID STANDARD cade la ≈ +0,077 cu CI90 inferior ≈ −0,058 → **Poarta 1 nu mai trece**; ECN (fără artefact) nu trece nici ea. Trecerea raportată cu marjă de 0,008 R este produsul artefactului.
- **Vot: RESPING.** Recomandare: corectat `app/backtest.py` (spread-ul cotației de decizie la `hour(t_i + sec)`, adică ora barei de umplere), refăcută **selecția pe TRAIN** (clasamentul se poate schimba: H06_N4 scade la ≈ +0,036 cu filtrul ridicat), și abia apoi o singură rulare pe VALID; alternativ, dacă ipoteza H06 se reia, declarat explicit în plugin un filtru de sesiune care exclude barele deschise la 21–01 UTC (ceea ce face implicit artefactul actual), ca regulă pre-înregistrată, nu ca efect secundar.

## 6. Fișiere

Doar acest raport în repo. În scratchpad (`/tmp/claude-0/-home-user-trandingbot/005b2e16-62ce-5573-b4b2-692c31da423d/scratchpad/vcosts/`): `valid_rerun.json`, `valid_trades.jsonl` (STANDARD), `valid_ecn_rerun.json`, `valid_ecn_trades.jsonl` (ECN), `analyze.py` (distribuție, ore, luni, stres de cost), `parity.py` (paritatea filtrului de spread), `gaps.py` (audit goluri / agregat H1), `news.py` (calendar aproximativ). Niciun fișier partajat modificat; nimic comis; TEST neatins.
