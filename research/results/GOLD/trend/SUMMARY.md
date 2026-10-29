# GOLD / agent „trend” — trend și breakout pe H4 și D1, DOAR XAUUSD

**Întrebarea proprietarului:** „aurul are mișcări mai mari — ar merge botul mai bine dacă tranzacționează DOAR XAUUSD? Și dacă deschide mai multe tranzacții pe același grafic?”

**Răspuns scurt: NU se poate demonstra.** Configurația aleasă mecanic pe TRAIN (donchian-v1 pe H4, canal 100, SL 3×ATR, ieșire canal 20 — aceeași pe care a găsit-o și F6) are pe VALID (2019–2020, cont STANDARD) **n = 32, avg R +0,202, CI90 [−0,314; +0,764], PF 1,40**. Pică Poarta 1 la **n ≥ 40** (are 32) și la **CI90 inferior > −0,02** (are −0,31). ECN identic (+0,209, CI90 inf. −0,31). În plus: (i) **fără 2020** (raliul aurului) rezultatul pe VALID este **−0,21 R pe 17 tranzacții, PF 0,62**; (ii) **2 tranzacții din 32** (TP „de plasă” la 20×ATR, +6,6 R fiecare) aduc **204 % din câștigul net** — mediana tranzacției este **−0,66 R**; (iii) **toate tranzacțiile câștigătoare pe VALID intră în sesiunea asiatică (01/02/05 UTC)**, unde spread-ul modelat al aurului este 42–78 puncte, peste pragul live de 30 → un bot live cu setările actuale **nu le-ar fi deschis**; pe tranzacțiile rămase (n 16) rezultatul este **−0,37 R**. Verdict: **REJECTED**. Nimic nu se activează live.

Toate rulările: `research/results/GOLD/trend/*.json` (TRAIN 20 configurații STANDARD; config aleasă și pe ECN; VALID o singură dată, STANDARD + ECN), listele complete de tranzacții `*_D04_trades.jsonl`, jurnalul comenzilor `_runlog.txt`, grila pre-înregistrată `_grid.sh`. Protocolul (`docs/research/STAGE_F_PROTOCOL.md`) nu a fost modificat; TEST nu a fost atins.

## 1. Protocol urmat

- Date: `data/research.db` (STANDARD, aur ≈ 30 pct = 0,30 USD, ×1,3–2,6 în Asia/rollover) și `data/research_ecn.db` (ECN, ≈ 12 pct + 7 USD/lot). Bare H4 și D1 în UTC. TRAIN 2013-06-01 → 2019-01-01, VALID 2019-01-01 → 2021-01-01. **Un singur simbol: XAUUSD** (`--symbols XAUUSD`), `--htf NONE`, `--swap-mode table`, `--workers 2`.
- Risc (aplicat identic tuturor celor 20 de configurații, decis înainte de rulare): `{"max_open_positions": 1, "max_trades_per_day": 20, "max_spread_points": 100, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000, "cooldown_after_loss_minutes": 0, "rollover_blackout_minutes": 0, "max_total_open_risk_pct": 100}` — protecțiile de cont dezactivate (nu ele se măsoară), pragul de spread ridicat la 100 pentru că barele H4/D1 ale brokerului se închid la rollover (spread modelat 66–78 pct) și în Asia (39–42). **Spread-ul este plătit integral**; blocarea „spread > 20 % din SL” rămâne activă. Consecința pentru live este tratată în §5.
- Grila: **exact 20 de configurații** pe TRAIN/STANDARD, scrise în `_grid.sh` înainte de rulare. Selecție: cea mai mare `avg_r` dintre cele cu **n ≥ 60** (H4/D1). VALID: **o singură rulare** a configurației alese, pe ambele baze. Configurația F6 (donchian H4 entry 100 / SL 3×ATR) a fost inclusă ca una din cele 20 și **re-rulată** cu motorul corectat (spread la momentul deciziei): TRAIN n 92, +0,1958 (F6, pre-corecție: +0,1971) — reproducere confirmată.
- Fără plugin nou (toate cele 5 strategii existau); fără modificări la fișierele partajate; nimic comis în git.
- **Abatere de independență, declarată:** F6 a rulat deja aceeași configurație pe VALID (n 32, +0,211). Rularea de aici nu este deci o verificare „oarbă” a ei; ea este cerută de protocolul acestui studiu (regula a ales-o mecanic) și servește pentru numerele noi (motor corectat, listă completă de tranzacții, ECN pe TRAIN). Dacă regula ar fi ales altceva, F6 nu ar fi contat.

