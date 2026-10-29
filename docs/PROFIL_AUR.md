# Profil de configurare: aur (XAUUSD) în bot

Cum pornești botul pe aur, pe contul DEMO, cu mai multe poziții permise pe același instrument — și ce înseamnă fiecare setare. Valorile de mai jos se pun în `.env` (apoi repornești serverul) sau, cele de risc, din pagina *Control & Risc*.

## 0. Cel mai simplu: o singură linie în `.env`

```
PROFILE=gold
```

Apoi repornești serverul (`./run.sh`). Profilul **gold** setează automat tot ce urmează mai jos: doar XAUUSD (grafic, analiză, tranzacții), 3 poziții pe aur la niveluri diferite, istoric extins din MT5, AI trader în umbră, spread/SL/lot potrivite pentru aur și **cadrul de strategie măsurat pe 9 ani de aur** (vezi §4): `trend-v1` pe **H1 cu confirmare H4**, SL 3×ATR, TP 4,5×ATR, time-stop 24 h, fără „rescue”, sesiune 09:00–19:00 Europe/Chisinau (≈ 07–17 UTC, Londra + New York), spread maxim 150 puncte. Profilul **fixează** aceste chei (simboluri, cadru, sesiune, plafoane de risc) peste valorile salvate anterior din dashboard — dacă le schimbi din *Control & Risc* rămân până la repornire, apoi revin la profil; celelalte setări salvate din dashboard sunt păstrate. Totul rămâne pe DEMO cu execuția dezarmată până o armezi tu din *Control & Risc*.

## 0b. Aceeași configurație, scrisă manual (dacă nu vrei profilul)

```
ACTIVE_SYMBOLS=EURUSD,GBPUSD,USDJPY,XAUUSD   # toate sunt cotate și analizate (context, etichete în umbră)
TRADE_SYMBOLS=XAUUSD                         # DOAR aurul primește semnale și ordine
# istoric extins descărcat din MT5 pentru aur (ani de bare), folosit de contextul de termen lung al AI-ului
ARCHIVE_EXTRA=M15:730,H1:1825,H4:3650,D1:7300
ARCHIVE_EXTRA_INTERVAL_S=60
# mai multe poziții pe aur, dar diferite: aceeași direcție, peste poziții pe profit, la ≥ 1 ATR distanță
MAX_OPEN_POSITIONS=3
MAX_POSITIONS_PER_SYMBOL=3
PYRAMID_MIN_R=0.5
PYRAMID_MIN_DISTANCE_ATR=1.0
# AI trader: pornește în UMBRĂ (vezi docs/AI_TRADER.md); live doar după ≥ 100 de decizii etichetate pozitive
AI_TRADER_ENTRY_MODE=shadow
AI_TRADER_MANAGE_MODE=shadow
AI_TRADER_TIMEFRAME=M15
```

`TRADE_SYMBOLS` și `trade_symbols` se pot seta și din API (`PUT /api/v1/strategy`), fără repornire. Istoricul extins se descarcă treptat (o cerere de până la 3 000 de bare la 60 s; cu profilul gold continuă și cât ai poziții deschise — SL/TP sunt la broker): D1 pe 20 de ani în ~2 minute, H4 pe 10 ani în ~10 minute, H1 pe 5 ani în ~15 minute, M15 pe 2 ani în ~20 de minute, în această ordine. Progresul e în *Sistem & Loguri → Arhivă* (`GET /api/v1/archive/status`). Cele „300 bare” din loguri sunt doar prima livrare a EA-ului (`InpHistoryBars`), ca analiza să pornească imediat; AI-ul citește contextul de termen lung din arhivă. Contextul de termen lung al AI-ului devine complet după ce arhiva D1 acoperă câțiva ani.

## 1. Simbolul

```
ACTIVE_SYMBOLS=EURUSD,XAUUSD        # aurul alături de perechile existente
# sau, doar aur:
ACTIVE_SYMBOLS=XAUUSD
```

Numele trebuie să fie **exact** cel din MT5 al brokerului tău (`XAUUSD`, uneori `XAUUSD.a`, `GOLD`, `XAUUSDm`). Îl vezi în fereastra de ordin: „XAUUSD – Gold vs US Dollar”.

## 2. Setări care TREBUIE ajustate pentru aur

