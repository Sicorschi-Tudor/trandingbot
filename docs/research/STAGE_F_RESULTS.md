# Etapa F — rezultate: 6 familii, 138 de configurații, 9,3 ani × 12 instrumente

Protocol pre-înregistrat: [`STAGE_F_PROTOCOL.md`](STAGE_F_PROTOCOL.md). Date publice (`github.com/ejtraderLabs/historical-data`, M15…D1, 2012-11 → 2022-03) încărcate cu `research/build_research_db.py`; rulări cu `research/run_bt.py` (perioade fixe; TEST blocat fără `--allow-test`). Fiecare familie a fost cercetată de un agent separat (rezumat în `research/results/<F>/SUMMARY.md`, toate rulările în JSON), iar configurația care a trecut VALID a fost atacată de 3 verificatori independenți (`research/results/F3/VERIFY_*.md`).

## Concluzie pe scurt

**0 din 6 familii este PROMISING.** Cinci sunt respinse direct la poarta 1 (VALID). A șasea (F3, `disc-h06`: compresie ATR → bară de expansiune, H4) a trecut poarta 1 la limită (CI90 inferior −0,012 față de pragul −0,02), dar a fost respinsă de 2 din 3 verificatori — iar cu motorul de backtest corectat (vezi §3) nu mai trece nici poarta 1. **TEST-ul nu a fost rulat** pentru nicio configurație (nimic nu l-a meritat). Nicio strategie nu intră în bot ca strategie live.

| Familie | Ce s-a testat | Config. | Aleasă (TRAIN) | VALID STANDARD | VALID ECN | Verdict |
|---|---|---|---|---|---|---|
| **F1** intraday existente | trend-v1, meanrev-v1, breakout-v1, pullback-v1 pe H1 (+M15), ieșiri | 24 | trend-v1 H1/H4, SL=TP=3×ATR, sesiune 07–17 UTC, time-stop 24 h | n 3 047, **−0,076 R**, CI90 [−0,101; −0,053], PF 0,82, ambii ani negativi | −0,042, PF 0,89 | **REJECTED** — toate cele 24 negative pe TRAIN cu CI90 integral sub zero; confirmă etapa C pe un eșantion de 45× mai mare |
| **F2** trend D1/H4 portofoliu | tsmom-v1, donchian-v1, ma-trend-v1 pe 12 instrumente simultan + `tsmom-v2` (ansamblu de lookback-uri, nou) | 24 | tsmom-v1 D1, lookback 126, portofoliu 12 | n 94, **−0,248 R**, CI90 [−0,486; +0,010], PF 0,63 | −0,225, PF 0,66 | **REJECTED** — swap-ul e 55 % din pierdere, dar și fără swap ≈ −0,11 R; ansamblul nou: PF ≤ 0,99 în toate variantele |
| **F3** 20 ipoteze de descoperire | disc-H01…H20 pe TF-ul fiecăreia (+4 vecini H06) | 24 | disc-h06 H4: ATR(5)/ATR(100) < 0,7, bară ≥ 1,3×ATR închisă în extremă, TP 2R, time-stop 48 h | n 149, **+0,134 R**, CI90 [−0,012; +0,278], PF 1,35, ambii ani pozitivi | +0,106, CI90 inf. −0,026 | **trece poarta 1 la limită → RESPINS la verificare (2/3)**; 18 din celelalte 19 ipoteze negative, 12 cu CI90 sub zero |
| **F4** sesiuni (nou) | `orb-v1` (breakout al range-ului de deschidere Londra/NY), `sessmom-v1` (momentum prima oră) | 24 | sessmom H1 Londra, |mișcare| ≥ 0,5×ATR, SL=TP=3×ATR, închidere 16:00 | n 1 862, **−0,034 R**, CI90 [−0,065; −0,003], PF 0,90 | −0,004, PF 0,99 | **REJECTED** — „îmbunătățirea” din grilă e doar diluarea costului prin SL mai larg; 56 % din ieșiri la ora fixă |
| **F5** ML meta-labeling | fs-v1 (HGB + calibrare izotonă) și xs-v1 pe candidații trend-v1 și breakout-v1 H1 | 18 | breakout-v1 H1 filtrat de HGB, prag P > 0,30 | n 1 366, **−0,109 R**, CI90 [−0,162; −0,059], PF 0,80 | −0,045, PF 0,91 | **REJECTED** — filtrul reduce pierderea (+0,03 R STD, +0,07 R ECN) refuzând 40–75 % din candidați, dar nu schimbă semnul; AUC 0,59; modelul de ieșire fără putere predictivă (corelație ≈ 0) |
| **F6** aur + cross JPY H4 | donchian/ma-trend/tsmom pe H4, pe XAUUSD singur, pe cross-uri JPY, pe toate 12 | 24 | donchian-v1 H4, **doar XAUUSD**, canal 100, SL 3×ATR, ieșire canal 20 | n 32, +0,211 R, CI90 [−0,312; +0,794], PF 1,42; 2019 −0,21 / 2020 +0,69 | +0,212 | **REJECTED** — n < 40, CI90 inferior −0,31; tot câștigul din 15 tranzacții din raliul aurului din 2020; alegerea sub-universului după rezultate = selecție |

