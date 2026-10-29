# F3 / disc-h06 H4 (c 0,7 · range_mult 1,3 · close_frac 0,3) — verificare adversarială (c): supra-optimizare și testare multiplă

Verificator independent, protocol §5(c). Lentilă: câte configurații au fost încercate; configurația aleasă e vârf ascuțit sau platou (vecini ±20 % re-rulați pe TRAIN); stabilitatea pe ani și pe instrumente a rezultatului VALID; plauzibilitatea rezultatului VALID sub ipoteza nulă, dat fiind 6 familii × ≤ 24 configurații. Re-rularea comenzii VALID alese: **o singură dată**. Niciun fișier modificat în afara acestui raport; nimic rulat pe TEST; nicio variantă rulată pe VALID.

**Vot: RESPING (refuted = true).** Rezultatul VALID se reproduce cifră cu cifră, dar pe lentila supra-optimizării nu supraviețuiește: (1) configurația aleasă stă pe o **margine**, nu pe un platou — slăbirea cu 20 % a filtrului de compresie `c` (0,7 → 0,84) schimbă semnul (−0,043 R pe 1 791 tranzacții, CI90 [−0,083; +0,003]), iar slăbirea cu 20 % a tuturor celor trei filtre dă **−0,082 R pe 2 659 tranzacții, CI90 integral negativ**; (2) pe TRAIN niciuna din cele 28 de configurații din vecinătate (24 inițiale + 27 vecini rulați aici) nu are CI90 inferior > 0 — TRAIN nu a adus nicio dovadă de avantaj, a adus doar o alegere; (3) concordanța pe instrumente TRAIN → VALID este **zero** (Spearman 0,01); (4) un VALID de +0,134 cu CI90 [−0,012; +0,278] are p ≈ 0,06 unilateral ca test unic, iar cu 6 familii care au fiecare o rulare VALID, probabilitatea ca **cel puțin una** să treacă Poarta 1 sub ipoteza nulă globală este ≈ 0,3–0,4. Rezultatul este exact ce ne-am aștepta să vedem o dată la 6 familii fără niciun avantaj real.

## 1. Re-rularea comenzii VALID alese (o singură rulare, STANDARD)

Comanda din SUMMARY §3 + `--split valid --workers 1 --trades-out` (ieșire în scratchpad, JSON-ul familiei nu a fost atins): **n = 149, avg R = +0,134, CI90 [−0,0124; +0,2777], PF 1,349, max DD 9,4 R, net +19,96 R, 2019 +0,148 / 2020 +0,118** — identic cu `valid_H06_N4_all.json`. Reproducerea nu este în discuție.

## 2. Câte configurații au fost încercate

SUMMARY §2 raportează **exact 24** pe TRAIN (20 ipoteze implicite + 4 vecini ai H06), plus 4 rulări ECN pe TRAIN și 2 rulări VALID (STANDARD + ECN) care nu sunt configurații noi. `grid_log.jsonl` confirmă ordinea: cele 20 implicite (09:13–09:18 UTC), apoi N1–N4 (09:18–09:20). Bugetul protocolului a fost respectat formal.

Dar mecanismul de selecție contează mai mult decât numărul: H06 implicit a ieșit pozitiv la **n = 43** (neeligibil), iar cei 4 vecini au fost construiți **după** ce s-a văzut acest rezultat, cu scopul declarat de a slăbi filtrele „până la n ≥ 60”. N1 (doar `c` 0,7) a dat +0,011 pe 243; N2/N3 au rămas la n = 54; N4 (toate trei slăbite) a dat +0,053 pe 370 și a fost singura eligibilă cu avg R vizibil pozitiv. Cu alte cuvinte, configurația aleasă este **prima slăbire a filtrelor care atinge simultan n ≥ 60 și avg R > 0** — o alegere pe margine, prin construcție. La nivelul întregii etape, cele 6 familii au încercat în total ≈ 138 configurații pe TRAIN și au consumat 6 rulări VALID.

## 3. Vârf ascuțit sau platou? Vecinii ±20 % pe TRAIN (STANDARD, 2013-06-01 → 2019-01-01)