| Setare | Valoare pentru aur | De ce |
|---|---|---|
| `MAX_SPREAD_POINTS` | `150` (profilul gold) | aurul are `point` = 0,01 USD; un spread normal e 20–40 de puncte (0,20–0,40 USD), pe MetaQuotes-Demo ~120. Cu stopul 3×ATR pe H1 (25–45 USD) 150 de puncte sunt 3–6 % din R; pe un stop 1,5×ATR pe M5 același spread era 30–40 % din R — motivul pentru care cadrul M5 pierde. Dacă brokerul tău are spread mic, coboară valoarea din *Control & Risc*. |
| `MIN_SL_POINTS` | `150` | 60 de puncte = 0,60 USD, prea puțin pentru aur; SL-ul e oricum 3×ATR pe H1, deci practic nu contează. |
| `MAX_VOLUME_LOTS` | `0.05` | 1 lot de aur = 100 uncii ≈ 200 000+ USD noțional; un lot minim 0,01 cu SL de 5 USD riscă 5 USD. |
| `NEWS_BLACKOUT_MINUTES` | `45` | aurul reacționează violent la CPI, NFP, FOMC; fereastra mai largă e justificată. |

Verifică și în MT5 (*Specificații* simbol): `Stops level`, `Volume min`, `Swap long/short` — swap-ul pe long la aur e de regulă mare și negativ; nu ține poziții peste noapte decât dacă strategia o cere explicit.

## 3. Mai multe poziții pe același instrument (piramidare)

```
MAX_OPEN_POSITIONS=3          # totalul pe toate simbolurile
MAX_POSITIONS_PER_SYMBOL=2    # câte pe XAUUSD deodată (implicit 1)
PYRAMID_MIN_R=0.5             # a doua se adaugă doar când prima e deja pe +0,5R
MAX_TOTAL_OPEN_RISK_PCT=2.0   # plafonul riscului deschis total rămâne
```

Regula implementată (`app/strategy.py: pyramid_blocker`, verificată și de Risk Engine):
- a doua poziție trebuie să fie la **cel puțin `PYRAMID_MIN_DISTANCE_ATR` × ATR(14)** distanță de fiecare intrare deschisă pe simbol — nu 3–4 tranzacții în același loc, ci la niveluri diferite;
- a doua poziție se deschide **doar în aceeași direcție** cu cea existentă (fără hedging pe același simbol — plătești spread-ul de două ori pentru nimic);
- **doar dacă toate pozițiile deschise pe simbol sunt pe profit** cu cel puțin `PYRAMID_MIN_R` din riscul lor inițial — adică se adaugă peste câștigători, nu se mediază pierderi (asta fac boții de tip grid/martingale, care pierd contul);
- un ordin încă în curs pe simbol blochează adăugarea;
- toate celelalte verificări rămân (risc total deschis, număr maxim de poziții, limite zilnice, spread, știri).

Ce **nu** face regula: nu creează avantaj. A doua poziție pe aur e același pariu ca prima (corelație 1). Dacă strategia are expectanță negativă, pierzi de două ori mai repede; dacă are expectanță pozitivă, câștigi de două ori mai repede cu drawdown de două ori mai mare. De aceea valoarea implicită rămâne 1 și de aceea o pornești doar pe DEMO.

## 3b. Urmărirea fiecărei tranzacții: trailing cu blocare de profit

```
PROFIT_LOCK_ENABLED=true
PROFIT_LOCK_ACTIVATE_R=0.67   # se armează la +0,67 R (cu risc 1,50 USD = +1,00 USD)
PROFIT_LOCK_GAP_R=0.33        # SL la 0,33 R în urma prețului (0,50 USD)
PROFIT_LOCK_STEP_R=0.1        # se mută doar în pași ≥ 0,1 R (fără MODIFY la fiecare tick)
```

Exact regula cerută: poziție cu SL inițial la −1,50 USD; prețul coboară puțin, apoi urcă la +1,00 → SL mutat la **+0,50**; prețul mai urcă 0,30 → SL la **+0,80**; dacă prețul cade brusc, poziția se închide la +0,80. SL-ul **nu coboară niciodată**. O face gestionarul de poziții, determinist, **la fiecare secundă**, pentru toate pozițiile botului, pe orice strategie — nu depinde de un apel AI (AI trader-ul poate, în plus, să strângă SL-ul când vede un motiv pe grafic). Aceeași regulă există în backtest (`--exit profit_lock`), ca să-i măsurăm efectul; rezultatele sunt în `docs/research/STAGE_G_GOLD.md` §Profit lock.

