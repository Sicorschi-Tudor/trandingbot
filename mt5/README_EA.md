# AIBridgeEA — instalare în MetaTrader 5 (v1)

`AIBridgeEA.mq5` este puntea dintre terminalul MetaTrader 5 și serverul botului (FastAPI, `http://127.0.0.1:8787`).

Ce face:
- la fiecare 500 ms trimite serverului cotațiile, barele, datele contului, pozițiile și deal-urile botului (endpoint `/bridge/v1/sync`);
- execută **doar** comenzile primite de la server (`OPEN`, `MODIFY`, `CLOSE`). EA-ul nu ia singur nicio decizie de tranzacționare;
- nu atinge niciodată pozițiile care nu au magic number-ul botului (`552211` implicit);
- în v1 refuză orice `OPEN` dacă contul nu este **DEMO** (parametrul `InpAllowRealAccount = false`).

> Serverul botului și MetaTrader 5 trebuie să ruleze pe **același calculator** (EA-ul se conectează la `127.0.0.1`).

---

## 1. Pregătire

1. Pornește serverul botului (vezi `README.md` din rădăcina proiectului) și verifică în browser că `http://127.0.0.1:8787` se deschide.
2. În fișierul `.env` al proiectului, `MT5_BRIDGE_KEY` trebuie să aibă o valoare (un șir lung, aleator). Dacă e gol, serverul refuză EA-ul cu HTTP 403. Repornește serverul după ce modifici `.env`.
3. În `.env` pune `DATA_SOURCE=mt5_bridge`.
4. Loghează-te în MetaTrader 5 cu **contul demo** (File > Login to Trade Account).

## 2. Copierea EA-ului în MetaTrader 5

### Windows
1. În MetaTrader 5: **File > Open Data Folder**.
2. Deschide `MQL5\Experts`.
3. Copiază acolo fișierul `mt5/AIBridgeEA.mq5` din proiect.

### macOS
MetaTrader 5 pentru macOS rulează într-un mediu Wine, dar meniul este același:
1. În MetaTrader 5: **File > Open Data Folder** — se deschide Finder-ul în folderul de date al terminalului.
2. Deschide `MQL5/Experts`.
3. Copiază acolo fișierul `AIBridgeEA.mq5`.

   Dacă meniul nu deschide Finder-ul, folderul se află de obicei în
   `~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts`
   (în Finder: Go > Go to Folder… și lipește calea).

## 3. Compilare

1. În MetaTrader 5 apasă **F4** (sau butonul *IDE*) ca să deschizi **MetaEditor**.
2. În Navigator-ul din MetaEditor deschide `Experts > AIBridgeEA.mq5`.
3. Apasă **F7** (Compile). În tab-ul *Errors* jos trebuie să apară `0 errors`. Avertismentele (warnings) nu blochează.
4. Se creează `AIBridgeEA.ex5`. Înapoi în MetaTrader 5, în *Navigator* (Ctrl+N) > *Expert Advisors*, dă click dreapta > *Refresh* dacă EA-ul nu apare.

## 4. Setările terminalului (obligatoriu)

**Tools > Options > Expert Advisors** (pe macOS: *MetaTrader 5 > Preferences* sau *Tools > Options*, în funcție de versiune):

- bifează **Allow algorithmic trading**;
- bifează **Allow WebRequest for listed URL**;
- adaugă în listă: `http://127.0.0.1:8787`
  - dacă tot primești eroarea 4014, adaugă și `http://localhost:8787` (și setează în EA `InpServerUrl = http://localhost:8787`);
- apasă **OK**.

Apoi apasă butonul **Algo Trading** din bara de sus a terminalului, astfel încât să fie verde (activ).

## 5. Atașarea EA-ului pe grafic

1. Deschide orice grafic (de exemplu EURUSD, M5). EA-ul se atașează pe **un singur** grafic; simbolurile urmărite vin de la server (`ACTIVE_SYMBOLS` din `.env`), nu din graficul ales.
2. Trage `AIBridgeEA` din Navigator pe grafic.
3. În fereastra care apare:
   - tab-ul **Common**: bifează *Allow Algo Trading*;
   - tab-ul **Inputs**:

| Parametru | Valoare | Explicație |
|---|---|---|
| `InpServerUrl` | `http://127.0.0.1:8787` | Adresa serverului botului |
| `InpBridgeKey` | valoarea `MT5_BRIDGE_KEY` din `.env` | Cheia secretă a punții (copiaz-o exact) |
| `InpMagic` | `552211` | Trebuie să fie egal cu `MT5_MAGIC` din `.env` |
| `InpTimerMs` | `500` | Intervalul de sincronizare (ms) |
| `InpDeviationPoints` | `20` | Deviația maximă implicită la execuție |
| `InpHistoryBars` | `300` | Câte bare de istoric trimite când serverul le cere |
| `InpAllowRealAccount` | `false` | Lasă `false`. v1 tranzacționează doar pe DEMO |