## 2. Grila pe TRAIN (XAUUSD, STANDARD, 2013-06 → 2019-01) — toate cele 20 de configurații

SL = 3×ATR(20) la toate (F6 arătase că 3×ATR > 2×ATR pe aur). „elig.” = n ≥ 60.

| # | id | Strategie | TF | Parametri | Ieșire | n | avg R | CI90 | PF | max DD (R) | elig. |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | D01 | donchian-v1 | H4 | entry 20 | channel(10) | 235 | +0,056 | [−0,080; +0,197] | 1,14 | 14,0 | da |
| 2 | D03 | donchian-v1 | H4 | entry 55 | channel(20) | 130 | +0,124 | [−0,144; +0,387] | 1,23 | 9,6 | da |
| **3** | **D04** | **donchian-v1** | **H4** | **entry 100** | **channel(20)** | **92** | **+0,196** | **[−0,086; +0,505]** | **1,42** | **6,2** | **da — ALEASĂ** |
| 4 | D05 | donchian-v1 | H4 | entry 100 | trailing (1R, 3×ATR14) | 98 | +0,147 | [−0,112; +0,421] | 1,29 | 6,9 | da |
| 5 | D06 | donchian-v1 | D1 | entry 20 | channel(10) | 48 | −0,125 | [−0,325; +0,078] | 0,68 | 9,1 | nu |
| 6 | D07 | donchian-v1 | D1 | entry 55 | channel(20) | 26 | −0,319 | [−0,574; −0,037] | 0,42 | 10,5 | nu |
| 7 | D08 | donchian-v1 | D1 | entry 100 | channel(20) | 18 | −0,457 | [−0,712; −0,147] | 0,26 | 10,4 | nu |
| 8 | M01 | ma-trend-v1 | H4 | EMA 50/100, trail 3 | combo[trailing, flip] | 151 | −0,025 | [−0,124; +0,085] | 0,90 | 14,4 | da |
| 9 | M02 | ma-trend-v1 | H4 | EMA 50/200, trail 3 | combo[trailing, flip] | 128 | −0,019 | [−0,129; +0,101] | 0,93 | 10,9 | da |
| 10 | M03 | ma-trend-v1 | D1 | EMA 50/100, trail 3 | combo[trailing, flip] | 35 | −0,178 | [−0,281; −0,068] | 0,30 | 6,2 | nu |
| 11 | M04 | ma-trend-v1 | D1 | EMA 20/100, trail 3 | combo[trailing, flip] | 41 | −0,097 | [−0,196; +0,013] | 0,51 | 5,2 | nu |
| 12 | T01 | tsmom-v1 | H4 | lookback 63, min_z 0,5 | signal_flip | 164 | +0,042 | [−0,165; +0,250] | 1,09 | 17,3 | da |
| 13 | T02 | tsmom-v1 | H4 | lookback 126, min_z 0 | signal_flip | 360 | −0,055 | [−0,159; +0,060] | 0,84 | 37,3 | da |
| 14 | T03 | tsmom-v1 | H4 | lookback 252, min_z 0,5 | signal_flip | 89 | +0,142 | [−0,265; +0,557] | 1,21 | 9,2 | da |
| 15 | T04 | tsmom-v1 | D1 | lookback 126, min_z 0,5 | signal_flip | 20 | +0,005 | [−0,376; +0,371] | 1,01 | 2,8 | nu |
| 16 | T05 | tsmom-v1 | D1 | lookback 63, min_z 0 | signal_flip | 82 | −0,049 | [−0,155; +0,063] | 0,78 | 8,7 | da |
| 17 | E01 | disc-f2-tsmom2 | H4 | k 2 (din 21/63/126/252) | signal_flip | 168 | −0,047 | [−0,291; +0,215] | 0,92 | 19,7 | da |
| 18 | E02 | disc-f2-tsmom2 | H4 | k 3 | signal_flip | 110 | −0,129 | [−0,406; +0,190] | 0,82 | 26,2 | da |
| 19 | H01 | disc-h06 | H4 | c 0,6 | TP 2R + time-stop 48 h | 7 | +0,812 | [+0,299; +1,347] | 20,7 | 0,3 | nu |
| 20 | H02 | disc-h06 | H4 | c 0,7 | TP 2R + time-stop 48 h | 38 | +0,246 | [+0,012; +0,467] | 1,97 | 2,1 | nu |

