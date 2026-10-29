# STAGE G — Multi-asset trend-following + studiu de știri (news surprise)

Data: 2026-10-29. Status: **AMBELE STUDII: NU SUNT PROMIȚĂTOARE. Nimic de pus pe live/demo ca strategie cu edge.**

Rezumat într-o frază: am testat dacă (1) adăugarea de metale/indici la trend-following sau (2) tranzacționarea surprizelor din calendarul economic dă un edge după costuri retail. Răspunsul e **nu**, în ambele cazuri, pe split-ul pre-înregistrat TRAIN/TEST.

---

## 1. Protocol (comun)

- **Date:** copie read-only `mr.db` (backup sqlite din `data/bot.db`) în scratchpad. Nicio scriere în `data/`, niciun restart, niciun POST/PUT, niciun commit, cod `app/` neatins.
- **Split pre-înregistrat:** TRAIN până la 2021-12-31 (D1) / 2024-06-30 (H4) / 2025-06-30 (H1); TEST după. Parametrii aleși **doar pe TRAIN**, TEST rulat **o singură dată**.
- **Bara de acceptare pe TEST:** avg R > 0, limita inferioară CI90 > -0.03, PF > 1.15, n ≥ 100 (multi-asset) / n ≥ 80 (news).
- **Fără lookahead:** semnal pe închiderea barei, intrare la open-ul barei următoare; SL în interiorul barei, gap peste SL = fill la open (cel mai rău).
- **CPU:** `nice -n 10`, max 2 workers.

Scripturi și rezultate brute (scratchpad, nu în repo):
- `/private/tmp/claude-501/-Users-tudorsicorschi-Documents-tradingBot/02b4cca0-75ff-4ba1-970b-84a88a9fd698/scratchpad/mr/multiasset.py` + `multiasset.json`
- `/private/tmp/claude-501/-Users-tudorsicorschi-Documents-tradingBot/02b4cca0-75ff-4ba1-970b-84a88a9fd698/scratchpad/mr/news.py` + `news.json`, `tchk.py`, `tchk2.py`

---

## 2. Ipoteze de cost (explicite)

**FX, round-trip în puncte:** EURUSD 10, GBPUSD 14, USDJPY 12, AUDUSD 12, USDCAD 16, EURJPY 18, GBPJPY 28, NZDUSD 16, EURGBP 14, EURCHF 18, AUDJPY 18, USDCHF 16, USDMXN 400, USDZAR 600. USDTRY exclus.

**Metale/indici:** 3 × spread-ul median H1 de pe demo-ul live; mărimea punctului dedusă din zecimalele prețurilor arhivate.

| Simbol | Round-trip (puncte) | Mărime punct |
|---|---|---|
| XAUUSD | 24 | 0.01 |
| XAGUSD | 54 | 0.001 |
| US500 | 150 | 0.01 |
| US30 | 360 | 0.01 |
| NAS100 | 300 | 0.01 |
| DE40 | 240 | 0.01 |
| UK100 | 54 | 0.1 |
| JP225 | 18 | 1 |

**Finanțare (swap):**
- Indici, metale, petrol, USDMXN, USDZAR: -0.02% din notional / zi calendaristică, pe ambele direcții.
- Restul FX: -0.5 pip / zi lucrătoare pe ambele direcții (`SWAP_DEFAULT_PTS` din `backtest.py`); în studiul de știri: 5 puncte la rollover 21:00 UTC (×3 miercurea).

**Ce NU e modelat:** lărgirea spread-ului exact la publicarea știrilor (intrarea la 1h după o evită în mare parte), slippage în afară de gap-uri.

---

## 3. Studiul A — Multi-asset trend-following