## 4. Ce strategie rulează pe aur — și de ce profilul e pe H1/H4

Strategia activă (`PUT /api/v1/strategy` sau pagina *Control & Risc*) se aplică tuturor simbolurilor active. Studiul pre-înregistrat `research/results/GOLDCFG/SYNTHESIS.md` (38 de configurații pe XAUUSD 2013–2018, o singură confirmare pe 2019–2020) a arătat:

| Cadru `trend-v1` pe aur | n | R mediu | IC90 | Tranz./săpt. |
|---|---|---|---|---|
| M15/H1, SL 1,5×ATR, TP 2,5×ATR, hold 240 min (fostul profil; M5 e și mai rău) — TRAIN | 6 623 | **−0,18** | complet negativ | 23 |
| același, pe VALID 2019–2020 | 2 289 | **−0,19** | [−0,22; −0,16] | 22 |
| **H1/H4, SL 3×ATR, TP 4,5×ATR, hold 1440, fără rescue, 07–17 UTC** (profilul actual) — TRAIN, spread standard | 1 429 | **−0,0025** | [−0,043; +0,039] | 4,9 |
| același, cost ECN/raw | 1 429 | +0,019 | [−0,020; +0,059] | 4,9 |

Cu cost zero, `trend-v1` pe M15 dă +0,002 R: intrarea nu are avantaj, iar **toată pierderea e spread-ul** raportat la un stop mic. Pe H1 cu stop 3×ATR spread-ul devine 3–6 % din R, de aceea expectanța urcă de la −0,18 R la ≈ 0. Ce **nu** ajută (măsurat): scor minim 5 (−0,06 R), hold 240 min pe H1 (−0,03 R, 81 % ieșiri pe timp), fereastra 12–17 UTC singură (mai slabă decât 07–17), orice ieșire suplimentară pe H1 — trailing (−0,025 R), profit-lock (−0,05…−0,06 R), partial (−0,05 R), stall/combo (întorc semnul). Nici backtest-urile proprietarului pe 90 de zile (M5: trend-v1 −0,10 R pe 1 137 tranzacții, pullback −0,11 R, meanrev +0,06 R pe 66, nesemnificativ) nu contrazic asta.

Alternativa „cea mai puțin rea” din studiu: `breakout-v1` H1/H4 (spargerea range-ului asiatic 00–06 UTC, SL la capătul opus, TP 2,5×range, hold 24 h, fără rescue) — pe VALID +0,009 R (standard) / +0,031 R (ECN), n 187, dar câștigul vine din 2020 („long aur în raliu”), 2019 e negativ. Se poate alege din *Control & Risc*; parametrii pluginului: `sl_mode "opposite"`, `tp_range_mult 2.5`, `sl_max_atr 3.0`, `range_max_atr 0`.

**Concluzia onestă:** nicio configurație testată nu are IC90 peste zero după costuri. Profilul actual oprește sângerarea (≈ 0 R la ~5 tranzacții pe săptămână) în loc să piardă 0,10–0,19 R la 20+ tranzacții pe săptămână; nu promite profit. Pe un cont cu spread la aur de 1,2 USD (120 puncte, tipic MetaQuotes-Demo), costul e de 3× modelul „standard” al cercetării — un broker cu spread 15–40 de puncte contează mai mult decât orice parametru.

## 5. Pornire sigură

1. `TRADING_MODE=DEMO`, `EXECUTION_ENABLED=false` → pornești serverul, verifici în dashboard că XAUUSD apare cu cotație OK și spread sub limită.
2. Lași câteva zile **doar semnale** (fără armare): vezi câte semnale dă pe aur, verdictele Claude și ce ar fi făcut (etichete umbră, pagina *Performanță → umbră*).
3. Abia apoi *Execuție: armată*, cu `MAX_POSITIONS_PER_SYMBOL=1`; piramidarea o pornești doar după ce vezi că strategia are rezultate pozitive pe DEMO pe ≥ 100 de tranzacții.