Fișiere: `train_<id>.json`. Pe ani (avg R) pentru configurațiile eligibile pozitive: D04 — 2013 +0,28 (10), 2014 +0,41 (14), 2015 +0,19 (18), 2016 +0,20 (15), **2017 −0,11 (20)**, 2018 +0,36 (15); D05 — +0,46 / +0,12 / +0,12 / +0,33 / −0,11 / +0,18; T03 — −0,22 / +0,29 / −0,00 / +0,51 / **−0,45** / +0,86; D03 — +0,31 / +0,36 / +0,03 / +0,01 / +0,17 / −0,02; D01 — +0,18 / +0,18 / +0,01 / +0,13 / +0,13 / −0,17.

Observații pe TRAIN:
- **Doar breakout-ul Donchian pe H4 este consistent pozitiv** pe aur (+0,06…+0,20, PF 1,14–1,42), dar **niciuna din cele 4 variante nu are CI90 care exclude zero**. Canalul scurt (entry 20, n 235) are cel mai mare eșantion și cel mai mic avantaj (+0,056): mai multe tranzacții ⇒ avantajul se topește spre zero, semn de zgomot + cost, nu de efect robust.
- **Pe D1 totul este negativ** pe aur (Donchian −0,13…−0,46, ma-trend −0,10…−0,18, tsmom −0,05…+0,01) și cu 18–82 tranzacții în 5,6 ani: aurul singur pe D1 nu produce nici eșantion, nici avantaj. Swap-ul (−0,5 pip/zi) pe poziții de săptămâni apasă vizibil.
- Medii mobile (ma-trend) și momentum (tsmom-v1, tsmom2) pe H4: negative sau ≈ 0 în 6 din 8 variante; tsmom lookback 252 (+0,14, n 89) e singura pozitivă și are 2017 la −0,45 și 2018 la +0,86 — alternanță de ani, nu platou.
- **disc-h06 pe aur arată cel mai bine (+0,81 / +0,25) dar cu 7 și 38 de tranzacții** — ineligibil (n < 60) și exact tipul de rezultat „pe margine” pe care verificarea F3 l-a respins (H06 cu c 0,6 pe 12 instrumente: +0,21 la n 68 → −0,04 la c 0,84). Nu a fost dus pe VALID.

## 3. Configurația aleasă și de ce

Regula §4: max `avg_r` pe TRAIN/STANDARD cu n ≥ 60 → **D04: donchian-v1, H4, XAUUSD, `entry_n` 100, `exit_n` 20, `atr_n` 20, `sl_atr` 3, `tp_atr` 20 (plasă), ieșire default `channel(20)`, swap `table`**. TRAIN STANDARD: n 92, avg R +0,196, CI90 [−0,086; +0,505], PF 1,42, win 39 %, max DD 6,2 R, net +18,0 R (swap −2,6 R), 46 BUY (+0,15) / 46 SELL (+0,25), durată medie 9,8 zile, ≈ 16 tranzacții/an. TRAIN ECN: n 92, +0,202, CI90 [−0,079; +0,513], PF 1,44.

