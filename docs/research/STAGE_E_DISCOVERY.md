# Stage E — Descoperire de strategii noi (20 de ipoteze) — rezultate validare

Data: 2026-10-28. Doar cercetare: nimic din ce e aici nu a fost activat pe botul live.

## 1. Protocol (pre-înregistrat înainte de orice rulare)

- **Date:** copie read-only a `data/bot.db` (`bars_archive`, 11 perechi FX). D1 are 10-13 ani, H4 ~5.5 ani, H1 ~3 ani (în practică H1 începe în 2023-09/10), M15/M5 ~4 luni.
- **Costuri (cont standard, mereu):** spread fix per simbol (EURUSD 10, GBPUSD 14, USDJPY 12, AUDUSD 12, USDCAD 16, EURJPY 18, GBPJPY 28, NZDUSD 16, EURGBP 14, EURCHF 18, AUDJPY 18 puncte), slippage 1 punct și tabelul de swap.
- **Măsurare:** `size_mode r_only` (rezultat în R), limită de corelație 1 (max. 1 poziție per monedă și direcție), max. 6 poziții deschise, fără filtru de sesiune (doar dacă ipoteza definește unul).
- **Împărțiri cronologice (embargo de 5 bare între segmente):**
  - D1: TRAIN până la 2019-12-31, VALID 2020-2022, HOLDOUT din 2023.
  - H4: TRAIN până la 2023-06-30, VALID 2023-07-01..2024-12-31, HOLDOUT din 2025.
  - H1: TRAIN până la 2024-12-31, VALID 2025-01-01..2025-08-31, HOLDOUT din 2025-09-01.
- **Screen pe TRAIN:** avg R > +0.05 după costuri, cu cel puțin 150 de tranzacții (D1: cel puțin 80). Maximum 6 combinații de parametri per ipoteză, toate raportate.
- **Poarta VALID** (rulată o singură dată, cu parametrii ficși de pe TRAIN, fără re-optimizare): avg R > 0, limita de jos a CI90 > -0.05, PF > 1.1 și cel puțin 100 de tranzacții (D1: cel puțin 50).
- **Poarta finală HOLDOUT** (rulată o singură dată): avg R > 0, limita de jos a CI90 > 0 (strict, ca protecție pentru testarea multiplă), PF > 1.15 și rezultat pozitiv în fiecare an calendaristic cu cel puțin 20 de tranzacții.

## 2. Cele 20 de ipoteze

| ID | TF | Idee (pe scurt) |
|---|---|---|
| H01 | H1 | Fade la deschiderea Londrei după o mișcare asiatică mare (Asia > k·ATRd) |
| H02 | H1 | Reversal după fixing-ul WM/Reuters de la 16:00 Londra |
| H03 | D1 | Fade al rebalansării USD de sfârșit de lună (z al USDX month-to-date) |
| H04 | H1 | Umplerea gap-ului de luni |
| H05 | H1 (setup D1) | Breakout NR4/NR7 inside-day (Crabel), cu ieșire la sfârșitul zilei următoare |
| H06 | H4 | Compresie ATR urmată de bară de expansiune (continuare) |
| H07 | D1 | Continuare după o zi cu range extins (range ≥ m·ATR20, închidere la extrem) |
| H08 | H1 | Sweep și reclaim al maximului/minimului zilei anterioare (PDH/PDL) |
| H09 | H4 | Sweep și reclaim al maximului/minimului săptămânii anterioare |
| H10 | D1 | Mean reversion RSI(2) (Connors), cu filtru SMA200 |
| H11 | H4 | Fade al epuizării după N închideri consecutive în aceeași direcție |
| H12 | D1 | Momentum săptămânal cross-sectional pe puterea monedelor |
| H13 | D1 | Reversal cross-sectional pe termen scurt între perechi |
| H14 | H4 | Reversia reziduului față de factorul USD |
| H15 | H1 | „Week squaring” de vineri după-amiaza |
| H16 | H1 | Sezonalitate intraday a monedei „de acasă” (Breedon & Ranaldo) |
| H17 | H1 | Respingere la prima atingere a numerelor rotunde (Osler) |
| H18 | H4 | Fals breakout de inside bar („fakey”) |
| H19 | H4 | Breakout al indicelui USD confirmat de o pereche USD |
| H20 | H1 | Fade al spike-urilor de știri |

## 3. Tabel TRAIN (cea mai bună combinație per ipoteză, costuri standard)

| ID | TF | Tranz. | avg R | PF | Screen |
|---|---|---|---|---|---|
| H01 | H1 | 263 | -0.129 | 0.74 | picat |
| H02 | H1 | 208 | +0.048 | 1.25 | picat (sub +0.05) |
| H03 | D1 | 59 | -0.007 | 0.97 | picat |
| H04 | H1 | 91 | +0.041 | 1.27 | picat (prea puține tranzacții) |
| **H05** | **H1** | **152** | **+0.053** | **1.16** | **TRECUT** (la limită) |
| H06 | H4 | 106 | -0.058 | 0.88 | picat |
| H07 | D1 | 85 | +0.003 | 1.01 | picat |
| H08 | H1 | 881 | -0.019 | 0.94 | picat |
| H09 | H4 | 421 | -0.033 | 0.92 | picat |
| H10 | D1 | 186 | +0.048 | 1.29 | picat (sub +0.05) |
| H11 | H4 | 383 | +0.012 | 1.07 | picat |
| H12 | D1 | 257 | -0.003 | 0.99 | picat |
| H13 | D1 | 94 | -0.008 | 0.96 | picat |
| H14 | H4 | 421 | -0.022 | 0.93 | picat |
| H15 | H1 | 47 | -0.006 | 0.97 | picat |
| H16 | H1 | 225 | +0.009 | 1.06 | picat |
| H17 | H1 | 449 | -0.046 | 0.91 | picat |
| H18 | H4 | 1026 | -0.111 | 0.84 | picat |
| H19 | H4 | 79 | +0.084 | 1.18 | picat (prea puține tranzacții) |
| H20 | H1 | 156 | -0.158 | 0.60 | picat |

