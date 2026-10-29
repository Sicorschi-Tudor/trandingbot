# Etapa F — protocol pre-înregistrat: căutarea unui avantaj pe 9 ani × 12 instrumente

Scris **înainte** de orice rulare pe VALID sau TEST. Nu se modifică după vederea rezultatelor; orice abatere se notează explicit în raport.

## 1. Date

- Sursă: `github.com/ejtraderLabs/historical-data` — 12 instrumente (AUDJPY, AUDUSD, EURCHF, EURGBP, EURJPY, EURUSD, GBPJPY, GBPUSD, USDCAD, USDCHF, USDJPY, XAUUSD), M15 / M30 / H1 / H4 / D1, **2012-11 → 2022-03** (9,3 ani), ora serverului unui broker „New York close” (regula `NY` a proiectului), convertită în UTC de `research/build_research_db.py`.
- Baze: `data/research.db` (**cont STANDARD**: spread tipic pe oră, fără comision) și `data/research_ecn.db` (**cont ECN/raw**: spread ≈ 1/5 + comision 7 USD/lot dus-întors). Spread-ul lipsește din sursă: este **modelat** per simbol × oră UTC (`SPREAD_PTS`, `ECN_PTS`, `HOUR_MULT`) — valori conservatoare pentru un broker retail obișnuit, mai largi la rollover (21–23 UTC) și în Asia.
- Slippage 1 punct la stop; swap: tabel implicit −0,5 pip/zi/direcție pentru D1/H4 (`--swap-mode table`), off pentru intraday închis în zi.
- Limitare cunoscută: 12 instrumente (fără indici/petrol), fără calendar economic în backtest (blocarea pe știri nu e simulată — rezultatele intraday sunt, dacă ceva, **optimiste**).

## 2. Împărțire temporală (UTC, fixă)

| Set | Perioadă | Rol |
|---|---|---|
| **TRAIN** | 2013-06-01 → 2019-01-01 (5,6 ani) | explorare, grile de parametri, selectarea UNEI configurații pe familie |
| **VALID** | 2019-01-01 → 2021-01-01 (2 ani) | **o singură rulare** a configurației alese pe TRAIN; poarta 1 |
| **TEST** | 2021-01-01 → 2022-03-04 (14 luni) | **o singură rulare**, doar pentru configurațiile care trec VALID; rulată exclusiv de coordonator (`--allow-test`); poarta 2 |

Agenții de cercetare **nu au voie** să ruleze pe TEST (runner-ul refuză fără `--allow-test`).

## 3. Familii de ipoteze (câte un agent)

| ID | Familie | Ce se testează | Timeframe |
|---|---|---|---|
| F1 | Intraday existente, eșantion mare | trend-v1, meanrev-v1, breakout-v1, pullback-v1 (etapa C le-a respins pe 120 de zile; acum 5,6 ani × 12 instrumente) + politicile de ieșire | H1 (grilă), M15 (doar finaliștii) |
| F2 | Trend D1/H4 pe portofoliu | tsmom-v1, donchian-v1, ma-trend-v1 ținute **simultan** pe 12 instrumente (max_open 12), + `tsmom-v2` (ansamblu de lookback-uri 1/3/6/12 luni, filtru de volatilitate), swap inclus | D1, H4 |
| F3 | Cele 20 de ipoteze de descoperire | disc-H01…H20 pe 5,6 ani (etapa E le-a respins pe date scurte) | TF-ul fiecăreia |
| F4 | Intraday nou: sesiuni | `orb-v1` (breakout al range-ului de deschidere Londra/New York cu filtru ATR și ieșire la oră fixă), `sessmom-v1` (continuarea mișcării din prima oră a sesiunii) | M15, H1 |
| F5 | ML meta-labeling | modelul de intrare fs-v1 (HistGradientBoosting calibrat) pe candidații trend-v1 H1 și breakout-v1 H1: schimbă filtrul ML semnul rezultatului? | H1 |
| F6 | Aur și cross-uri JPY pe H4 | strategiile de trend H4 (etapa C2) pe 9 ani, separat pe XAUUSD și pe cross-urile JPY (instrumente cu trend-uri lungi 2013–2021) | H4 |

Fiecare agent: implementează ce lipsește (plugin nou cu test de fum), rulează grila pe TRAIN cu `research/run_bt.py` (`--workers 1`, pe **ambele** baze), alege **o singură** configurație după regula de mai jos, o rulează **o dată** pe VALID și scrie `research/results/<F>/SUMMARY.md` + JSON-urile rulărilor.

## 4. Reguli de selecție și porți

**Selecție pe TRAIN** (per familie): configurația cu cel mai mare `avg_r` pe STANDARD **dintre cele cu ≥ 150 tranzacții (intraday) / ≥ 60 (D1, H4)** și cu cel mult 24 de configurații încercate; se raportează numărul exact de configurații încercate.

**Poarta 1 — VALID** (STANDARD): `avg_r > 0` **și** limita inferioară CI90 `> −0,02` **și** `PF > 1,10` **și** `n ≥ 100` (intraday) / `≥ 40` (D1/H4) **și** `avg_r > 0` în cel puțin unul din cei doi ani. Se raportează și ECN.

**Poarta 2 — TEST** (STANDARD, o singură rulare): `avg_r > 0` **și** limita inferioară CI90 `> 0` **și** `PF > 1,15` **și** `n ≥ 100` (intraday) / `≥ 30` (D1/H4). Pragul CI90 > 0 (mai strict decât în etapele C–E) compensează testarea multiplă: 6 familii × ≤ 24 configurații.

**Verdict final** `PROMISING` = trece ambele porți **și** supraviețuiește verificării adversariale (§5). Altfel `REJECTED`. O configurație care trece doar pe ECN se raportează ca `ECN_ONLY` (nu se activează pe cont standard).

## 5. Verificare adversarială (înainte de TEST)

Pentru fiecare configurație care trece VALID, trei verificatori independenți încearcă s-o **respingă**: (a) lookahead / eroare de cod în plugin sau în politica de ieșire (decizia folosește doar bare închise? nivelurile folosesc cotația barei curente?), (b) realism al costurilor și artefacte de date (spread-ul modelat, goluri, ore de rollover, umpleri la deschiderea barei), (c) supra-optimizare (mărimea grilei, sensibilitatea la parametri vecini ±20 %, stabilitatea pe ani). Două din trei voturi „respins” → `REJECTED` înainte de TEST.

## 6. Ce înseamnă un PROMISING pentru bani

Un PROMISING nu înseamnă „50 USD pe zi”. Înseamnă: strategia merită testul forward (PAPER apoi DEMO, ≥ 3 luni, ≥ 200 tranzacții) conform `docs/PLAN_PROFITABILITATE.md`, Etapa 5. Venitul așteptat se calculează **abia atunci**, din `avg_r` măsurat forward × risc pe tranzacție × tranzacții pe zi.