Aleasă **mecanic**, deși este aceeași configurație ca în F6 și deși (observat înainte de VALID) 16 tranzacții/an înseamnă ≈ 32 pe 2 ani — sub pragul n ≥ 40 al Porții 1. Regula nu are criteriu de frecvență, iar introducerea unuia după vederea grilei ar fi fost o abatere. Lipsa de putere statistică pe un singur simbol este în sine răspunsul la întrebarea proprietarului.

Concentrarea pe TRAIN, înainte de VALID: **top 5 % (5 tranzacții) = +30,1 R = 167 % din net**; cele 4 TP la 20×ATR = +26,3 R (mai mult decât tot netul de +18,0); **mediana tranzacției −0,35 R**; fără cele 5 tranzacții de top avg R = −0,14. Strategia este un bilet de loterie pe trend-uri rare, nu un flux de câștiguri.

Comanda exactă (VALID STANDARD):

```
/tmp/claude-0/venv/bin/python research/run_bt.py --db data/research.db --strategy donchian-v1 --tf H4 --htf NONE \
  --symbols XAUUSD --split valid --swap-mode table --workers 2 --strategy-json '{"entry_n": 100, "sl_atr": 3.0}' \
  --risk-json '{"max_open_positions": 1, "max_trades_per_day": 20, "max_spread_points": 100, "max_daily_loss_pct": 100, "max_consecutive_losses": 1000, "cooldown_after_loss_minutes": 0, "rollover_blackout_minutes": 0, "max_total_open_risk_pct": 100}' \
  --json-out research/results/GOLD/trend/valid_std_D04_donchian_H4_e100_sl3_ch20.json
```

## 4. VALID (2019-01-01 → 2021-01-01) — o singură rulare

| Cont | n | win | avg R | CI90 | PF | max DD (R) | serie pierderi | net R | swap R |
|---|---|---|---|---|---|---|---|---|---|
| **STANDARD** (`valid_std_D04_…json`) | 32 | 0,375 | **+0,202** | **[−0,314; +0,764]** | **1,40** | 4,8 | 5 | +6,46 | −0,70 |
| ECN (`valid_ecn_D04_…json`) | 32 | 0,375 | +0,209 | [−0,307; +0,771] | 1,41 | 4,8 | 5 | +6,67 | −0,70 |

Pe ani (STANDARD; ECN în paranteză):

| An | n | avg R | sumă R | win |
|---|---|---|---|---|
| 2019 | 17 | **−0,210** (−0,199) | −3,57 | 0,29 |
| 2020 | 15 | **+0,668** (+0,671) | +10,02 | 0,47 |

Ieșiri: CHANNEL 17 (+4,96 R), SL 12 (−12,13), **TP 2 (+13,18)**, END 1 (+0,45, închisă forțat la 2021-01-01). Direcție: 22 BUY (+0,48 avg, +10,6 R) / 10 SELL (−0,42 avg, −4,2 R). Contoare: 150 candidați, 118 blocați „poziție deja deschisă”, 32 semnale, 32 umplute, 0 blocate de piață.

**Dependența de 2020 și de câteva tranzacții (STANDARD):**
- **Fără 2020:** n 17, avg R **−0,210**, PF **0,62**, CI90 [−0,63; +0,21]. Tot câștigul vine din raliul aurului din 2020 (+25 %); în 2019 — tot un an ascendent (+18 %) — strategia a pierdut.
- **Top 5 % = 2 tranzacții** (BUY 2019-12-24 → 2020-01-05 și BUY 2020-06-22 → 2020-07-27, ambele TP la 20×ATR, +6,6 R fiecare) = **+13,2 R = 204 % din netul de +6,5 R**. Fără ele: n 30, avg R −0,22. Top 3 = 237 % din net. **Mediana tranzacției: −0,66 R.**
- ECN schimbă +0,007 R/tranzacție: spread-ul aurului (0,30 → 0,12 USD) este ≈ 1 % din SL-ul de 3×ATR H4; costul dominant este swap-ul, identic.

