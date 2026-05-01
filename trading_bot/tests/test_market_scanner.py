from api.routes.market_routes import _scanner_from_trade_decision


def test_scanner_from_trade_decision_converts_quantity_usd():
    parsed = {
        "action": "BUY",
        "symbol": "xrpusdt",
        "quantity_usd": 42.5,
        "confidence": 0.62,
        "reason": "Momentum setup",
    }

    out = _scanner_from_trade_decision(parsed, total_budget=500.0)
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["symbol"] == "XRPUSDT"
    assert out[0]["suggested_allocation_usd"] == 42.5
    assert out[0]["confidence"] == 0.62


def test_scanner_from_trade_decision_uses_fallback_allocation():
    parsed = {"action": "BUY", "symbol": "BTCUSDT", "quantity_usd": 0}
    out = _scanner_from_trade_decision(parsed, total_budget=500.0)
    assert out[0]["suggested_allocation_usd"] == 100.0