### Metodă
- Strategiile donchian-v1, ma-trend-v1, tsmom-v1 re-codate din reguli în Python simplu (nu prin `backtest.py` — pot exista mici diferențe).
- 4 seturi de parametri / strategie, alese pe avg R TRAIN (min 50 trade-uri).
- Portofoliu: max 10 poziții, 1 pe simbol; trade sărit dacă există deja ≥2 poziții în aceeași direcție pe active cu corelație > 0.7 (ultimele 120 bare). Risc egal 1R pe trade.
- **XTIUSD exclus din rezultatul principal:** prețurile arhivei sunt între 1.1 și 9.1 (ultimul 4.22) — nu e WTI, maparea simbolului e probabil greșită. Raportat separat: pierde pe TEST în 4 din 6 variante. Deci **nu avem o clasă reală de petrol** în rezultate.
- D1: NAS100 nu are D1 înainte de 2022 → apare doar în TEST. H4: arhiva începe în 2021-05 → TRAIN scurt.

### TRAIN vs TEST (parametri aleși)

| TF / strategie | Parametri | TRAIN n / avgR / PF | TEST n | TEST win | TEST avgR | TEST CI90 | TEST PF | TEST maxDD R | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| D1 donchian | 20/10 | 781 / -0.145 / 0.75 | 522 | 27% | -0.246 | [-0.33, -0.16] | 0.58 | 130 | NU |
| D1 ma-trend | 20/100, trail 3 | 987 / -0.047 / 0.77 | 757 | 27% | -0.011 | [-0.045, 0.024] | 0.94 | 18 | NU |
| D1 tsmom | lookback 126, z 0 | 598 / -0.032 / 0.90 | 406 | 24% | -0.093 | [-0.21, 0.06] | 0.73 | 64 | NU |
| H4 donchian | 100/20 | 964 / -0.179 / 0.74 | 750 | 26% | -0.192 | [-0.30, -0.08] | 0.73 | 212 | NU |
| H4 ma-trend | 50/200, trail 3 | 1588 / +0.006 / 1.03 | 1280 | 24% | -0.035 | [-0.067, -0.001] | 0.85 | 76 | NU |
| H4 tsmom | lookback 756, z 0.5 | 231 / +0.092 / 1.11 | 178 | 13% | -0.356 | [-0.70, 0.07] | 0.62 | 104 | NU |

### TEST avg R pe clase de active (n în paranteză)

| Variantă | FX | Metale | Indici |
|---|---|---|---|
| D1 donchian | -0.30 (365) | +0.07 (39) | -0.19 (118) |
| D1 ma-trend | -0.04 (538) | +0.04 (66) | +0.08 (153) |
| D1 tsmom | -0.19 (298) | +0.76 (25) | 0.00 (83) |
| H4 donchian | -0.23 (516) | +0.60 (60) | -0.36 (174) |
| H4 ma-trend | -0.06 (897) | +0.01 (116) | +0.03 (267) |
| H4 tsmom | -0.47 (144) | +1.91 (13) | -0.97 (21) |

- FX negativ în **toate** variantele.
- Metalele sunt pozitive în TEST peste tot, dar asta vine dintr-un singur episod (raliul aur/argint 2024–26); pe TRAIN au fost în mare parte negative, iar eșantioanele sunt mici (13–116 trade-uri). Nu e edge, e un trend care s-a întâmplat.
- Costurile mănâncă ~0.04R/trade la ma-trend și 0.11–0.36R/trade la celelalte.

### Fezabilitate pe cont de 200 USD (lot minim, SL = 2–3 × ATR20 la volatilitatea curentă)

Mărimile de contract sunt valori retail tipice, **neconfirmate cu brokerul**.

| Simbol | Contract presupus | Risc D1 (USD) | Risc H4 (USD) |
|---|---|---|---|
| XAUUSD | 100 oz, min 0.01 | 160–241 (80–120%) | 61–92 (31–46%) |
| XAGUSD | 5000 oz, min 0.01 | 209–313 | 75–112 |
| US30 | 1 USD/pt, min 0.1 | 116–174 | 45–67 |
| NAS100 | 1 USD/pt, min 0.1 | 104–156 | 45–67 |
| DE40 | 1 EUR/pt, min 0.1 | 72–108 | 27–41 |
| UK100 | 1 GBP/pt, min 0.1 | 41–62 | 13–20 |
| US500 | 1 USD/pt, min 0.1 | 16–23 | 6–9 |
| EURUSD | 100k, min 0.01 | 12–18 | 4–6 |