4. Apasă **OK**. În colțul din dreapta-sus al graficului trebuie să apară o „căciulă” albastră/zâmbet (EA activ). În stânga-sus, EA-ul afișează starea: `CONECTAT` / `DECONECTAT`, ultimul sync, simbolurile active.

## 6. Verificare

- Tab-ul **Experts** (jos, în *Toolbox*, Ctrl+T) trebuie să arate:
  `AIBridgeEA v1.00 pornit...` și apoi `Conectat la server http://127.0.0.1:8787 ...`.
- Tab-ul **Journal** arată erorile terminalului (de exemplu WebRequest blocat).
- În pagina web a botului, starea „Bridge” devine verde, iar sursa de date devine `MT5_FEED`.
- Simbolurile din `ACTIVE_SYMBOLS` sunt adăugate automat în *Market Watch*.

## 7. Depanare

| Simptom (tab Experts/Journal) | Cauză | Soluție |
|---|---|---|
| `WebRequest interzis (eroare 4014)` / `Add URL to Tools > Options > Expert Advisors > Allow WebRequest` | URL-ul nu e în lista permisă | Adaugă `http://127.0.0.1:8787` (și eventual `http://localhost:8787`) în Tools > Options > Expert Advisors, apoi reatașează EA-ul |
| `WebRequest a esuat (eroare 5200/5201/5202/5203)` | Serverul nu rulează, port greșit sau URL invalid | Pornește serverul, verifică `http://127.0.0.1:8787` în browser, verifică `InpServerUrl` (fără `/` la final) |
| `Bad bridge key (HTTP 401)` | `InpBridgeKey` diferă de `MT5_BRIDGE_KEY` | Copiază exact cheia din `.env` în parametrii EA (F7 nu e necesar; doar reatașează / modifică Inputs) |
| `HTTP 403` | `MT5_BRIDGE_KEY` este gol în `.env` | Completează `MT5_BRIDGE_KEY` în `.env` și repornește serverul |
| EA-ul nu apare în Navigator | Nu a fost compilat | Deschide în MetaEditor și apasă F7; Refresh în Navigator |
| Erori de compilare | Fișier copiat incomplet / versiune MT5 veche | Actualizează MetaTrader 5 (Help > Check for updates) și recompilează; trimite-ne textul exact al erorii |
| `Butonul Algo Trading este oprit` la comenzi | Algo Trading dezactivat | Apasă butonul **Algo Trading** (verde) și bifează *Allow Algo Trading* în proprietățile EA |
| `Brokerul nu permite tranzactionare prin EA` | Contul nu permite EA | Folosește alt cont demo sau contactează brokerul |
| `EA refuza OPEN: contul nu este DEMO (v1)` | Ești logat pe cont real | Loghează-te pe contul demo |
| `simbolul 'EURUSD' nu exista la acest broker` | Brokerul folosește alt nume (ex. `EURUSD.a`, `EURUSDm`) | Pune numele exact din Market Watch în `ACTIVE_SYMBOLS` în `.env` și repornește serverul |
| Ordin respins: `Unsupported filling mode` | Mod de umplere nesuportat | EA-ul alege automat FOK > IOC > RETURN; raportează-ne simbolul și brokerul |
| Ordin respins: `Market closed` | Piața e închisă (weekend/sărbătoare) | Așteaptă deschiderea pieței |
| Ordin respins: `Invalid stops` | SL/TP prea aproape de preț | Normal în condiții de spread mare; serverul verifică `stops_level` |
| Pagina web arată `DECONECTAT` deși EA rulează | Ceas/oră greșit sau EA blocat | Verifică tab-ul Experts; scoate și reatașează EA-ul |
| Mesajul `WebRequest nu functioneaza in Strategy Tester` | EA rulat în tester | Atașează-l pe un grafic normal |

## 8. Note de siguranță

- EA-ul nu tranzacționează singur. Fără server pornit nu face nimic.
- Fiecare comandă are un `cmd_id` unic; EA-ul ține minte ultimele 500 și nu execută niciodată aceeași comandă de două ori.
- Pozițiile deschise manual (alt magic) sunt doar raportate, nu sunt modificate sau închise.
- Închiderea parțială este suportată doar pe conturile de tip *hedging* (cel mai frecvent tip de cont demo). Pe *netting*, închiderea completă funcționează normal.
- Nu pune niciodată cheia `MT5_BRIDGE_KEY` în chat-uri sau în fișiere publice.

## 9. Actualizare la v1.10

Ce aduce v1.10:
- **Calendar economic**: EA-ul citește calendarul economic din MT5 (evenimente de importanță medie și mare, următoarele 24 h) și îl trimite serverului la pornire și apoi la fiecare 10 minute. Pe grafic apare rândul `Calendar economic: N evenimente`.
- **Protecție la execuție**: un ordin OPEN care a stat prea mult pe drum (peste 20 s) sau al cărui preț s-a mutat prea mult față de prețul de la momentul semnalului nu mai este trimis la broker (rezultat `TIMEOUT (expired)` sau `PRICE_CHANGED (ref drift)`).
- **Detectare instanțe duble**: fiecare pornire a EA-ului are un identificator; serverul avertizează dacă două EA-uri sincronizează în același timp.