27 de rulări noi, toate pe `data/research.db`, aceeași comandă ca cea aleasă (risc 6/30/3, `--workers 1`), doar `--strategy-json` schimbat. Fișierele: scratchpad `overfit/train_<nume>.json` (+ `.out`). `base` = configurația aleasă (re-rulată, identică cu `train_H06_N4_all.json`).

| nume | parametri | n | avg R | CI90 | PF | net R | 2018 avg R (n) |
|---|---|---|---|---|---|---|---|
| base | c 0.7, range_mult 1.3, close_frac 0.3 | 370 | +0.0533 | [-0.045; +0.146] | 1.121 | +19.7 | -0.1207 (39) |
| c0.56 | c 0.56, range_mult 1.3, close_frac 0.3 | 27 | +0.2804 | [-0.061; +0.624] | 1.822 | +7.6 | 0.0154 (1) |
| c0.84 | c 0.84, range_mult 1.3, close_frac 0.3 | 1791 | -0.0426 | [-0.083; +0.003] | 0.912 | -76.2 | -0.1357 (273) |
| c0.60 | c 0.6, range_mult 1.3, close_frac 0.3 | 68 | +0.2136 | [-0.008; +0.438] | 1.601 | +14.5 | -0.6359 (5) |
| c0.65 | c 0.65, range_mult 1.3, close_frac 0.3 | 162 | +0.1176 | [-0.024; +0.248] | 1.314 | +19.1 | -0.7211 (14) |
| c0.75 | c 0.75, range_mult 1.3, close_frac 0.3 | 774 | -0.0267 | [-0.094; +0.038] | 0.945 | -20.7 | -0.2089 (97) |
| c0.80 | c 0.8, range_mult 1.3, close_frac 0.3 | 1305 | -0.0249 | [-0.076; +0.024] | 0.948 | -32.5 | -0.1987 (183) |
| rm1.04 | c 0.7, range_mult 1.04, close_frac 0.3 | 510 | +0.0177 | [-0.067; +0.106] | 1.036 | +9.1 | -0.1423 (54) |
| rm1.56 | c 0.7, range_mult 1.56, close_frac 0.3 | 262 | +0.0213 | [-0.085; +0.128] | 1.053 | +5.6 | -0.3505 (26) |
| rm1.1 | c 0.7, range_mult 1.1, close_frac 0.3 | 476 | +0.0056 | [-0.076; +0.094] | 1.012 | +2.7 | -0.2941 (49) |
| rm1.2 | c 0.7, range_mult 1.2, close_frac 0.3 | 419 | +0.0641 | [-0.023; +0.153] | 1.145 | +26.8 | -0.1274 (43) |
| rm1.4 | c 0.7, range_mult 1.4, close_frac 0.3 | 320 | +0.0082 | [-0.089; +0.105] | 1.019 | +2.6 | -0.3277 (32) |
| rm1.5 | c 0.7, range_mult 1.5, close_frac 0.3 | 289 | +0.0447 | [-0.055; +0.147] | 1.109 | +12.9 | -0.3068 (27) |
| cf0.24 | c 0.7, range_mult 1.3, close_frac 0.24 | 331 | +0.0170 | [-0.070; +0.111] | 1.039 | +5.6 | -0.0442 (36) |
| cf0.36 | c 0.7, range_mult 1.3, close_frac 0.36 | 397 | +0.0568 | [-0.036; +0.160] | 1.128 | +22.6 | -0.1661 (41) |
| cf0.20 | c 0.7, range_mult 1.3, close_frac 0.2 | 297 | +0.0234 | [-0.070; +0.123] | 1.056 | +6.9 | -0.0769 (31) |
| cf0.25 | c 0.7, range_mult 1.3, close_frac 0.25 | 340 | +0.0149 | [-0.079; +0.107] | 1.033 | +5.1 | -0.0715 (37) |
| cf0.35 | c 0.7, range_mult 1.3, close_frac 0.35 | 393 | +0.0641 | [-0.028; +0.156] | 1.145 | +25.2 | -0.1661 (41) |
| cf0.40 | c 0.7, range_mult 1.3, close_frac 0.4 | 416 | +0.0527 | [-0.038; +0.141] | 1.117 | +21.9 | -0.2069 (44) |
| all_loose20 | c 0.84, range_mult 1.04, close_frac 0.36 | 2659 | -0.0820 | [-0.118; -0.046] | 0.848 | -218.1 | -0.1914 (300) |
| all_tight20 | c 0.56, range_mult 1.56, close_frac 0.24 | 17 | -0.0205 | [-0.389; +0.328] | 0.943 | -0.3 | 0.0154 (1) |
| hold36 | c 0.7, range_mult 1.3, close_frac 0.3, hold_hours 36 | 372 | +0.0135 | [-0.071; +0.102] | 1.033 | +5.0 | -0.2009 (38) |
| hold60 | c 0.7, range_mult 1.3, close_frac 0.3, hold_hours 60 | 369 | +0.0523 | [-0.044; +0.145] | 1.114 | +19.3 | -0.1232 (39) |
| tp1.6 | c 0.7, range_mult 1.3, close_frac 0.3, tp_r 1.6 | 372 | +0.0195 | [-0.067; +0.104] | 1.045 | +7.3 | -0.1858 (39) |
| tp2.4 | c 0.7, range_mult 1.3, close_frac 0.3, tp_r 2.4 | 367 | +0.0729 | [-0.033; +0.170] | 1.165 | +26.8 | -0.049 (39) |
| compmin2 | c 0.7, range_mult 1.3, close_frac 0.3, comp_min 2 | 630 | -0.0030 | [-0.072; +0.062] | 0.994 | -1.9 | -0.1348 (69) |
| compmin4 | c 0.7, range_mult 1.3, close_frac 0.3, comp_min 4 | 194 | +0.1164 | [-0.010; +0.237] | 1.297 | +22.6 | -0.243 (22) |