### Poarta 1 (STANDARD)

| Criteriu | Prag | Valoare | Trece? |
|---|---|---|---|
| avg R | > 0 | +0,202 | da |
| CI90 inferior | > −0,02 | **−0,314** | **NU** |
| PF | > 1,10 | 1,40 | da |
| n | ≥ 40 (H4/D1) | **32** | **NU** |
| cel puțin un an cu avg R > 0 | — | 2019 −0,21 / 2020 +0,67 | da |

**Verdict: REJECTED** (2 din 5 criterii picate; ECN identic → nici `ECN_ONLY`). Nu se cere verificarea adversarială, nu se rulează TEST.

## 5. Nota „ora din zi”: câștigul este exact unde botul live nu poate intra

Barele H4 ale brokerului se închid la 21/01/05/09/13/17 UTC (vară) sau 22/02/06/10/14/18 (iarnă). Spread-ul modelat STANDARD al aurului la intrare, după ora UTC a deciziei: 13–18 UTC → 27 pct; 21/22 → 30/32; 09/10 → 36/33; 05/06 → 42/39; **01/02 → 66/78**. Pragul live `max_spread_points` este 30, iar `rollover_blackout_minutes` (±20 min în jurul 21/22 UTC) blochează decizia de la rollover.

| Set | Intrări cu spread > 30 (blocate live) | Intrări la 21/22 UTC (rollover) | **Rămase cu setările live** |
|---|---|---|---|
| TRAIN (n 92, +18,0 R) | n 27, **+6,9 R** | n 5, +4,9 R | **n 63, avg R +0,064, +4,0 R** |
| VALID (n 32, +6,5 R) | n 14, **+11,5 R** | n 3, +1,1 R | **n 16, avg R −0,372, −6,0 R** |

Pe VALID, intrările de la 01:00 UTC (n 3, +5,5 R, spread 66) și 02:00 UTC (n 3, +4,6 R, spread 78) conțin **ambele TP-uri mari**; intrările din sesiunea Londra/New York (13–18 UTC, n 16) au dat −6,0 R. Deci chiar dacă cineva ar ignora poarta statistică, **un bot live cu setările actuale nu ar fi reprodus câștigul** — l-ar fi filtrat, păstrând pierderile. Iar ridicarea pragului de spread la 100 pe aur ar însemna să plătești 0,66–0,78 USD/oz spread la fiecare intrare asiatică — exact ce se vede aici, fără niciun avantaj dovedit în schimb. Spread-ul este modelat (conservator, nu istoric): la 01–02 UTC pe aur, cel real al unui broker retail poate fi și mai larg la știri.

## 6. Răspuns la întrebările proprietarului

1. **„Doar aur, pentru că are mișcări mai mari”:** mișcările mai mari nu ajută în R: SL-ul este 3×ATR, deci se scalează cu volatilitatea, iar R-ul pe tranzacție nu depinde de mărimea absolută a mișcărilor. Ce contează este dacă regulile au avantaj după costuri — pe aur, pe H4 și D1, 9 ani de date nu o pot arăta (CI90 [−0,31; +0,76]). Pe D1 aurul singur este clar negativ.
2. **„Rezultate mai rapide, mai mari și mai corecte pe un singur grafic”:** invers. Un simbol pe H4 dă ≈ 16 tranzacții/an; 2 ani de VALID = 32 de tranzacții, insuficient chiar și pentru pragul minim al protocolului (40). Un singur instrument înseamnă și o singură poveste (raliul din 2020 = 155 % din câștig): rezultatul este mai **puțin** corect statistic, nu mai mult. Diversificarea pe instrumente este cea care dă eșantion și netezește anii; problema F2/F6 era că nici pe 12 instrumente trend-ul nu avea avantaj după costuri — nu că erau prea multe instrumente.
3. **„Mai multe tranzacții deodată pe același grafic”:** motorul (live și backtest) permite o poziție a botului pe simbol. O a doua poziție pe același instrument, în aceeași direcție, este același pariu cu corelație 1: **dublează riscul și drawdown-ul, nu avantajul**. Dacă avg R ar fi fost pozitiv, mărimea poziției se reglează prin riscul pe tranzacție, nu prin număr de poziții; dacă este zero sau negativ (cazul demonstrabil aici), două poziții pierd de două ori mai repede. Nu a fost modificat nimic în această direcție.

