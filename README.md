# Bot de scalping pe aur cu Claude (MT5)

Un singur mod de lucru: pe contul MetaTrader 5 conectat prin EA-ul `AIBridgeEA`, la fiecare bară M1 închisă Claude
primește toată starea (cont, poziții, grafice M1/M5/M15/H1, modele de price action, ultimele tranzacții) și alege
direcția. Fiecare intrare primește la broker un TP fix (+1,5 USD de preț) și un SL fix (4 USD de preț).

```
MT5 (cont demo) ──EA AIBridgeEA──HTTP 127.0.0.1──▶ Server Python (FastAPI)
                                                    ├─ app/basket.py   — coșul Claude: decizii, intrări armate, limite dure
                                                    ├─ app/ai_claude.py — apelul la Claude (prompt + schema JSON)
                                                    ├─ app/risk.py      — Risk Engine (marjă, spread, lot)
                                                    ├─ app/executor.py  — comenzi către EA, reconciliere
                                                    └─ Dashboard http://127.0.0.1:8787 (Prezentare, Poziții & Ordine)
```

> Nu există garanție de profit; tranzacționarea implică risc de pierdere.

## Pornire

1. Completează `.env` după `.env.example` (`ANTHROPIC_API_KEY`, `ADMIN_TOKEN`, `MT5_BRIDGE_KEY`).
2. Instalează dependențele: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
3. Pornește serverul: `./run.sh`.
4. În MT5 atașează `mt5/AIBridgeEA.mq5` pe un grafic XAUUSD cu aceeași `InpBridgeKey`.
5. Deschide http://127.0.0.1:8787, pune tokenul de administrare, apoi **Pornește botul** pe pagina Poziții & Ordine.

Teste: `.venv/bin/python -m pytest -q`.

## Reguli impuse în cod (indiferent ce răspunde Claude)

- limita de pierdere a grupului închide toate pozițiile;
- lotul maxim, numărul maxim de poziții și marja liberă a brokerului;
- CLOSE pe minus refuzat în primele 10 minute ale unei poziții;
- nicio reintrare pe aceeași parte în zona în care ultimele 2 tranzacții au pierdut (90 de minute);
- spread anormal și rollover-ul blochează intrările.