Lectură:

- **`c` (pragul de compresie) este o pantă, nu un platou.** avg R scade **monoton** cu `c`, iar eșantionul crește de ≈ 5× la fiecare +0,1: 0,56 → 27 tranz. / +0,28; 0,60 → 68 / +0,21; 0,65 → 162 / +0,12; **0,70 → 370 / +0,053**; 0,75 → 774 / −0,027; 0,80 → 1 305 / −0,025; 0,84 → 1 791 / −0,043 (CI90 [−0,083; +0,003]). Configurația aleasă este **ultimul punct** în care avg R > 0 și n ≥ 60 sunt simultan adevărate. Un vecin la −20 % (0,56) are 27 de tranzacții — neevaluabil; vecinul la +20 % (0,84) este negativ pe un eșantion de 5× mai mare. Aceasta este definiția unui vârf ascuțit în raport cu regula de selecție, nu a unui platou.
- Populația largă a ipotezei pierde: **toate trei filtrele slăbite cu 20 %** (`all_loose20`) → **n = 2 659, avg R −0,082, CI90 [−0,118; −0,046], PF 0,85**. „Compresie → bară de expansiune” în forma ei generală are speranță **semnificativ negativă** după costuri STANDARD; rezultatul pozitiv trăiește într-o coadă subțire, selectată.
- `range_mult` și `close_frac` sunt platouri **în jurul lui zero**: `range_mult` 1,04 … 1,56 dă +0,006 … +0,064 (non-monoton: 1,3 → +0,053, 1,4 → +0,008, 1,5 → +0,045); `close_frac` 0,20 … 0,40 dă +0,015 … +0,064. Niciun vecin nu are CI90 inferior > 0; toate au CI90 care include zero cu marjă largă.
- Ieșirea: `hold_hours` 36 → +0,014, 60 → +0,052; `tp_r` 1,6 → +0,020, 2,4 → +0,073. Tot zgomot în banda [0; 0,07].
- `comp_min` 2 → −0,003 (n 630); 4 → +0,116 (n 194): aceeași poveste ca `c` — cu cât filtrul e mai strâns, cu atât avg R e mai mare și n mai mic. Compatibil atât cu un avantaj real concentrat în compresia adâncă, cât și cu zgomot de eșantion mic; dar niciuna din versiunile strânse nu are CI90 inferior > 0 pe TRAIN (cea mai apropiată: `c` 0,60 [−0,008; +0,438] pe 68 tranzacții).
- Media **ponderată cu numărul de tranzacții** a celor 6 vecini ±20 % (câte un parametru pe rând) este **-0.0078 R** pe 3318 tranzacții — adică zero.
- **2018 este negativ în toate configurațiile cu n > 1** (de la −0,04 la −0,72). Nu există nicio variantă a ipotezei care să fi câștigat în ultimul an al TRAIN-ului; la fel, etapa E a măsurat H06 implicit la **−0,058 pe 106 tranzacții** pe 2021–2023 (alt feed, `docs/research/STAGE_E_DISCOVERY.md`). Perioadele în care ipoteza a mers: 2015–2017 și 2019–2020.