## 7. Caveats

1. **Selecție în straturi**: 5 strategii × 2 TF × parametri = 20 configurații, pe un univers (XAUUSD) ales **după** ce F2/F6 arătaseră aurul drept singurul instrument pozitiv la Donchian. Cel mai bun avg R din 20 este optimist prin construcție; VALID era deja văzut pentru configurația aleasă (F6). Toate cele 20 sunt raportate și numărate.
2. **Eșantion minuscul**: 92 (TRAIN) și 32 (VALID) tranzacții; CI90 ±0,3 R și ±0,55 R. Un avantaj real de +0,1 R nu ar fi detectabil; un rezultat de +0,2 R pe 32 de tranzacții nu înseamnă nimic.
3. **Dependența de cozi**: 6 tranzacții din 124 (TRAIN+VALID) la TP 20×ATR = +39,5 R față de un net total de +24,5 R. Mediana tranzacției este negativă în ambele seturi. Un astfel de profil cere sute de tranzacții ca să se distingă de noroc.
4. **Setări de cercetare ≠ setări live**: spread 100 și fără blackout de rollover (uniform, decis înainte, spread plătit integral). §5 arată că cu setările live rezultatul pe VALID ar fi fost −0,37 R.
5. **Swap modelat** (−0,5 pip/zi/direcție). Pe aur swap-ul real pe cumpărare la brokerii retail este de regulă de câteva ori mai negativ; cu 277 zile de swap pe 32 de tranzacții (durată medie 8,6 zile), netul de +6,5 R s-ar reduce vizibil. Semnul pe VALID poate depinde de asta.
6. **Spread modelat pe oră**, nu istoric; fără calendar economic (NFP, FOMC, 2020-03) — rezultatele sunt, dacă ceva, optimiste.
7. **Bare H4 pe granițele brokerului** (deplasate cu o oră la DST New York); `entry_n` 100 bare ≈ 17 zile.
8. disc-h06 pe aur (n 7 / 38) nu a fost dus mai departe: regula cere n ≥ 60; rezultatele cu CI90 pozitiv la n 7 sunt artefacte de eșantion mic.
9. Nicio configurație alternativă nu a fost rulată pe VALID (D05, T03, D03 rămân doar pe TRAIN) — protocolul permite o singură configurație.
10. **Rezultatele din trecut nu garantează rezultatele viitoare**; un backtest care nu trece poarta nu este „aproape bun”, este nedemonstrat.

## 8. Fișiere

- `research/results/GOLD/trend/_grid.sh` — grila pre-înregistrată (20 configurații), `_runlog.txt` — jurnalul tuturor rulărilor.
- `train_<id>.json` (20, STANDARD), `train_std_D04_…json` / `train_ecn_D04_…json` (config aleasă, ambele baze, cu `train_std_D04_trades.jsonl` / `train_ecn_D04_trades.jsonl`).
- `valid_std_D04_donchian_H4_e100_sl3_ch20.json`, `valid_ecn_D04_donchian_H4_e100_sl3_ch20.json` + `valid_std_D04_trades.jsonl`, `valid_ecn_D04_trades.jsonl`.
- Niciun fișier partajat modificat, niciun plugin nou, nimic comis în git.