Toate numerele: `research/results/<F>/SUMMARY.md` și JSON-urile rulărilor (TRAIN complet, VALID o singură dată pe configurație).

## 1. De ce F3 a fost respins deși a trecut poarta 1

Trei verificatori independenți au reprodus întâi rezultatul cifră cu cifră (n 149, +0,134 R), apoi:

- **Lookahead / corectitudine (nu respinge):** direcția vine doar din bare închise; umplerea la deschiderea barei următoare; toate cele 149 de tranzacții re-derivate independent din barele brute, 149/149 identice. Observație: **întregul câștig net (+20 R) vine din cele 82 de ieșiri la time-stop (48 h)**; SL (−45,6 R) și TP (+45,0 R) se anulează.
- **Costuri și date (RESPINGE, încredere mare):** motorul de backtest dădea cotației de decizie spread-ul **orei de deschidere a barei de decizie**, deși decizia se ia la închidere (4 h mai târziu pe H4). Pe cont STANDARD, asta bloca tocmai declanșările din bara 21–01 UTC (spread ×2,2–2,6 la deschidere, ×1,3–1,4 la decizie) — exact tranzacțiile pierzătoare ale strategiei. Pe ECN (filtru aproape inactiv) apar 21 de tranzacții în plus și CI90 pică sub prag. Concentrare: iulie 2020 = 51 % din câștig; 3 TP corelate pe 21.07.2020 = 30 %; marți = 70 % din câștig; fără GBPJPY avg R scade la +0,083.
- **Supra-optimizare (RESPINGE, încredere mare):** configurația e a 24-a din 24, obținută slăbind filtrele până la n ≥ 60 (alegere „pe margine”). Parametrul principal `c` e o pantă monotonă: 0,60 → +0,21 (n 68); 0,70 (ales) → +0,05 (n 370); 0,84 (+20 %) → **−0,043 (n 1 791)**. Slăbirea tuturor filtrelor cu 20 %: **−0,082 R pe 2 659 tranzacții, CI90 integral negativ** — forma generală a ipotezei pierde semnificativ. Concordanța TRAIN→VALID pe simboluri: Spearman 0,01. 2018 negativ în toate variantele; etapa E măsurase H06 la −0,058 pe 2021–2023.

Verdict: **REJECTED înainte de TEST** (2 din 3 voturi). Dacă ideea „compresie → expansiune” se reia, trebuie pre-înregistrată ca ipoteză nouă (ex. compresie adâncă `c` 0,60–0,65, posibil doar SELL), cu date noi.