Concluzia pe acest punct: pe TRAIN, întreaga vecinătate stă în banda [−0,08; +0,12] R, cu o relație mecanică „filtru mai strâns ⇒ avg R mai mare ⇒ n mai mic”. TRAIN nu a furnizat o dovadă (niciun CI90 strict pozitiv în 51 de configurații H06 rulate în total), ci doar o **alegere** făcută pe margine. Tot ce susține configurația este o singură rulare VALID.

## 4. Stabilitatea rezultatului VALID (pe ani, trimestre, instrumente, tranzacții)

Din lista de tranzacții a re-rulării (149):

- **Pe ani**: 2019 +0,148 (79), 2020 +0,118 (70) — ambii pozitivi, cum raportează SUMMARY. Dar granularitatea de un an ascunde structura:
- **Pe trimestre**: 2019Q1 +0,78 R (15), Q2 +0,48 (21), Q3 +4,28 (17), Q4 +6,16 (26), 2020Q1 +7,10 (5 tranzacții!), **Q2 −5,71 (35)**, Q3 +11,14 (17), **Q4 −4,27 (13)**. Trei trimestre (2019Q3–Q4, 2020Q3) aduc +21,6 R din +20,0 net; 2020Q2 și Q4, cu 48 de tranzacții, pierd −10 R. **11 din 22 de luni** cu tranzacții sunt pozitive — o monedă aruncată. Iulie 2020 singur: +10,1 R pe 12 tranzacții = jumătate din profitul celor doi ani.
- **Pe direcție**: SELL +0,227 R (77, +17,5 R), BUY +0,034 (72, +2,5 R). Pe TRAIN (ultimele 300): SELL +0,124, BUY +0,024. Avantajul, atât cât e, e doar pe short — nu face parte din ipoteză și nu a fost pre-declarat.
- **Pe instrumente, concordanța TRAIN → VALID este nulă**: Spearman **0,01** (Pearson 0,04) pe 12 instrumente (0,08 fără AUDJPY, n = 1). Cele mai bune pe TRAIN — USDJPY (+0,23 / 56) și EURGBP (+0,61 / 24) — dau pe VALID **−0,32 / 19** și +0,04 / 14; cele mai bune pe VALID — GBPJPY (+0,67), USDCAD (+0,47), GBPUSD (+0,33) — erau pe TRAIN +0,22, **−0,07** și +0,06. Trei instrumente aduc +20,7 R din +20,0 net; fără GBPJPY avg R cade la +0,083, fără GBPUSD la +0,102 (sub pragul implicit de CI90 al Porții 1). Dacă avantajul ar fi real și structural (compresie → expansiune), ne-am aștepta ca instrumentele care au mers pe 5,6 ani să continue; nu continuă.
- **Pe tranzacții**: cele mai bune 10 tranzacții din 149 (toate ≈ +2 R, TP) însumează +19,84 R = **întreg profitul net** (+19,96); fără cele mai bune 5, avg R = +0,069. Decompoziția pe ieșire (TIME +20,5 R; SL −45,6; TP +45,0) e identică cu a verificatorului (a). Nu sunt outlieri de date, dar e un rezultat subțire: ≈ 7 % din tranzacții decid semnul.

## 5. Este plauzibil sub ipoteza nulă?

