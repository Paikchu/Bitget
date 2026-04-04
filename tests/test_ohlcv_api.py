from fastapi.testclient import TestClient

from bitget_bot import api


class _FakeBitgetExchange:
    def __init__(self, rows, now_ms=1_710_000_000_000):
        self.rows = rows
        self.now_ms = now_ms
        self.calls = []

    def load_markets(self):
        return {}

    def market(self, symbol):
        assert symbol == "BTC/USDT:USDT"
        return {"id": "BTCUSDT", "settle": "USDT"}

    def parse_timeframe(self, timeframe):
        assert timeframe == "15m"
        return 15 * 60

    def milliseconds(self):
        return self.now_ms

    def publicMixGetV2MixMarketHistoryCandles(self, params):
        self.calls.append(params)
        return {"data": self.rows}


def test_get_ohlcv_returns_paginated_history_page(monkeypatch):
    rows = [
        ["1710000000000", "100", "110", "90", "105", "10", "1000"],
        ["1710000900000", "105", "115", "95", "110", "12", "1200"],
    ]
    fake_exchange = _FakeBitgetExchange(rows, now_ms=1710003600000)
    client = TestClient(api.app)
    monkeypatch.setattr(api.ccxt, "bitget", lambda *_args, **_kwargs: fake_exchange)

    response = client.get(
        "/api/ohlcv",
        params={"symbol": "BTC/USDT:USDT", "timeframe": "15m", "limit": 500, "before_ts": 1710001800000},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "BTC/USDT:USDT"
    assert body["timeframe"] == "15m"
    assert body["page"]["requested_before_ts"] == 1710001800000
    assert body["page"]["returned"] == 2
    assert body["page"]["next_before_ts"] == 1710000000000
    assert body["page"]["has_more"] is False
    assert body["candles"] == [
        {"ts": 1710000000000, "open": 100.0, "high": 110.0, "low": 90.0, "close": 105.0, "volume": 10.0},
        {"ts": 1710000900000, "open": 105.0, "high": 115.0, "low": 95.0, "close": 110.0, "volume": 12.0},
    ]
    assert fake_exchange.calls == [
        {
            "symbol": "BTCUSDT",
            "productType": "USDT-FUTURES",
            "granularity": "15m",
            "limit": "200",
            "endTime": "1710001800000",
            "startTime": "1709821800000",
        }
    ]


def test_get_ohlcv_filters_out_unclosed_future_candle(monkeypatch):
    rows = [
        ["1710000000000", "100", "110", "90", "105", "10", "1000"],
        ["1710000900000", "105", "115", "95", "110", "12", "1200"],
    ]
    fake_exchange = _FakeBitgetExchange(rows, now_ms=1710001000000)
    client = TestClient(api.app)
    monkeypatch.setattr(api.ccxt, "bitget", lambda *_args, **_kwargs: fake_exchange)

    response = client.get("/api/ohlcv", params={"symbol": "BTC/USDT:USDT", "timeframe": "15m", "limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert [candle["ts"] for candle in body["candles"]] == [1710000000000]
    assert body["page"]["returned"] == 1