JP225: mărimea contractului necunoscută, cifre nefiabile. Aur, argint și majoritatea indicilor riscă 20% până la peste 100% din cont pe **un singur** trade la lot minim. Un portofoliu diversificat 1R pe aceste piețe **nu se poate rula pe 200 USD**; încap doar FX și eventual US500/UK100 pe H4.

**Verdict A: NU ESTE PROMIȚĂTOR.** Adăugarea metalelor și indicilor nu face trend-following-ul profitabil după costuri retail.

---

## 4. Studiul B — Surpriza la știri (event study, H1, FX)

### Bug de timp în `calendar_history` (important pentru orice studiu viitor)
`calendar_history.t` e salvat cu offset fix +2h. În ora de vară EU fiecare publicare apare cu **1h prea devreme** (NFP la 11:30 UTC în loc de 12:30). Corecție aplicată: +1h în perioada de vară EU. După corecție, bara care conține publicarea are cel mai mare range în ambele sezoane (vară 2.43×, iarnă 2.72× range-ul median). Barele sunt deja UTC corect. **Orice cod care folosește tabela trebuie să aplice aceeași corecție.**

### Metodă
- Evenimente: imp=3 cu actual și forecast → 4904 evenimente cu z valid: USD 4171, EUR 368, JPY 373, GBP 301, NZD 32, AUD 3, CAD 3, CHF 2. **Eșantionul e aproape tot USD.**
- Surpriză: z = (actual − forecast) / std-ul surprizelor **anterioare** ale aceluiași event_id (min 8 valori anterioare). Evenimente simultane ale aceleiași valute → media z; familia = componenta cu |z| maxim.
- Intrare la open-ul barei H1 de după bara publicării; SL = 1.5 × ATR(14) H1; ieșire la orizont fix (1h / 4h / 24h). Variante: momentum (în direcția surprizei) și fade (contra).
- Istoric H1: oct 2023 – oct 2026; TRAIN până la 2025-06-30. Pragul |z| ales pe TRAIN (cel mai bun avg R cu n ≥ 30). CI90 prin bootstrap pe clustere de timp de publicare (o știre USD = 4 trade-uri corelate).
- Cost mediu ≈ 0.06R/trade.

### Rezultate H1

| Variantă | prag | TRAIN n | TRAIN avgR | TRAIN PF | TEST n | TEST avgR | TEST CI90 | TEST PF | TEST hit |
|---|---|---|---|---|---|---|---|---|---|
| momentum 1h | 1.0 | 273 | -0.082 | 0.74 | 237 | -0.101 | [-0.19, -0.01] | 0.62 | 0.45 |
| momentum 4h | 2.0 | 53 | +0.105 | 1.20 | 40 | +0.219 | [-0.30, 0.86] | 1.69 | 0.50 |
| momentum 24h | 2.0 | 53 | +0.340 | 1.53 | 40 | +0.780 | [-0.50, 2.46] | 2.36 | 0.45 |
| fade 1h | 2.0 | 53 | +0.086 | 1.37 | 40 | -0.024 | [-0.28, 0.24] | 0.91 | 0.45 |
| fade 4h | 0.5 | 675 | +0.013 | 1.03 | 476 | -0.117 | [-0.24, 0.00] | 0.75 | 0.40 |
| fade 24h | 2.0 | 53 | +0.113 | 1.19 | 40 | -0.658 | [-0.91, -0.36] | 0.19 | 0.18 |

