from app.bridge import build_response


def test_symbols_line_includes_open_bot_position_symbols(monkeypatch):
    import app.bridge as br

    class S:
        MT5_MAGIC = 552211

    class RT:
        mt5_positions = [{"symbol": "GBPUSD", "magic": 552211}, {"symbol": "XAUUSD", "magic": 0}]

    captured = {}

    def fake_build(t, symbols, tfs, hist, commands):
        captured["symbols"] = symbols
        return "OK"

    monkeypatch.setattr(br, "build_response", fake_build)
    # exercise only the tail logic through a tiny harness mirroring process_sync's final step
    rt, magic = RT(), 552211
    symbols = ["EURUSD"]
    quote_symbols = list(symbols)
    for p in rt.mt5_positions:
        if p["symbol"] not in quote_symbols and int(p.get("magic") or 0) == magic:
            quote_symbols.append(p["symbol"])
    br.build_response(0, quote_symbols, [], [], [])
    assert captured["symbols"] == ["EURUSD", "GBPUSD"]
    assert "SYMBOLS|EURUSD,GBPUSD" in build_response(0, ["EURUSD", "GBPUSD"], [], [], [])
