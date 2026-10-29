# MT5 AI Bot — descriere completă, de la A la Z

Acest document descrie **tot** ce face botul: arhitectura, fiecare componentă, drumul complet al unei tranzacții, tehnicile folosite (indicatori, strategii, gestiunea riscului, simulare, backtest, ML, AI), siguranța, datele stocate, interfața și rezultatele cercetării. Este scris pe baza codului din acest depozit (`app/`, `mt5/`, `web/`, `tests/`, `docs/`).

---

## Cuprins

1. [Ce este botul, pe scurt](#1-ce-este-botul-pe-scurt)
2. [Arhitectura](#2-arhitectura)
3. [Modurile de lucru](#3-modurile-de-lucru)
4. [Drumul complet al unei tranzacții (A → Z)](#4-drumul-complet-al-unei-tranzacții-a--z)
5. [Puntea MT5 — Expert Advisor-ul `AIBridgeEA.mq5`](#5-puntea-mt5--expert-advisor-ul-aibridgeeamq5)
6. [Datele de piață pe server](#6-datele-de-piață-pe-server)
7. [Indicatorii tehnici](#7-indicatorii-tehnici)
8. [Strategia live `trend-v1`](#8-strategia-live-trend-v1)
9. [Filtrele de context: sesiune, știri, rollover, corelații](#9-filtrele-de-context-sesiune-știri-rollover-corelații)
10. [Arbitrajul semnalelor (înainte de AI)](#10-arbitrajul-semnalelor-înainte-de-ai)
11. [Claude — evaluatorul AI](#11-claude--evaluatorul-ai)
12. [Risk Engine — toate verificările](#12-risk-engine--toate-verificările)
13. [Calculul volumului (position sizing)](#13-calculul-volumului-position-sizing)
14. [Execuția: intenții, comenzi, reconciliere](#14-execuția-intenții-comenzi-reconciliere)
15. [Simulatorul PAPER și feed-ul SIMULAT](#15-simulatorul-paper-și-feed-ul-simulat)
16. [Gestionarea pozițiilor deschise](#16-gestionarea-pozițiilor-deschise)
17. [Închiderea tranzacției, contabilitate și performanță](#17-închiderea-tranzacției-contabilitate-și-performanță)
18. [Telegram](#18-telegram)
19. [Dashboard-ul web](#19-dashboard-ul-web)
20. [API-ul](#20-api-ul)
21. [Baza de date](#21-baza-de-date)
22. [Arhiva de istoric și calendarul economic istoric](#22-arhiva-de-istoric-și-calendarul-economic-istoric)
23. [Etichete „umbră” (shadow labels)](#23-etichete-umbră-shadow-labels)
24. [Backtest walk-forward](#24-backtest-walk-forward)
25. [Strategiile de cercetare (plugin-uri)](#25-strategiile-de-cercetare-plugin-uri)
26. [Politicile de ieșire de cercetare](#26-politicile-de-ieșire-de-cercetare)
27. [Testele forward (PAPER)](#27-testele-forward-paper)
28. [Stratul de Machine Learning](#28-stratul-de-machine-learning)
29. [Metodologia de cercetare și rezultatele](#29-metodologia-de-cercetare-și-rezultatele)
30. [Securitate și reguli de siguranță](#30-securitate-și-reguli-de-siguranță)
31. [Configurare (`.env`)](#31-configurare-env)
32. [Teste automate](#32-teste-automate)
33. [Istoricul dezvoltării (etape)](#33-istoricul-dezvoltării-etape)
34. [Limitări și probleme cunoscute](#34-limitări-și-probleme-cunoscute)
35. [Glosar](#35-glosar)

---

## 1. Ce este botul, pe scurt

Un **sistem de trading semi-automat pentru MetaTrader 5**, construit pentru a fi testat pe un **cont DEMO**. Botul:

- primește în timp real cotații, bare, contul și pozițiile din MT5, printr-un Expert Advisor (EA) care vorbește HTTP cu un server Python;
- analizează piața cu o **strategie deterministă** (fără „ghicit”): indicatori EMA, RSI, MACD, ATR pe două timeframe-uri;
- trimite fiecare semnal la **Claude** (modelul AI Anthropic), care îl explică în română și poate pune **VETO** — dar nu poate niciodată deschide un ordin sau schimba nivelurile;
- trece fiecare ordin prin un **Risk Engine** determinist cu peste 20 de verificări;
- execută ordinul (simulat în PAPER sau real pe contul demo în DEMO), îl protejează mereu cu Stop Loss și Take Profit, apoi îl gestionează (break-even, time-stop, închidere la sfârșitul sesiunii, ieșire „rescue”);
- trimite notificări pe **Telegram** și afișează totul într-un **dashboard web**;
- are în paralel o întreagă **platformă de cercetare**: arhivă de istoric multi-an, backtest walk-forward care folosește exact codul live, 30+ strategii testate, politici de ieșire, teste forward și un strat **ML** cu modele care pornesc „în umbră” și acționează doar după aprobare umană.

Concluzia onestă a cercetării de până acum: **nicio strategie testată nu are avantaj statistic după costuri**, deci botul rămâne pe DEMO. Modul LIVE (bani reali) este blocat în cod.

Cifre: ~35 000 de linii, ~60 de module Python, EA MQL5 de ~1 800 de linii, dashboard de ~3 000 de linii, **608 teste automate** (toate trec).

---

## 2. Arhitectura

```
┌───────────────────────────┐        HTTP POST la 500 ms          ┌─────────────────────────────────────────────┐
│  MetaTrader 5 (cont demo) │  ── /bridge/v1/sync (JSON) ──────▶  │  Server Python (FastAPI + uvicorn)          │
│  EA AIBridgeEA.mq5 v1.14  │  ◀── răspuns text: comenzi ───────  │  un singur proces, port implicit 8787       │
│  (doar execută comenzi)   │      OPEN / MODIFY / CLOSE / HIST…  │                                             │
└───────────────────────────┘                                     │  bridge.py   → state.py (memorie, EventBus)  │
                                                                  │  engine.py   → bucla principală (1 s)        │
                                                                  │    ├ strategy.py   (trend-v1)                │
                                                                  │    ├ ai_claude.py  (Claude: explică / VETO)  │
                                                                  │    ├ risk.py       (Risk Engine)             │
                                                                  │    ├ executor.py   (intenții → comenzi)      │
                                                                  │    ├ paper.py      (broker simulat)          │
                                                                  │    ├ position_manager.py                     │
                                                                  │    ├ forward.py    (teste forward PAPER)     │
                                                                  │    └ ml_live.py    (ML umbră / aprobat)      │
                                                                  │  telegram.py → api.telegram.org              │
                                                                  │  api.py      → /api/v1/* + SSE /stream       │
                                                                  │  db.py       → SQLite (WAL) data/bot.db      │
                                                                  └──────────────┬──────────────────────────────┘
                                                                                 │ servește web/
                                                                  ┌──────────────▼──────────────┐
                                                                  │ Dashboard (HTML + JS simplu)│
                                                                  │ http://127.0.0.1:8787       │
                                                                  └─────────────────────────────┘
```

**De ce un EA și nu pachetul Python `MetaTrader5`?** Pachetul oficial merge doar pe Windows. EA-ul MQL5 rulează în MT5 pe orice platformă (Mac, Windows, VPS) și comunică prin `WebRequest`.

**Tehnologii:** Python 3.11+ (testat pe 3.13), FastAPI, Pydantic Settings, uvicorn, `anthropic` SDK, httpx, SQLite în mod WAL, numpy + scikit-learn (doar pentru ML), JavaScript simplu fără framework, MQL5.

**Principiu de proiectare:** toate deciziile de trading sunt **deterministe și reproductibile**. AI-ul și ML-ul pot doar **reduce** riscul (veto, volum mai mic) — cu o singură excepție controlată: un model ML aprobat manual poate mări riscul până la un plafon strict.

**Timp:** totul se stochează și se transportă în **UTC**; interfața afișează ora din `DEFAULT_TIMEZONE` (implicit Europe/Chisinau), cu trecerea la ora de vară tratată automat.

---

## 3. Modurile de lucru

| Mod | Ce face |
|---|---|
| `SIGNAL_ONLY` | Analizează și produce semnale, dar nu execută nimic. |
| `PAPER` | Execută ordinele într-un **broker simulat intern** folosind bid/ask-ul live. Nu atinge MT5. |
| `DEMO` | Trimite ordine **reale** către contul MT5 — **doar** dacă EA-ul raportează `trade_mode == 0` (cont DEMO). Orice altceva e blocat. |
| `LIVE` | Există doar ca nume; serverul îl **refuză** (403). |

Pe lângă mod, există comutatorul **Execuție: armată / dezarmată** (pornește mereu dezarmată) și **Pauză intrări noi** (kill switch). După o repornire, botul **nu** revine singur în DEMO armat decât dacă `.env` spune explicit `TRADING_MODE=DEMO` și `EXECUTION_ENABLED=true`.

Sursa de date: `DATA_SOURCE=mt5_bridge` (real, de la EA) sau `sim` (feed **SIMULAT**, marcat clar peste tot, permite doar PAPER).

---

## 4. Drumul complet al unei tranzacții (A → Z)

1. **Sincronizare EA.** La fiecare 500 ms EA-ul trimite cotațiile, ultimele 3 bare pe fiecare timeframe cerut, contul, toate pozițiile, deal-urile din ultimele 24 h și rezultatele comenzilor executate.
2. **Ingestie.** `bridge.py` verifică cheia `X-Bridge-Key`, corectează decalajul de ceas al EA-ului, actualizează starea în memorie (`state.py`), detectează golurile de istoric și cere reumplere (`HIST`).
3. **Bucla motorului** (`engine.loop_once`, o dată pe secundă): salvează barele, actualizează equity-ul de referință, verifică sesiunea, starea bridge-ului și calendarul economic.
4. **Bară nouă închisă.** Pentru fiecare simbol activ, când se închide o bară nouă pe `SIGNAL_TIMEFRAME` (implicit M5), rulează strategia — doar pe bare **închise**, doar dacă bara e proaspătă.
5. **Analiza** (`strategy.analyze`): indicatori → scor → direcție BUY/SELL/HOLD → intrare, SL, TP, regim de piață, motive. Fiecare analiză e salvată în tabelul `analyses` (inclusiv cele blocate).
6. **Filtre.** Filtre de piață (spread, vechimea tick-ului, ATR, distanța minimă a brokerului) și filtre de context (sesiune, poziție existentă, limită de semnale, știri, corelație). Dacă un filtru blochează → HOLD cu motivul scris.
7. **Semnal.** Dacă direcția e BUY/SELL, se creează un semnal unic (cheia: simbol + timeframe + ora barei + direcție), cu expirare după 15 min.
8. **Arbitraj pre-AI.** Semnalele aceleiași bare sunt sortate (aliniate cu trendul mare primele, apoi costul cel mai mic) și trec printr-o simulare a Risk Engine-ului; cele care oricum ar fi respinse nu mai ajung la Claude (economie de bani și timp).
9. **Claude.** Primește datele structurate și răspunde AGREE / CAUTION / VETO + explicație în română + riscuri + condiția de invalidare. VETO → NO_TRADE.
10. **Execuție.** Se verifică: modul permite execuția, semnalul a fost creat după armare, nu a expirat, prețul nu s-a mutat mai mult de 50% din distanța SL.
11. **Poarta ML** (doar dacă există un model aprobat — implicit nu există): poate respinge intrarea sau ajusta factorul de risc.
12. **Risk Engine.** Toate verificările trebuie să treacă; calculează volumul exact.
13. **Intenție.** `executor.py` creează o intenție. PAPER → umplere imediată în simulator. DEMO → comanda `OPEN` se pune în coadă și pleacă în răspunsul la următoarea sincronizare.
14. **EA execută.** Verifică că nu a expirat (20 s) și că prețul nu a derivat, rulează `OrderCheck`, apoi `PositionOpen` cu SL și TP, magic `552211` și comentariul `ai:<id>`.
15. **Confirmare.** Rezultatul vine înapoi; intenția devine FILLED / FAILED. Dacă nu vine nimic în 30 s → UNKNOWN + alertă, apoi reconciliere după comentariul poziției.
16. **Gestionare.** `position_manager` urmărește poziția: break-even, ieșire la știri, rescue, time-stop (240 min), închidere la sfârșitul sesiunii. Nu lărgește niciodată SL-ul.
17. **Închidere.** Prin SL, TP sau decizia botului. Deal-ul de ieșire e preluat, tranzacția e salvată cu profit, comision, swap, rezultatul în R, MFE și MAE.
18. **Notificări și statistici.** Telegram pentru fiecare pas important; dashboard-ul se actualizează live (SSE); statisticile de performanță se recalculează.
19. **Etichetare umbră.** Și setup-urile **neexecutate** (blocate, cu veto) sunt simulate ulterior, ca să știm ce s-ar fi întâmplat.
20. **Învățare offline.** Noaptea (22:30), un proces separat reantrenează modelele ML pe o copie a bazei; modelele noi intră doar „în umbră”.

---

## 5. Puntea MT5 — Expert Advisor-ul `AIBridgeEA.mq5`

**Versiune 1.14.** Se atașează pe **un singur** grafic. Rolul lui: transmite date și execută **doar** comenzile primite de la server — **nu tranzacționează niciodată singur**.

### Ce trimite (la fiecare `OnTimer`, implicit 500 ms)
- `terminal` (conectat, trading permis, build), `account` (login, server, broker, `trade_mode`, sold, equity, marjă, levier, trade_expert);
- `symbols`: bid, ask, ora tick-ului, spread, point, digits, volum min/max/pas, `stops_level`, `freeze_level`, `tick_value`, `tick_size`, contract, mod de umplere;
- `bars`: ultimele 3 bare (formare + 2 închise) per simbol × TF; istoric complet (`InpHistoryBars`, 300) când serverul cere `HIST`;
- `positions`: **toate** pozițiile (serverul le filtrează după magic);
- `deals`: deal-urile cu magicul botului din ultimele 24 h (max 50);
- `results`: rezultatele comenzilor, păstrate într-un buffer până primește HTTP 200;
- `instance_id` (detectează mai multe EA-uri pornite simultan), `srv_offset` (ora serverului brokerului față de GMT);
- `calendar`: evenimentele economice următoare (importanță ≥ moderată), la fiecare 10 min;
- `archive`: loturi de bare istorice la cererea `HISTX`;
- `calendar_hist`: istoricul calendarului economic cu cifre (actual / prognoză / anterior) la cererea `CALHIST`.

### Ce primește (răspuns text, o instrucțiune pe linie)
```
OK|<ora_server_utc>
SYMBOLS|EURUSD,GBPUSD
TF|M1,M5,M15,H1
HIST|EURUSD|M5
OPEN|<cmd_id>|<simbol>|BUY|<volum>|<sl>|<tp>|<deviație>|<comentariu>|<preț_ref>|<max_slip>|<expiră_utc>
MODIFY|<cmd_id>|<ticket>|<sl>|<tp>
CLOSE|<cmd_id>|<ticket>|<volum>
CALENDAR
HISTX|<simbol>|<tf>|<end_utc>|<număr>
CALHIST|<de_la>|<până_la>|<importanță_min>
```

### Tehnici de siguranță în EA
- **Idempotență:** ține minte ultimele 500 de `cmd_id` executate și **nu execută niciodată aceeași comandă de două ori**.
- **Expirare:** un OPEN mai vechi de 20 s este refuzat (`TIMEOUT (expired)`), fără să ajungă la broker.
- **Protecție la derivă de preț:** dacă ask/bid s-a mutat față de prețul de referință mai mult de `max(5, 25% din SL)` puncte → refuzat (`PRICE_CHANGED`).
- `OrderCheck` înainte de fiecare ordin; tipul de umplere ales automat (FOK → IOC → RETURN); prețurile normalizate la `digits`.
- MODIFY și CLOSE se aplică **doar** pozițiilor cu magicul botului.
- Conversia orei serverului brokerului în UTC, inclusiv regula DST (`MT5_SERVER_TZ`: auto / EET / NY / fix / zonă IANA).
- Descărcările lente de istoric rulează **după** executarea comenzilor, ca să nu întârzie cotațiile sau ordinele.
- Mesaje clare de diagnostic: eroarea 4014 (URL nepermis în WebRequest), 401 (cheie greșită).

---

## 6. Datele de piață pe server

- `bridge.py` validează cheia (comparare sigură), limitează corpul la 5 MB, normalizează ceasul (corectează eroarea lui `TimeGMT()` din EA) și marchează bridge-ul **conectat** dacă ultima sincronizare are sub 5 s.
- `state.py` ține în memorie cotațiile, barele pe fiecare (simbol, TF), contul, pozițiile, ultima analiză și un **EventBus** care alimentează dashboard-ul prin Server-Sent Events.
- **Bare închise vs. bara în formare:** strategia folosește numai bare închise (`ora_deschiderii + durata_TF ≤ acum`).
- **Goluri:** dacă lipsesc bare, serverul cere reumplere (`HIST`) și nu analizează simbolul până se completează.
- **Calitatea cotației:** OK / STALE (tick vechi) / NO_DATA.
- **Statistici de spread:** spread-ul fiecărui simbol e eșantionat la 10 s și agregat pe oră UTC (n, medie, abatere, maxim) în `spread_stats` — folosit apoi de simulări ca model realist de cost.
- Simbolurile de cercetare (aur, indici, petrol, crypto…) sunt doar arhivate, **niciodată** cotate, analizate sau tranzacționate.

---

## 7. Indicatorii tehnici

Implementați în Python pur (`app/indicators.py`), fiecare întoarce o listă aliniată cu prețurile (`None` unde nu e definit):

| Indicator | Folosire |
|---|---|
| **EMA** (medie mobilă exponențială) 20, 50, 200 | trend, distanța prețului față de medie, panta |
| **RSI(14)** (Wilder) | zone de supracumpărare / supravânzare, impuls |
| **ATR(14)** (Average True Range, Wilder) | volatilitate, distanțele SL/TP, filtre, normalizarea feature-urilor |
| **MACD(12, 26, 9)** — histograma | accelerarea impulsului |
| **Bollinger Bands** (SMA ± k·σ) | strategia mean-reversion, lățimea benzii ca feature ML |
| **Canale Donchian** (max/min pe N bare) | strategia donchian, ieșirea „channel” |
| Randamente logaritmice, volatilitate | strategia de momentum (TSMOM) |

---

## 8. Strategia live `trend-v1`

Strategie **de urmărire a trendului**, deterministă, pe M5 cu confirmare pe H1 (`app/strategy.py`).

**Date necesare:** ≥ 100 de bare închise pe M5 și ≥ 60 pe H1.

**Trendul mare (HTF):** EMA(50) pe H1 și panta ei pe 3 bare → `UP` / `DOWN` / `FLAT`.

**Scor — 5 componente pentru LONG (+1 fiecare):**
1. EMA20 > EMA50;
2. închiderea > EMA20;
3. histograma MACD > 0 **și** în creștere față de bara anterioară;
4. 50 < RSI < 70;
5. trendul H1 = UP.

SHORT e simetric (EMA20 < EMA50, sub EMA20, MACD < 0 și în scădere, 30 < RSI < 50, H1 = DOWN). `scor = (long − short) / 5`, în intervalul [−1, 1].

**Decizie:** BUY dacă scorul long ≥ `MIN_SCORE_COMPONENTS` (4) **și** H1 nu e DOWN; SELL simetric; altfel HOLD.

**Niveluri:**
- intrare = ask (BUY) / bid (SELL);
- SL = intrare ∓ `ATR_SL_MULT` (1.5) × ATR, cu un minim `MIN_SL_POINTS` (60 de puncte);
- TP = intrare ± `ATR_TP_MULT` (2.5) × ATR → raport risc/câștig ≈ 1 : 1.67;
- distanța trebuie să respecte `stops_level` al brokerului (+5 puncte), altfel HOLD.

**Probabilități (euristice, NECALIBRATE — marcate explicit):** `p_direcție = 0.5 + 0.25 × |scor|`, `p_opus = (1 − p_direcție) × 0.6`, neutru = restul.

**Regimul pieței:** `HIGH_VOL` dacă ATR > 2 × mediana ATR pe 100 de bare; altfel `TREND_UP` / `TREND_DOWN` dacă |EMA20 − EMA50| > 0.5 × ATR; altfel `RANGE`.

**Filtre de piață → HOLD:** spread > maxim; spread > 20% din distanța SL; tick mai vechi de `MAX_TICK_AGE_SECONDS`; ATR = 0; distanță sub nivelul minim al brokerului.

**Filtre de context → HOLD:** sesiune închisă; există deja o poziție a botului pe simbol; s-a atins numărul maxim de semnale pe simbol în sesiune (5); știre importantă în fereastră; conflict de corelație valutară.

**Altele:** expirare semnal 15 min, orizont 60 min, fără semnale duplicate. Strategia activă poate fi schimbată din dashboard cu altă strategie intraday din registru (meanrev, breakout, pullback) — implicit trend-v1.

---

## 9. Filtrele de context: sesiune, știri, rollover, corelații

- **Fereastra de sesiune** (`scheduler.py`): implicit 09:00–13:00 ora Chișinăului, luni–vineri, cu ora de vară corectă (zoneinfo). Există override manual (start/stop forțat). La deschidere se trimite un mesaj Telegram; la închidere nu mai intră poziții noi, dar cele deschise rămân gestionate.
- **Blocare la știri:** ±`NEWS_BLACKOUT_MINUTES` (30) în jurul evenimentelor cu importanță ≥ `NEWS_MIN_IMPORTANCE` (3 = mare) pentru oricare dintre cele două monede ale perechii. Sursa: calendarul economic din MT5, trimis de EA. Dacă un calendar proaspăt lipsește, serverul cere `CALENDAR`.
- **Rollover-ul FX** (17:00 New York, cu DST): fără intrări în ±`ROLLOVER_BLACKOUT_MINUTES` (20) — spread-urile sunt mari și swap-ul se aplică.
- **Corelații valutare:** fiecare pereche e descompusă în „picioare” (EURUSD BUY = EUR long + USD short). Nu se deschide o poziție dacă aceeași monedă e deja ținută în aceeași direcție de `MAX_SAME_CURRENCY_DIRECTION` (1) ori. Sufixele brokerului (`.a`, `m`) sunt ignorate.
- **Pauză după pierdere pe simbol:** `COOLDOWN_AFTER_LOSS_MINUTES` (30).
- **Pauză după serie de pierderi:** după `MAX_CONSECUTIVE_LOSSES` (3) → pauză de `LOSS_STREAK_PAUSE_MINUTES` (90).
- **Încălzire (warm-up):** după pornire sau reconectare, botul așteaptă date suficiente și stabile înainte să analizeze.

---

## 10. Arbitrajul semnalelor (înainte de AI)

Când mai multe simboluri dau semnal la aceeași bară, motorul:
1. le **sortează determinist**: întâi cele aliniate cu trendul H1, apoi cele cu cel mai mic raport `spread / distanța SL` (cost relativ), apoi alfabetic;
2. le admite pe rând cu o **simulare a Risk Engine-ului** care consideră deja „deschise” și semnalele admise înaintea lor, plus cele care încă așteaptă răspunsul Claude (rezervare de expunere);
3. cele respinse devin NO_TRADE cu `ai.status = SKIPPED` — Claude nici nu mai e apelat.

Verificările care se pot rezolva cât timp se așteaptă Claude (de ex. vechimea tick-ului) nu resping în această etapă. Verificarea finală de risc se face oricum, din nou, la execuție.

---

## 11. Claude — evaluatorul AI

Fișier: `app/ai_claude.py`. Model implicit: `CLAUDE_MODEL=claude-opus-5-5` (configurabil).

**Ce primește:** simbolul, timeframe-ul, direcția, intrarea, SL, TP, RR, spread-ul, probabilitățile, motivele, indicatorii, plus context: bid/ask, regimul, scorul, ultimele 12 bare M5 și ultimele 6 bare H1.

**Ce răspunde** (JSON forțat printr-o schemă strictă):
- `verdict`: `AGREE` (datele susțin setup-ul), `CAUTION` (riscuri notabile, dar nu motiv de blocare), `VETO` (motiv concret: trend contrar pe TF mare, cumpărare în supracumpărare spre rezistență, volatilitate sau spread anormale, date contradictorii);
- `explanation`: în română, max 600 de caractere;
- `risks`: listă de riscuri scurte;
- `invalidation`: condiția concretă care anulează setup-ul.

**Reguli din prompt:** nu poate schimba direcția sau nivelurile, nu creează ordine, nu inventează știri, nu promite profit.

**Comportament:**
- `CLAUDE_VETO_ENABLED=true` + VETO → semnalul devine NO_TRADE;
- eroare sau timeout (`CLAUDE_TIMEOUT_SECONDS`, 45 s) → se continuă fără Claude (status ERROR), **cu excepția** `CLAUDE_REQUIRED=true`, caz în care → NO_TRADE;
- fără cheie API → status DISABLED;
- erorile sunt traduse clar (401 cheie invalidă, 403, 429, conexiune) și orice secret e mascat în mesaje;
- **costul** fiecărui apel (tokeni input / output / cache) e calculat în USD și salvat în `claude_usage`.

---

## 12. Risk Engine — toate verificările

Fișier: `app/risk.py`. Complet **independent de AI**. Fiecare verificare produce `{nume, ok, detaliu}`; **toate** trebuie să treacă.

| # | Verificare | Ce verifică |
|---|---|---|
| 1 | `mode_allows_execution` | mod PAPER/DEMO și execuție armată (nu SIGNAL_ONLY, nu LIVE) |
| 2 | `entries_not_paused` | kill switch-ul nu e activ |
| 3 | `bridge_connected` | (DEMO) EA-ul e conectat |
| 4 | `account_is_demo` | (DEMO) `trade_mode == 0` |
| 5 | `terminal_trade_allowed` | (DEMO) terminalul și contul permit trading algoritmic |
| 6 | `ea_magic_matches` | (DEMO) EA-ul folosește același magic ca serverul |
| 7 | `account_settled` | (DEMO) contul nu e în curs de actualizare (după schimbări) |
| 8 | `quote_fresh` | tick mai nou de `MAX_TICK_AGE_SECONDS` (10 s) |
| 9 | `quote_not_crossed` | bid < ask (cotație validă) |
| 10 | `spread_ok` | spread ≤ `MAX_SPREAD_POINTS` (30) |
| 11 | `sl_tp_present` | SL și TP există și sunt de partea corectă a prețului |
| 12 | `sl_distance_ok` | SL și TP la distanță ≥ `stops_level` |
| 13 | `volume_ok` | volumul calculat e valid și riscul ≤ limita (vezi §13) |
| 14 | `ml_size_factor` | (dacă există model ML aprobat) factorul aplicat riscului |
| 15 | `max_open_positions` | poziții deschise < `MAX_OPEN_POSITIONS` (1) |
| 16 | `max_trades_per_day` | tranzacții azi < `MAX_TRADES_PER_DAY` (5) |
| 17 | `daily_loss_ok` | pierderea zilei < `MAX_DAILY_LOSS_PCT` (1%) din equity-ul de la începutul zilei |
| 18 | `drawdown_ok` | scăderea față de equity-ul maxim < `MAX_EQUITY_DRAWDOWN_PCT` (3%) |
| 19 | `consecutive_losses_ok` | seria de pierderi < 3 (sau pauza a expirat) |
| 20 | `total_open_risk_ok` | riscul total deschis + noul ordin ≤ `MAX_TOTAL_OPEN_RISK_PCT` (2%) |
| 21 | `news_blackout_ok` | fără știre importantă în fereastră |
| 22 | `free_margin_ok` | marjă liberă ≥ `MIN_FREE_MARGIN_PCT` (50%) din equity |
| 23 | `currency_exposure_ok` | fără conflict de corelație valutară |
| 24 | `no_existing_bot_position_on_symbol` | nicio altă poziție a botului pe simbol |

Plus filtrele de cooldown și rollover din §9. Ziua se calculează în fusul orar local; equity-ul de început de zi și cel maxim sunt salvate periodic (`equity_snapshots`).

---

## 13. Calculul volumului (position sizing)

Tehnica: **risc fix în procente din equity** (fixed fractional).

```
bani_risc_țintă = equity × MAX_RISK_PER_TRADE_PCT / 100            (implicit 0.25%)
risc_pe_lot     = (distanța_SL / tick_size) × tick_value
volum           = rotunjire_în_jos_la_pas( bani_risc_țintă / risc_pe_lot )
```

- volumul e limitat la `volume_max` și `MAX_VOLUME_LOTS` (0.05);
- dacă volumul iese sub lotul minim → respins („risc prea mic pentru lotul minim”) — botul **nu** mărește singur riscul;
- **ordinul de test** din dashboard folosește lotul minim și acceptă un risc de cel mult 2 × limita;
- dacă un model ML aprobat dă un factor > 1, riscul nu poate depăși `ML_MAX_RISK_PCT` (0.5%);
- fără `tick_value` / `tick_size` valide → respins (riscul nu poate fi calculat).

---

## 14. Execuția: intenții, comenzi, reconciliere

Fișier: `app/executor.py`. Fiecare acțiune (OPEN / MODIFY / CLOSE) este o **intenție** salvată în baza de date, cu mașină de stări:

```
OPEN PAPER:  → REJECTED_RISK  sau  PAPER_FILLED
OPEN DEMO:   → REJECTED_RISK  sau  QUEUED → DELIVERED → FILLED / FAILED
                                                    └→ (30 s fără răspuns) UNKNOWN → reconciliere
```

- O comandă e livrată EA-ului **o singură dată** și nu e retrimisă orbește.
- **UNKNOWN** se rezolvă prin reconciliere: botul caută poziția după comentariul `ai:<primele 8 caractere ale intenției>`.
- **Legare de cont:** fiecare intenție DEMO memorează login-ul contului. Dacă s-a schimbat contul în MT5, intențiile vechi devin FAILED (`ACCOUNT_CHANGED`) și nu se livrează pe alt cont.
- Un OPEN care stă în coadă mai mult de 20 s nu mai e livrat (`EXPIRED_BEFORE_DELIVERY`).
- Eșecurile repetate pe aceeași poziție au **backoff exponențial** (30 s → 15 min, max 6 încercări).
- Fiecare tranziție declanșează evenimente pe semnal, mesaj Telegram și actualizare în dashboard.

---

## 15. Simulatorul PAPER și feed-ul SIMULAT

- **`paper.py` — broker simulat:** deschide la ask (BUY) / bid (SELL) live, calculează profitul cu `tick_value`/`tick_size`, închide automat la atingerea SL/TP, simulează marja (levier 100), tichete de la 900 000 001. Starea se salvează în baza de date și supraviețuiește repornirilor. Sold inițial: `PAPER_START_BALANCE` (10 000).
- **`simfeed.py` — feed simulat:** random walk cu segmente de trend, produce aceleași structuri ca EA-ul. Totul e etichetat `SIMULATED`, iar modul DEMO e interzis. Util ca să vezi interfața fără MT5.

---

## 16. Gestionarea pozițiilor deschise

Fișier: `app/position_manager.py`. Rulează în fiecare buclă, doar pe pozițiile cu magicul botului. **Nu lărgește niciodată SL-ul.**

| Acțiune | Regulă |
|---|---|
| **Break-even** | la +`BREAK_EVEN_AT_R` (1R) profit, SL e mutat la intrare + o mică marjă: `max(spread curent + 3 puncte, BE_LOCK_R × risc inițial)` (BE_LOCK_R = 0.1). Se aplică **o singură dată** (fără MODIFY repetat când spread-ul variază). |
| **Break-even la știri (NEWS_BE)** | dacă vine o știre importantă pentru monedele poziției și profitul e ≥ 0.3R → mută SL-ul la break-even mai devreme. |
| **Trailing** | opțional (`TRAILING_ENABLED`, implicit oprit). |
| **Rescue** | după `RESCUE_MINUTES` (30) deschisă, cu MAE ≥ 0.3R și ≥ 60% din minute pe minus, poziția se închide la primul profit **net** ≥ 0.1R (socotind swap-ul și comisionul acumulate). SL-ul nu e mutat. |
| **Time-stop** | închidere după `MAX_HOLD_MINUTES` (240). |
| **Sfârșit de sesiune** | `CLOSE_AT_SESSION_END=true` → închidere la ora de sfârșit. |
| **ML_EXIT** | doar dacă există un model de ieșire **aprobat**: închidere anticipată când profitul rămas prezis < −marjă. |

Detalii tehnice: MODIFY limitat la unul la 10 s pe poziție; time-stop și sfârșitul de sesiune se execută și pe tick vechi (închidere la piață) și nu se abandonează niciodată; un tracker urmărește minut cu minut traiectoria fiecărei poziții (R curent, MFE, MAE) pentru rescue și analiză.

---

## 17. Închiderea tranzacției, contabilitate și performanță

- Deal-urile de ieșire de la EA (sau închiderile PAPER) sunt transformate în tranzacții, **idempotent** (cheie unică pe deal).
- **Motivul închiderii:** SL, TP, BOT (time-stop, sesiune, rescue…), MANUAL, UNKNOWN.
- **Câmpuri:** preț de intrare/ieșire, profit, comision, swap, risc inițial în bani, **multiplu R** (`profit / risc inițial`), legătura cu semnalul.
- **Analytics** (pagina *Performanță*, `perf_stats`): număr de tranzacții, rată de câștig, R mediu (expectanță), interval de încredere 90% pentru R mediu (bootstrap), profit factor, drawdown maxim în R și USD, serie maximă de pierderi, profit net, defalcare pe simbol / oră / zi / motiv de ieșire, curba de equity, costul Claude.

---

## 18. Telegram

Fișier: `app/telegram.py`. Tehnica **outbox**: mesajele se salvează întâi în baza de date, apoi un worker le trimite cu **retry și backoff exponențial** și **idempotență** (fiecare mesaj are o cheie unică, deci nu se trimite de două ori).

Tipuri de mesaje: semnal nou (cu verdictul Claude și explicația), ordin trimis / executat / respins / blocat de risc / stare necunoscută, modificare SL (break-even, știri), tranzacție închisă cu rezultat, alerte de risc (bridge deconectat, limite atinse), început de sesiune. Suportă topicuri de forum (`TELEGRAM_MESSAGE_THREAD_ID`) și un buton „Deschide semnalul” dacă există un URL public HTTPS. Tokenul nu apare niciodată în loguri.

---

## 19. Dashboard-ul web

`web/index.html` + `app.js` + `style.css` — JavaScript simplu, actualizat live prin **SSE** (`/api/v1/stream`). Pagini:

1. **Prezentare** — starea bridge-ului, a contului, a sesiunii, a modului; contoare pentru azi.
2. **Grafic live** — lumânări M1/M5/M15/H1 cu EMA-uri, analiza curentă, nivelurile.
3. **Semnale** — lista filtrabilă și pagina de detaliu cu toată cronologia evenimentelor.
4. **Poziții & Ordine** — pozițiile deschise (ale botului și altele), comenzile în curs, închidere manuală.
5. **Performanță** — statistici, curba de equity, defalcări.
6. **Backtest** — rulezi backtest-uri din interfață și vezi rezultatele.
7. **Modele ML** — registrul de modele, deciziile în umbră, checklist-ul de promovare, aprobare / retragere.
8. **Control & Risc** — mod, armare, pauză, sesiune, limite de risc, strategia activă, ordin de test, „Închide TOATE” (cu dublă confirmare).
9. **Telegram & AI** — stare, test de mesaj, costuri Claude.
10. **Sistem & Loguri** — loguri, arhivă de istoric, calendar, teste forward.

Butoanele de control cer `ADMIN_TOKEN` (introdus din iconița ⚙, păstrat local în browser).

---

## 20. API-ul

Prefix `/api/v1`, JSON. Toate acțiunile (POST/PUT) cer header-ul `X-Admin-Token`; dacă tokenul nu e setat în `.env` → 403, token greșit → 401.

- **Citire:** `status`, `symbols`, `candles`, `analysis`, `signals`, `signals/{id}`, `positions`, `trades`, `intents`, `risk`, `settings`, `strategies`, `logs`, `stream` (SSE), `stats`, `shadow`, `archive/status`, `exits`, `backtest/runs`, `backtest/runs/{id}`, `forward`, `models`, `models/{id}`.
- **Control:** `control/mode`, `control/execution`, `control/pause-entries`, `control/resume-entries`, `control/session`, `control/close-position`, `control/close-all` (cere `{"confirm":"CLOSE ALL"}`), `control/test-order`, `control/analyze-now`, `PUT risk`, `PUT schedule`, `PUT strategy`, `telegram/test`, `backtest/run`, `forward/reset`, `PUT models/settings`, `models/retrain`, `models/{id}/approve` (cere „APROB”), `models/{id}/retire`.
- **Bridge:** `POST /bridge/v1/sync` (doar pentru EA, cu `X-Bridge-Key`).

Detaliile complete ale formatului: [`docs/CONTRACT.md`](CONTRACT.md).

---

## 21. Baza de date

SQLite (`data/bot.db`), mod **WAL**, o singură conexiune protejată de un lock; scrierile mari (arhivă) rulează pe un fir separat. Migrările sunt **aditive** (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ADD COLUMN`) — nimic nu se șterge.

Tabele: `signals`, `signal_events`, `intents`, `trades`, `bars`, `bars_archive`, `spread_stats`, `analyses` (fiecare analiză + eticheta umbră), `equity_marks` (equity de început de zi / maxim), `equity_snapshots`, `claude_usage`, `notifications` (outbox-ul Telegram), `settings`, `system_events` (loguri), `backtest_runs`, `trade_paths`, `forward_meta`, `forward_positions`, `forward_trades`, `calendar_history`, `calendar_history_windows`, `ml_models`, `ml_decisions`, `ml_approvals`, `ml_retrain_runs`.

---

## 22. Arhiva de istoric și calendarul economic istoric

**Arhiva de bare** (`app/archive.py`, EA ≥ 1.11):
- **Faza 1 (bază):** pentru fiecare simbol activ × [M5, H1, M15], descarcă înapoi în timp loturi de până la 5 000 de bare până acoperă `ARCHIVE_DAYS` (120 de zile).
- **Faza 2 (extinsă, pentru cercetare):** `ARCHIVE_EXTRA`, de ex. `H1:1095,H4:1825,D1:3650` — ani de istoric, D1 primul, cel mult o cerere la 60 s, **niciodată** cât botul are poziții deschise (descărcarea poate bloca MT5).
- **Faza 3 (simboluri de cercetare):** aur, argint, indici, petrol, BTC, ETH, alte perechi; botul încearcă automat **aliasuri** de nume (US500 → SPX500, US500.cash, SP500; DE40 → GER40, DAX40 etc.) până găsește numele brokerului.
- O singură cerere în aer, cu timeout și backoff; niciodată în același răspuns cu un ordin; potrivire strictă cerere–răspuns; scrieri `INSERT OR IGNORE` în lot pe fir separat; barele live sunt oglindite în arhivă, deci nu rămân goluri.
- **DST-ul brokerului** e recalculat corect pentru barele vechi (regula EET sau NY, detectată automat).

**Calendarul economic istoric** (`app/calendar_hist.py`, EA ≥ 1.14): descarcă evenimentele din 2015 până azi + 7 zile, cu valorile actual / prognoză / anterior / revizuit, an cu an (fereastra se micșorează adaptiv dacă răspunsul e trunchiat); reîmprospătează ultimele 3 zile la 6 h. Folosit doar pentru cercetare (studii de „surpriză” la știri).

---

## 23. Etichete „umbră” (shadow labels)

`app/labeler.py`: pentru **fiecare** setup analizat — executat, blocat sau cu veto — botul simulează ulterior, bară cu bară, ce s-ar fi întâmplat:
- intrare la deschiderea barei următoare (+ spread pentru BUY);
- ieșirile BUY pe bid, SELL pe ask (+ spread);
- **SL și TP atinse în aceeași bară → SL primul** (conservator);
- break-even și time-stop exact ca în live;
- spread-ul din statisticile reale pe oră, slippage la stop.

Rezultatul (`outcome`, `outcome_r`, MFE, MAE) e salvat în `analyses`. Așa se poate măsura dacă filtrele și VETO-urile Claude ajută sau încurcă (pagina *shadow*).

---

## 24. Backtest walk-forward

`app/backtest.py`. Principiul cheie: **folosește exact codul live** (aceeași funcție `strategy.analyze`, aceleași filtre de context, același `risk.evaluate`), cu un cont și un registru de poziții simulate.

- **Faza 1** (în paralel, pe procese, câte unul per simbol): analiza fiecărei bare cu context neutru → candidați + blocaje de piață.
- **Faza 2** (secvențial, toate simbolurile în ordine cronologică): filtre de context, risc, dimensionare, umpleri, ieșiri. Un test garantează că împărțirea e exactă.
- **Execuție simulată:** umplere la deschiderea barei următoare, spread modelat, **slippage** (implicit 1 punct), **comision** (implicit 7 USD/lot dus-întors), **swap** (tabel per simbol, pentru deținerile lungi), SL primul în bara ambiguă.
- **Fără lookahead:** doar bare închise până la momentul deciziei (ultimele 400 pe TF-ul semnalului, 200 pe HTF).
- **Moduri de cost:** din statisticile reale (`stats`), spread fix, STANDARD, ECN, fără costuri (diagnostic).
- **Metrici:** tranzacții, rată de câștig, R mediu, interval de încredere 90%, profit factor, drawdown maxim în R, serie maximă de pierderi, R net, USD net, defalcări.
- Poate înregistra traiectoria fiecărei tranzacții (JSONL) și păstrează faza 1 în cache, ca să testeze rapid multe politici de ieșire.
- Rulează din dashboard, din API sau din linia de comandă, pe o conexiune SQLite **read-only**.

---

## 25. Strategiile de cercetare (plugin-uri)

Registru: `app/strategies/`. Fiecare plugin are aceeași interfață și același format de ieșire ca `trend-v1`.

**Intraday (pot fi activate și live din dashboard):**
- **`meanrev-v1`** — mean reversion: vinde/cumpără la extremele Bollinger cu RSI extrem, doar când trendul H1 e plat; SL dincolo de bandă, TP la mijlocul benzii.
- **`breakout-v1`** — breakout al range-ului asiatic: range-ul orelor asiatice, intrare la prima închidere dincolo de el, în direcția trendului; SL la mijlocul / capătul opus al range-ului.
- **`pullback-v1`** — cumpără corecțiile într-un trend H1 puternic: atingerea EMA20 + RSI care trece înapoi de 50; SL sub swing low, TP 2R.

**Pe timeframe-uri mari (doar backtest):**
- **`donchian-v1`** — breakout de canal în stil „Turtle” (D1 55 / H4 100 de bare), ieșire pe canalul opus.
- **`ma-trend-v1`** — trend cu EMA200 + EMA50, intrare la reîncrucișarea EMA50, trailing + ieșire la inversarea semnalului.
- **`tsmom-v1`** — time-series momentum: semnul randamentului pe 126 de zile, filtrat după volatilitate.

**Test forward:** **`fade-v1`** — oglinda exactă a lui trend-v1 (direcția opusă, SL și TP inversate), pre-înregistrată, neoptimizată.

**Descoperire (Etapa E):** 20 de ipoteze din literatura de specialitate și din practică, în `strategies/discovery/` — fade la deschiderea Londrei, reversal după fixing-ul de la 16:00, rebalansarea de sfârșit de lună, umplerea gap-ului de luni, NR4/NR7 (Crabel), compresie ATR, zile cu range extins, sweep și reclaim pe maximul/minimul zilei și al săptămânii anterioare, RSI(2) Connors, epuizare după N închideri, momentum și reversal cross-sectional, reziduul față de factorul USD, „week squaring” de vineri, sezonalitate intraday (Breedon & Ranaldo), numere rotunde (Osler), „fakey”, breakout al indicelui USD, fade al spike-urilor de știri.

---

## 26. Politicile de ieșire de cercetare

`app/exits.py` — politici aplicate bară cu bară în simulări, fără lookahead:

| Mod | Regulă |
|---|---|
| `base` | SL/TP + break-even + time-stop (exact ca live) |
| `trailing` | după +N R, SL urmărește cea mai bună închidere ∓ max(k×ATR, m×R) |
| `partial` | închide o fracțiune la +1R, restul la break-even |
| `invalidation` | ieșire când condiția de intrare nu mai e valabilă |
| `stall` | ieșire dacă după X minute prețul stă într-o bandă îngustă |
| `giveback` | ieșire dacă se returnează o fracțiune din profitul maxim |
| `channel` | ieșire la extrema opusă a canalului Donchian |
| `signal_flip` | ieșire când direcția strategiei se inversează |
| `rescue` | închide la profit mic după mult timp pe minus |
| `combo` | combinație de mai multe moduri |

---

## 27. Testele forward (PAPER)

`app/forward.py`: rulează strategii **în timp real**, dar complet separat de trading — fiecare test are propriul registru de poziții și tranzacții, nu creează intenții, nu atinge MT5 și nu influențează deciziile live. Configurare prin `FORWARD_TESTS` (JSON): strategie, TF-uri, simboluri, risc, equity simulat, time-stop și **criterii de evaluare pre-înregistrate** (număr minim de tranzacții, durată maximă, limita inferioară a intervalului de încredere, R mediu, PF).

---

## 28. Stratul de Machine Learning

Principiu: **învățarea e separată de trading**, iar modelele nu acționează fără aprobarea unui om.

### Date (`app/ml/dataset.py`)
Construite dintr-un singur backtest al strategiei de bază:
- **Setul de intrare (fs-v1):** fiecare candidat fără blocaje de piață; eticheta = R **net** după costuri realiste.
- **Setul de ieșire (xs-v1):** un rând pentru fiecare bară cât tranzacția e încă deschisă; ținta = R-ul care mai e de câștigat sau pierdut dacă ții poziția în loc s-o închizi acum.

### Feature-uri (`app/ml/features.py`) — același cod în backtest și live
- **Intrare (fs-v1):** distanța și panta EMA20/EMA50 în unități ATR, RSI14, MACD/ATR, ATR relativ și percentila lui, lățimea Bollinger, panta și distanța pe HTF, ora (sin/cos), ziua săptămânii, spread/ATR, direcția, scorul, SL/ATR, RR, randamentul pe 12 bare, plus variante orientate în direcția tranzacției.
- **Ieșire (xs-v1):** R curent, MFE, MAE, minute în poziție, variația R pe 3 și 6 bare, distanța până la SL/TP în R, raportul ATR, EMA20 și RSI orientate, ora, direcția.

### Antrenare (`app/ml/train.py`)
- Împărțiri **strict cronologice**, walk-forward pe 4 fold-uri: train | calibrare | validare | test.
- **Purge** (elimină eșantioanele a căror etichetă se suprapune cu segmentul următor) și **embargo** (o pauză de timp între segmente) — tehnici anti-scurgere de informație (López de Prado).
- **Model de intrare:** baseline Logistic Regression + **HistGradientBoostingClassifier** calibrat **izotonic** → P(R_net > 0). Pragul e ales pe validare (R mediu maxim, cu acoperire ≥ 25%).
- **Model de ieșire:** **HistGradientBoostingRegressor** → R-ul rămas; închide când predicția < −marjă.
- **Metrici out-of-sample:** Brier, log loss, AUC, bine-uri de calibrare + ECE, plus politica de trading (n, R mediu, CI90, PF față de baseline).
- Simbolul e codificat prin target encoding calculat **doar** pe train.

### Registru și ciclul de viață (`app/ml/registry.py`)
`candidate → shadow → approved → archived`. Artefactele (joblib) sunt verificate cu **sha256** — un fișier modificat e refuzat. Un singur model aprobat per tip; rollback la cel anterior.

### Live (`app/ml_live.py`)
- **shadow:** modelul doar scrie decizii în `ml_decisions`; nu schimbă nimic.
- **approved (intrare):** poate respinge intrări sub prag și poate scala riscul în [`ML_SIZE_MIN`, `ML_SIZE_MAX`] (0.5–1.0, maxim 2.0), liniar între prag și percentila 90 a validării. Toate limitele Risk Engine rămân.
- **approved (ieșire):** poate doar închide mai devreme (`ML_EXIT`); nu modifică SL/TP și nu adaugă.
- **Checklist de promovare:** R mediu OOS > 0, limita inferioară CI90 > −0.02, bate baseline-ul cu > 0.05R, ECE < 0.05, calibrare la ieșire < 0.05R, ≥ 150 de tranzacții OOS, ≥ 14 zile sau ≥ 100 de decizii în umbră cu semn consecvent. Aprobarea cere tokenul admin și cuvântul „APROB”; un override cere și „ȘTIU RISCUL”, iar un model aprobat cu override poate doar respinge intrări, nu poate mări riscul. Totul e auditat în `ml_approvals`.
- **Reantrenare nocturnă** la `ML_RETRAIN_AT` (22:30): subproces cu prioritate redusă (`nice`), pe o **copie** a bazei, cu timeout; produce doar candidați și mută cel mai nou în shadow — **nu aprobă niciodată**.
- Dacă numpy sau scikit-learn lipsesc, toate hook-urile devin no-op (botul merge exact ca fără ML).

CLI: `python -m app.ml train | list | approve | retire | shadow`.

---

## 29. Metodologia de cercetare și rezultatele

**Metodologie (aplicată în toate etapele):**
- **protocol pre-înregistrat:** regula de decizie e scrisă **înainte** de a vedea rezultatele de test;
- împărțire TRAIN / VALID / TEST (sau HOLDOUT), cu setul de test evaluat **o singură dată**;
- costuri realiste (spread, comision, slippage, swap) și diagnostic fără costuri;
- interval de încredere 90% prin bootstrap, profit factor, consistență pe ani / sub-perioade;
- atenție explicită la **testarea multiplă** (multe ipoteze → risc de rezultate false pozitive).

**Rezultate:**

| Etapă | Ce s-a testat | Rezultat |
|---|---|---|
| C | trend-v1, meanrev, breakout, pullback pe M15 + 10 politici de ieșire | 4/4 **REJECTED** (cel mai bun: breakout ≈ 0R) |
| C2 | donchian, ma-trend, tsmom pe H1/H4/D1, multi-an | 9/9 **REJECTED**, toate negative și fără swap, și cu costuri ECN |
| D | ML: filtru de intrare, model de ieșire, combinat | 4/4 **REJECTED**: pierderea scade de la −0.103R la −0.050R, dar rămâne negativă |
| E | 20 de ipoteze noi | **0/20** trec validarea |

**Concluzie:** pe date de preț simple, pe acest univers de perechi FX, nu există avantaj după costuri. Recomandare: **rămâne pe DEMO**. Direcții care ar putea schimba concluzia: costuri mai mici (cont ECN/raw), mai mult istoric intraday, surse de informație dincolo de preț (calendar macro, poziționare, carry). Detalii: `docs/research/STAGE_*.md`.

---

## 30. Securitate și reguli de siguranță

- **LIVE blocat** în cod; DEMO doar pe cont verificat ca demo (`trade_mode == 0`).
- **SL și TP obligatorii** pe fiecare ordin (`MUST_HAVE_SL_TP`).
- Botul atinge **doar** pozițiile cu magicul lui (`552211`); pozițiile manuale sunt afișate, dar nu modificate.
- **Kill switch** (pauză intrări) separat de **close-all** (care cere dublă confirmare).
- AI-ul și ML-ul nu pot crea ordine; pot doar reduce riscul (cu excepția unui model aprobat manual, plafonat).
- Comenzile sunt livrate o singură dată (idempotență la server și la EA), legate de cont, cu expirare și protecție la derivă de preț.
- Secretele (cheia API, tokenurile, cheia bridge-ului) nu sunt returnate niciodată prin API și sunt mascate în loguri și erori; login-ul contului e afișat mascat (`****1234`).
- Acțiunile de control cer `ADMIN_TOKEN`; bridge-ul cere `MT5_BRIDGE_KEY`; cererile respinse sunt logate agregat.
- Serverul ascultă implicit doar pe `127.0.0.1`.
- Detectează mai multe instanțe EA pornite simultan.

---

## 31. Configurare (`.env`)

Toate variabilele sunt documentate în [`.env.example`](../.env.example). Grupuri:
- **Aplicație:** `HOST`, `PORT`, `ADMIN_TOKEN`, `APP_ENV`.
- **Date:** `DATA_SOURCE`, `MT5_BRIDGE_KEY`, `MT5_MAGIC`, `DATABASE_PATH`, `ARCHIVE_*`, `RESEARCH_SYMBOLS`, `MT5_SERVER_TZ`, `CALHIST_*`.
- **Teste forward:** `FORWARD_TESTS`.
- **ML:** `ML_ENABLED`, `ML_SIZE_MIN/MAX`, `ML_MAX_RISK_PCT`, `ML_RETRAIN_*`, `ML_MODELS_DIR`.
- **Sesiune:** `DEFAULT_TIMEZONE`, `SESSION_*`.
- **Instrumente:** `ACTIVE_SYMBOLS`, `SIGNAL_TIMEFRAME`, `HTF_TIMEFRAME`, `CHART_TIMEFRAMES`.
- **Mod:** `TRADING_MODE`, `EXECUTION_ENABLED`, `LIVE_TRADING_ALLOWED`, `MUST_HAVE_SL_TP`.
- **Risc:** `MAX_RISK_PER_TRADE_PCT`, `MAX_DAILY_LOSS_PCT`, `MAX_EQUITY_DRAWDOWN_PCT`, `MAX_OPEN_POSITIONS`, `MAX_TRADES_PER_DAY`, `MAX_CONSECUTIVE_LOSSES`, `MAX_SPREAD_POINTS`, `MAX_SLIPPAGE_POINTS`, `MAX_VOLUME_LOTS`, `MAX_SAME_CURRENCY_DIRECTION`, `MIN_FREE_MARGIN_PCT`, `MAX_TICK_AGE_SECONDS`, `MAX_SIGNALS_PER_SYMBOL_PER_SESSION`, `NEWS_*`, `MAX_TOTAL_OPEN_RISK_PCT`, `LOSS_STREAK_PAUSE_MINUTES`, `COOLDOWN_AFTER_LOSS_MINUTES`, `ROLLOVER_BLACKOUT_MINUTES`.
- **Strategie și ieșiri:** `ATR_SL_MULT`, `ATR_TP_MULT`, `MIN_SL_POINTS`, `MIN_SCORE_COMPONENTS`, `SIGNAL_EXPIRY_MINUTES`, `BREAK_EVEN_AT_R`, `BE_LOCK_R`, `TRAILING_ENABLED`, `MAX_HOLD_MINUTES`, `CLOSE_AT_SESSION_END`, `RESCUE_*`, `PAPER_START_BALANCE`.
- **Claude:** `ANTHROPIC_API_KEY`, `CLAUDE_ENABLED`, `CLAUDE_MODEL`, `CLAUDE_VETO_ENABLED`, `CLAUDE_REQUIRED`, `CLAUDE_TIMEOUT_SECONDS`.
- **Telegram:** `TELEGRAM_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_MESSAGE_THREAD_ID`, `PUBLIC_APP_URL`.

Multe limite de risc, programul și strategia activă se pot schimba și din dashboard (se salvează în tabelul `settings`).

**Pornire:** `./run.sh` (cere `.venv` și `.env`). Instalarea EA-ului: [`mt5/README_EA.md`](../mt5/README_EA.md).

---

## 32. Teste automate

**608 teste** pytest în `tests/` (toate trec, ~60 s): indicatori, strategie, risc, scheduler, bridge și protocol, motor, end-to-end cu un **EA fals** (`tests/fake_ea.py`), integrare, PAPER, poziții, Telegram + AI, arhivă, backtest, performanță, strategii, ieșiri, rescue, forward, ML (pipeline, live, end-to-end), calendar istoric, simboluri de cercetare, plus teste de regresie pentru fiecare runda de review.

```bash
.venv/bin/python -m pytest -q
```

---

## 33. Istoricul dezvoltării (etape)

| Commit | Etapă |
|---|---|
| `b6045fd` | **v0.1 + Etapa A:** botul de bază (EA, server, trend-v1, Claude, Risk Engine, PAPER/DEMO, Telegram, dashboard) + reparații de siguranță |
| `9ae1448` | **Etapa B:** arhivă de istoric, backtest, etichete umbră, analytics de performanță |
| `cd4ddfe` | **Etapa C:** plugin-uri de strategii, politici de ieșire, traiectorii de tranzacții, cercetare walk-forward |
| `e77be64`, `91683e5`, `ac7e651` | **Etapa C2:** arhivă multi-an H1/H4/D1, strategii HTF, costuri swap, rezultate |
| `de995e1` | Runner pentru teste forward PAPER + strategia fade-v1 |
| `981f7ff` | **Etapa E:** 20 de ipoteze noi; niciuna nu trece |
| `e72d0db` | Ieșirea „rescue” |
| `a468248` | **Etapa D:** modele ML de intrare/ieșire, registru, mod umbră, reantrenare nocturnă, pagina Modele |
| `6be54a0` | **Pachet de optimizări:** rescue live, break-even inteligent, cooldown după pierdere, arbitraj pre-AI, blocare la rollover |
| `b313c25` | EA v1.14: aliasuri pentru simbolurile de cercetare + istoric calendar economic (CALHIST) |
| `3d2e703` | Rescue socotește swap-ul și comisionul (profit = bani netți) |
| `f76e7b3` | Break-even aplicat o singură dată |
| `7d2526f` | Bridge-ul continuă să coteze simbolurile cu poziții deschise chiar dacă au ieșit din lista activă |

---

## 34. Limitări și probleme cunoscute

- **Nu există avantaj statistic demonstrat** — botul este o infrastructură solidă și sigură, nu o mașină de profit.
- Probabilitățile strategiei sunt **euristice, necalibrate**.
- **Port inconsistent:** `.env.example` are `PORT=8765` și `PUBLIC_APP_URL=…:8765`, dar codul, README-ul și EA-ul folosesc **8787**. Dacă se copiază `.env.example` ca atare, EA-ul nu se conectează (trebuie pus `PORT=8787` sau schimbat `InpServerUrl` în EA).
- **Dependențe ML lipsă:** `requirements.txt` nu conține `numpy` și `scikit-learn`; fără ele botul merge, dar ML-ul și reantrenarea sunt dezactivate.
- Un singur proces, SQLite, dashboard local fără HTTPS — potrivit pentru demo pe calculatorul propriu, nu pentru producție expusă pe internet.
- În jurul schimbării orei la broker, barele H4/D1 pot fi decalate cu ±1 h de două ori pe an (acceptat).

---

## 35. Glosar

- **R** — unitatea de risc: 1R = pierderea dacă se atinge SL-ul inițial. +2R = câștig de două ori riscul.
- **MFE / MAE** — Maximum Favorable / Adverse Excursion: cel mai bun / cel mai rău punct atins de o poziție deschisă.
- **PF (profit factor)** — câștigurile brute / pierderile brute.
- **CI90** — intervalul de încredere 90% pentru R-ul mediu.
- **Walk-forward** — antrenare / optimizare pe trecut, testare pe perioada imediat următoare, glisând în timp.
- **Lookahead** — eroarea de a folosi date din viitor într-o decizie din trecut; evitată peste tot.
- **Purge / embargo** — eliminarea eșantioanelor care se suprapun în timp între seturile de antrenare și test.
- **ECE** — Expected Calibration Error: cât de departe sunt probabilitățile prezise de frecvențele reale.
- **HTF** — Higher TimeFrame (timeframe-ul superior, de confirmare).
- **Magic number** — identificatorul pozițiilor deschise de bot în MT5.
- **Shadow / umbră** — rulare care doar înregistrează decizii, fără să tranzacționeze.
- **Break-even** — mutarea SL-ului la prețul de intrare (pierdere zero).
- **Rollover** — momentul zilnic (17:00 New York) când se aplică swap-ul.