Toate combinațiile testate sunt în `screen_*_results.json` din directorul `scratchpad/discovery/`.

**H05 pe TRAIN** (2023-10-11..2024-12-31): nr_n=4 / tp_r=1.5 a dat 152 de tranzacții, +0.053R, CI90 [-0.067, +0.170] și PF 1.155. Edge-ul vine aproape în întregime din 2023 Q4 (26 de tranzacții, +0.233R); 2024 are doar +0.016R pe 126 de tranzacții. Toate cele 4 combinații au fost pozitive.

## 4. Tabel VALID (o singură rulare, parametri ficși nr_n=4, tp_r=1.5)

Fereastra: 2025-01-01 05:00 UTC (după embargo) → 2025-09-01 00:00 UTC.

| ID | Tranz. | avg R | CI90 | PF | Win | Max DD (R) | avg R > 0 | CI lo > -0.05 | PF > 1.1 | n ≥ 100 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| H05 | 99 | **-0.059** | [-0.202, +0.085] | 0.85 | 46% | 13.5 | NU | NU | NU | NU (99) | **RESPINS** |

Ieșiri: 58 pe timp (TIMEX), 29 SL, 11 TP și 1 TIMEX_OPEN. Pe simboluri rezultatul este amestecat: EURGBP și EURUSD sunt pozitive, 9 din 11 perechi sunt negative.

## 5. Tabel HOLDOUT

**Nu s-a rulat.** Nicio ipoteză nu a trecut de VALID, iar protocolul interzice să te uiți la HOLDOUT în acest caz, pentru că datele HOLDOUT trebuie să rămână neatinse pentru candidații viitori. Din același motiv nu există nici simularea pe 200 USD cu loturi minime la 0.5% / 1% risc.

## 6. Verdicte finale

| ID | TRAIN | VALID | HOLDOUT | FINAL |
|---|---|---|---|---|
| H05 | trecut (la limită) | picat | nerulat | **RESPINS** |
| celelalte 19 | picat | — | — | **RESPINS** |

**FINAL PASS: niciuna (0 din 20).**

## 7. Concluzie onestă

- **Nicio strategie nou testată nu are un edge demonstrabil după costurile reale ale unui cont standard.** Singurul candidat care a trecut screen-ul (H05) a trecut la limită, cu CI90 care include zero. Pe date noi (VALID 2025) a devenit negativ: -0.059R, PF 0.85. Acesta e tiparul tipic al unui rezultat apărut din noroc sau din optimizare pe un eșantion scurt.
- Etapele C și C2 au ajuns la aceeași concluzie (trend, mean reversion, breakout, pullback, Donchian, MA trend, TSMOM: toate respinse, ~0R chiar fără costuri). Acum avem în total peste 27 de familii de idei, toate în jur de 0R sau negative.
- **Ce înseamnă asta practic:**
  1. Pe aceste 11 perechi, pe timeframe-urile H1-D1 și cu spread-urile contului standard, **nu există în datele noastre o regulă simplă, bazată doar pe preț, care să producă profit sistematic**. Costul (spread, swap) consumă orice micro-edge brut.
  2. **Nu există o strategie care să „închidă mereu pe plus”.** Orice sistem care pare să facă asta (de exemplu martingale, grid, stop-uri foarte largi cu TP-uri mici) doar ascunde riscul în cozi și, mai devreme sau mai târziu, distruge contul. Nu recomandăm așa ceva.
  3. **Recomandare pentru botul live:** nu porniți nicio strategie din această etapă. Pe un cont de 200 USD cu loturi minime, cel mai rațional e să reduceți riscul sau frecvența, sau să lăsați botul doar pe demo, până când un candidat trece întregul protocol, inclusiv HOLDOUT. Pierderile dintr-o zi proastă nu se repară forțând mai multe tranzacții.
  4. Direcții care ar putea schimba concluzia: costuri mai mici (cont ECN/raw cu comision), date intraday mai lungi (H1/M15 cu mai mulți ani) pentru eșantioane suficiente și surse de informație dincolo de preț (calendar macro, poziționare, carry). Pe date de preț simple, pe acest univers, rezultatele sunt constant nule.

## 8. Notă despre testarea multiplă

Au fost testate 20 de ipoteze × până la 6 combinații, adică aproximativ 80 de configurații. La pragul screen-ului (avg R > +0.05, fără cerință de semnificație), cam 5-10% din configurații ar trece doar din întâmplare chiar dacă niciuna nu are edge real. Faptul că a trecut exact 1 configurație din aproximativ 80, la limită, este **perfect compatibil cu ipoteza „niciun edge”**. De aceea poarta finală cere CI90 strict peste 0 pe HOLDOUT. Eșecul H05 pe VALID confirmă această interpretare.

## Fișiere

- Plugin-uri: `app/strategies/discovery/` (disc_h01 … disc_h20, plus helper-ele _disc0, _disc3, _b2)
- Screen-uri: `screen_0.py`, `screen_1.py`, `screen_2.py`, `screen_3.py` și rezultatele lor `screen_*_results.json`
- Validare: `validate.py`; rezultate brute în `discovery_results.json`

Toate fișierele de screen și validare sunt în `/private/tmp/claude-501/-Users-tudorsicorschi-Documents-tradingBot/02b4cca0-75ff-4ba1-970b-84a88a9fd698/scratchpad/discovery/`.