## 2. Ce s-a confirmat, pe eșantioane mari

- Strategiile intraday clasice (trend EMA/RSI/MACD, mean reversion Bollinger, breakout asiatic, pullback, fade la Londra, fix de 16:00, gap de luni, NR7, PDH/PDL, numere rotunde, spike-uri de știri) pierd **−0,03 … −0,20 R pe tranzacție după costuri** pe mii de tranzacții, în 5,6 ani de TRAIN și 2 ani de VALID. Singurul efect sistematic al grilelor a fost **diluarea costului** (SL mai larg ⇒ R mai puțin negativ), niciodată schimbarea semnului.
- Trend-following pe D1/H4 pe 12 instrumente FX+aur: prea puține tranzacții și negativ după swap; dovezile academice pentru trend sunt pe 50–100 de instrumente din toate clasele de active, nu pe FX singur.
- **ML meta-labeling nu salvează o strategie fără avantaj** (a doua confirmare după etapa D): clasificatorul distinge slab (AUC 0,59), iar modelul de ieșire nu prezice nimic.
- Diferența STANDARD → ECN este exact spread-ul modelat (+0,03 … +0,04 R pe tranzacție): costurile mai mici **reduc pierderea, dar nu creează avantaj**.

## 3. Corecții aduse motorului de backtest (găsite de verificare)

1. **Spread la momentul deciziei** (`app/backtest.py`, `scan_symbol`): în modurile `stats`/`fixed`, cotația de decizie primește acum spread-ul orei în care se deschide bara următoare (= ora închiderii barei de decizie), cum vede botul live; modul `bar` păstrează spread-ul înregistrat al barei (cel al barei următoare nu e cunoscut la închidere). Testat în `tests/test_backtest_decision_spread.py`. Cu motorul corectat, configurația F3: TRAIN n 348 / +0,045 / CI90 [−0,050; +0,142]; **VALID n 143 / +0,101 / CI90 [−0,043; +0,245] / PF 1,25 → nu mai trece poarta 1** (diagnostic post-verdict, `research/results/F3/fixedengine_*.json`).
2. **Lista completă de tranzacții la cerere** (`all_trades`): tabelele pe ani din rapoarte foloseau ultimele 300 de tranzacții; runner-ul cere acum lista completă.
3. Observații operaționale notate de agenți: pe D1/H4 blocajul de rollover (±20 min) și pragul live de spread (30 pct) blochează aproape toate deciziile la orele 21–22 UTC — pentru cercetare au fost relaxate explicit și documentat; pentru live ele rămân (sunt protecții corecte).

## 4. Ce rămâne neacoperit (și de ce contează)

- Universul: 12 instrumente FX + aur. Indici, petrol, obligațiuni, crypto (unde trend-following diversificat are dovezi) nu au fost disponibile ca OHLC în acest mediu (rețea restricționată). Carry (dobânzi pe termen scurt G10) și poziționarea COT nu au putut fi descărcate.
- Spread-ul este modelat (realist, dar nu istoric); nu există calendar economic în backtest (rezultatele intraday sunt, dacă ceva, optimiste).
- Fereastra TEST (2021–2022) rămâne **neatinsă** și poate servi unei etape viitoare cu ipoteze noi pre-înregistrate.

## 5. Implicații pentru bot

Nimic din această etapă nu se activează live. Botul rămâne pe DEMO cu `trend-v1` (demonstrat pierzător) **dezarmat**; valoarea construită în această etapă este infrastructura (bază de cercetare de 9 ani, runner cu perioade fixe, 6 plugin-uri noi de cercetare, 75 de teste noi) și corecția motorului. Direcțiile cu șanse reale sunt cele din `docs/PLAN_PROFITABILITATE.md`, Etapa 2: univers de 20+ instrumente din mai multe clase de active, costuri ECN măsurate, informație dincolo de preț (surpriza știrilor, carry, poziționare).