Detalii TEST:
- **Momentum 24h |z|>2** e singurul rezultat pozitiv, dar n=40, CI90 [-0.50, +2.46], aproape tot USD (n=32, +1.03) și dintr-o singură familie (OTHER n=21). E zgomot, nu edge.
- **Momentum 1h:** USD n=204, -0.109, PF 0.58. PMI a trecut de la +0.12 (TRAIN) la -0.20 (TEST).
- **Fade 4h:** USD n=420, -0.155, PF 0.67. Subgrupul **PMI fade 4h** e singurul pozitiv și pe TRAIN (n=157, +0.22, PF 1.56) și pe TEST (n=88, +0.153, PF 1.57) — dar a fost găsit **după** ce am văzut datele, nu atinge bara de 80 pe subgrup cu CI și nu are CI propriu. E doar o idee pentru un studiu nou, cu pre-înregistrare proprie.
- NFP, GDP, retail sales aproape lipsesc (n ≤ 6) din cauza etichetării pe componenta cu |z| maxim.

### M5 (suplimentar, în afara split-ului)
M5 există doar din ~iunie 2026 → fără TRAIN; prag fix |z|>1, n=65 pentru fiecare variantă (sub 80):

| Variantă | avg R | PF |
|---|---|---|
| momentum 1h | -0.33 | 0.51 |
| momentum 4h | -0.26 | 0.71 |
| momentum 24h | -0.04 | 0.95 |
| fade 1h | +0.03 | 1.04 |
| fade 4h | +0.13 | 1.16 |
| fade 24h | +0.11 | 1.11 |

CI-uri foarte largi; nu se poate trage nicio concluzie.

### Limite practice
- Intrarea vine după închiderea barei de publicare (până la 1h pe H1). Prima reacție — unde stă de obicei efectul surprizei — nu poate fi prinsă fără date tick. După acel moment, a urma surpriza pierde bani la 1h.
- Forecast-urile MT5 pot fi estimări proprii MetaQuotes, nu consensul pieței → z mai slab.
- Aproape niciun eveniment imp=3 cu forecast pentru AUD/CAD/CHF; majoritatea știrilor non-USD sunt imp=2 (se poate rula `news.py <db> <out> 2`).

**Verdict B: NU ESTE PROMIȚĂTOR.** Nicio variantă nu trece bara pe TEST.

---

## 5. Concluzie onestă

- Împreună cu STAGE C/D/E: **nu am găsit niciun edge robust** nici pe FX, nici pe metale/indici cu trend-following, nici pe surprize de știri, după costuri retail și pe date out-of-sample.
- Singurele „buzunare” pozitive (metale în TEST, momentum 24h la |z|>2, PMI fade 4h) sunt fie un singur episod de piață, fie eșantioane de 40–90 trade-uri găsite post-hoc. A le pune pe bani ar fi curve-fitting.
- Costurile retail (0.04–0.36R/trade) și lotul minim sunt obstacole structurale pentru un cont de 200 USD.

## 6. Ce s-ar putea pune pe demo-ul de 200 USD și cum

**Nicio strategie din STAGE G nu e recomandată pentru deploy ca sursă de profit.** Dacă se dorește totuși ceva util pe demo:

1. **Filtru de știri (risc, nu profit) — recomandat.** Folosind `calendar_history` **cu corecția de timp +1h în vara EU**, botul existent să nu deschidă poziții noi de la −15 min până la +60 min față de evenimentele imp=3 ale valutelor perechii. Studiul arată că bara de publicare are range 2.4–2.7× median și că a intra în prima oră după surpriză pierde (momentum 1h: -0.10R). Nu promite profit, doar evită volatilitate inutilă. Necesită schimbare de cod — separat, cu review.
2. **Paper-tracking (forward test) fără bani, opțional:** H4 ma-trend 50/200, trail 3 × ATR, doar pe FX + US500/UK100 la lot minim, risc ≤ 1% (2 USD) — practic imposibil la lot minim pe majoritatea simbolurilor, deci doar ca jurnal de semnale (log), nu ordine reale. Scop: verificare forward, nu câștig.
3. **Ideea PMI fade 4h** → doar ca pre-înregistrare pentru un studiu nou (reguli fixate acum, evaluate pe date care nu există încă), **nu** deploy.

Pași necesari înainte de orice studiu nou pe aceste active: corectarea mapării XTIUSD, confirmarea mărimilor de contract/lot minim la broker, și corecția de timp în `calendar_history`.
