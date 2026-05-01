from services.rag_reliable_feeder import (
    _compute_symbol_delta,
    _normalize_launchpool_rows,
    _onboard_to_utc_string,
    _timestamp_to_utc_string,
)


def test_compute_symbol_delta():
    added, removed = _compute_symbol_delta(
        previous_symbols=["BTCUSDT", "ETHUSDT", "XRPUSDT"],
        current_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    )
    assert added == ["SOLUSDT"]
    assert removed == ["XRPUSDT"]


def test_onboard_to_utc_string():
    # 2024-01-01 00:00:00 UTC
    value = _onboard_to_utc_string(1704067200000)
    assert value == "2024-01-01 00:00 UTC"


def test_timestamp_to_utc_string():
    value = _timestamp_to_utc_string(1704067200000)
    assert value == "2024-01-01 00:00 UTC"


def test_normalize_launchpool_rows_dedupes_symbol_trade_time():
    tracking = [
        {
            "rebateCoin": "AAA",
            "projectName": "Project AAA",
            "projectId": "AAA_USDT",
            "coinTradeTime": "1704067200000",
            "status": "ONGOING",
            "coinTrading": False,
        }
    ]
    completed = [
        {
            "rebateCoin": "AAA",
            "projectName": "Project AAA Duplicate",
            "projectId": "AAA_BNB",
            "coinTradeTime": "1704067200000",
            "status": "REDEEMED",
            "coinTrading": True,
        },
        {
            "rebateCoin": "BBB",
            "projectName": "Project BBB",
            "projectId": "BBB_USDT",
            "coinTradeTime": "1704153600000",
            "status": "REDEEMED",
            "coinTrading": True,
        },
    ]
    rows = _normalize_launchpool_rows(tracking, completed)
    assert [row["symbol"] for row in rows] == ["BBB", "AAA"]
    assert rows[0]["trade_time_utc"] == "2024-01-02 00:00 UTC"
    assert rows[1]["trade_time_utc"] == "2024-01-01 00:00 UTC"