Pași:
1. Înlocuiește fișierul `MQL5/Experts/AIBridgeEA.mq5` cu versiunea nouă din `mt5/` (File > Open Data Folder > MQL5 > Experts).
2. Deschide-l în MetaEditor și apasă **F7** (Compile). Trebuie să apară `0 errors`.
3. Repornește EA-ul pe grafic: scoate-l (click dreapta pe grafic > Expert list > Remove) și atașează-l din nou din Navigator. Parametrii (Inputs) rămân aceiași.
4. Verifică în tab-ul **Experts**: `AIBridgeEA v1.10 pornit (instanta ...)`. În pagina web, versiunea EA devine `1.10`.
5. Calendarul economic MT5 funcționează doar cu terminalul **conectat** la server-ul brokerului. Dacă apare `ATENTIE: calendarul economic MT5 nu este disponibil`, verifică conexiunea (colțul dreapta-jos) — EA-ul reîncearcă automat la fiecare minut.

Notă: serverul funcționează și cu EA v1.00 (fără calendar și fără verificările noi la OPEN), dar se recomandă actualizarea.

## 10. Actualizare la v1.11

Ce aduce v1.11:
- **Arhivă de istoric pentru backtest**: serverul cere EA-ului, pe rând, loturi de câte cel mult 5000 de bare închise mai vechi (linia `HISTX`), până acoperă ultimele `ARCHIVE_DAYS` zile (implicit 120) pentru fiecare simbol activ pe timeframe-ul semnalelor, pe cel superior și pe M15. Barele se salvează în baza de date a serverului (`bars_archive`) și sunt folosite pentru backtest și pentru evaluarea semnalelor.
- Cererile de arhivă nu întârzie niciodată tranzacționarea: serverul nu trimite `HISTX` cât timp are o comandă OPEN/MODIFY/CLOSE în curs, iar EA-ul citește istoricul **după** ce a executat comenzile din răspuns și îl trimite la sincronizarea următoare (≈0,5 MB per lot).
- Pe grafic apare rândul `Arhiva istoric: N loturi trimise`.

Pași:
1. Înlocuiește fișierul `MQL5/Experts/AIBridgeEA.mq5` cu versiunea nouă din `mt5/`.
2. Deschide-l în MetaEditor și apasă **F7** (Compile) — trebuie să apară `0 errors`.
3. Scoate EA-ul de pe grafic și atașează-l din nou (parametrii rămân aceiași).
4. Verifică în tab-ul **Experts**: `AIBridgeEA v1.11 pornit (instanta ...)`. În pagina web, versiunea EA devine `1.11`.
5. Prima umplere a arhivei durează câteva minute (MT5 poate descărca istoricul de la broker). Dacă brokerul nu are încă seria pregătită, serverul reîncearcă automat (după 30 s, 60 s, ... maxim 10 min) — nu e nevoie de nicio acțiune.
6. Pentru istoric lung pe M5 (120 de zile ≈ 35.000 de bare), setarea **Tools > Options > Charts > Max bars in chart** trebuie să fie cel puțin `100000` (valoarea implicită). Dacă e mai mică, EA-ul afișează `Arhiva: limita 'Max bars in chart' ... atinsa` și arhiva se oprește acolo.

Notă: serverul funcționează în continuare cu EA v1.10 / v1.00 (fără arhivă — acestor versiuni nu li se trimite `HISTX`). Arhiva se poate opri din `.env` cu `ARCHIVE_ENABLED=false`.

## 11. Actualizare la v1.12

Ce aduce v1.12:
- **Ora de vară/iarnă a brokerului în arhivă**: EA-ul trimite și offset-ul curent al serverului (`srv_offset`, `srv_off`). Serverul reconvertește fiecare bară din arhivă cu offset-ul valabil la data barei (regula din `MT5_SERVER_TZ`, implicit `auto`: EET = schimbarea orei în UE, NY = închiderea New York GMT+2/+3). Fără asta, barele mai vechi de ultima schimbare a orei erau decalate cu 1 oră.
- **Arhiva nu mai blochează sincronizarea**: când istoricul cerut există deja în terminal, EA-ul îl copiază doar local (fără așteptarea descărcării de la broker); descărcarea pornește doar când în terminal nu există nimic mai vechi.

Pași: la fel ca la v1.11 (înlocuiește fișierul, **F7**, reatașează EA-ul; în pagina web versiunea devine `1.12`).
Dacă brokerul nu schimbă deloc ora (offset fix tot anul), pune `MT5_SERVER_TZ=fixed` în `.env`.
Barele arhivate cu v1.11 înainte de ultima schimbare a orei pot rămâne decalate cu 1 oră — pentru un backtest exact pe acea perioadă, golește arhiva perechii și las-o să se descarce din nou.