- Ca **test unic**, VALID: t = 1,54, **p ≈ 0,062 unilateral** (bootstrap CI90 [−0,007; +0,277], SE 0,087). TRAIN nu adaugă aproape nimic: z = 0,92 (p ≈ 0,18). Combinate prin inversul varianței: avg R ≈ +0,078, z ≈ 1,6, p ≈ 0,055 — tot nesemnificativ la pragul CI90 > 0 pe care îl cere Poarta 2.
- **Testare multiplă la nivel de etapă**: Poarta 1 cere, pentru n ≈ 150 (SE ≈ 0,087), avg R > ≈ 0,123, adică z > 1,41, **p ≈ 0,08 per familie**. Cu 6 familii care au fiecare o rulare VALID independentă, P(cel puțin o familie trece Poarta 1 | niciun avantaj real) = 1 − 0,92⁶ ≈ **0,39** (≈ 0,32 la pragul observat p = 0,062). Faptul că exact o familie din șase a trecut, la limită (marjă 0,008 R pe CI90), este rezultatul **modal** sub nul. În plus, F6 a avut +0,211 pe 32 tranzacții (picată doar la n și la CI) — a doua „aproape-trecere” din șase.
- Selecția pe TRAIN din 24 nu umflă direct VALID-ul (VALID e out-of-sample), dar umflă **probabilitatea de a ajunge la VALID cu o configurație de zgomot**: din 17 configurații eligibile, exact 3 aveau avg R > 0 pe TRAIN, toate cu CI90 care include zero; cea aleasă era pur și simplu cea mai mare dintre trei valori nesemnificative.
- Semnele clasice de supra-potrivire sunt toate prezente: a 24-a configurație din 24; parametrii găsiți prin slăbirea filtrelor până la pragul de eligibilitate; sensibilitate de semn la ±20 % pe parametrul principal; versiunea generală a ipotezei semnificativ negativă; instrumentele câștigătoare se schimbă complet între TRAIN și VALID; profitul VALID concentrat în 3 trimestre și 10 tranzacții; perioada ulterioară (2021–2023, etapa E) negativă.

## 6. Ce NU resping

- Lookahead / corectitudinea codului — nu e lentila mea; verificatorul (a) a reprodus toate 149 de tranzacții independent.
- Că ipoteza „compresie adâncă → expansiune pe H4” ar putea avea un sâmbure real: curba monotonă pe `c` și `comp_min` (filtru mai strâns ⇒ avg R mai mare) e compatibilă cu asta. Dar la nivelul de strângere la care eșantionul devine evaluabil, avantajul măsurat pe 5,6 ani este indistinct de zero, iar versiunea rulată pe VALID este cea de la margine, nu cea „adâncă”. Dacă cineva vrea să urmărească ideea, trebuie **re-pre-înregistrată** (ex. `c` 0,6–0,65, direcție SELL, portofoliu larg) și testată pe un eșantion forward, nu promovată acum pe TEST cu această configurație.

## 7. Verdict pe lentila (c)

**RESPING (refuted = true), încredere ridicată.** Configurația aleasă nu stă pe un platou: pe parametrul care contează (`c`) semnul se inversează la +20 %, versiunea generală a ipotezei este semnificativ negativă pe TRAIN, concordanța pe instrumente TRAIN → VALID este zero, iar rezultatul VALID (p ≈ 0,06 ca test unic) este exact ce produce ipoteza nulă o dată la 6 familii. Recomandare: **nu se promovează pe TEST**; TEST-ul (14 luni, ≈ 100 tranzacții așteptate) nu ar putea oricum da CI90 > 0 decât cu un avg R > ≈ 0,15 — adică ar cere ca VALID-ul să fie subestimat, nu doar real.

Fișiere produse: doar acest raport. Rulările vecinilor și re-rularea VALID (`valid_rerun.json`, `valid_trades.jsonl`, `train_*.json`, `grid.log`, `run_grid.sh`, `table.py`) sunt în scratchpad: `/tmp/claude-0/-home-user-trandingbot/005b2e16-62ce-5573-b4b2-692c31da423d/scratchpad/overfit/`. Niciun fișier partajat modificat; nimic comis; nimic rulat pe TEST; VALID rulat o singură dată.
