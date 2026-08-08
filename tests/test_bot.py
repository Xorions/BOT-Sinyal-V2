"""Test evaluasi sinyal via kline sejak-sesi (`bot._range_since`) — tanpa network."""

from datetime import datetime

import bot
from data import binance
from evaluation import WIB


class TestRangeSince:
    def test_uses_klines_since_session(self, monkeypatch):
        captured = {}

        def fake_since(symbol, interval, since, limit=1000):
            captured["symbol"] = symbol
            captured["interval"] = interval
            captured["since"] = since
            return [
                {"high": 100.0, "low": 95.0, "close": 97.0},
                {"high": 110.0, "low": 96.0, "close": 105.0},
                {"high": 108.0, "low": 99.0, "close": 99.0},
            ]

        monkeypatch.setattr(binance, "get_klines_since", fake_since)
        since = datetime(2026, 8, 6, 13, 30, tzinfo=WIB)
        high, low, current = bot._range_since("BTCUSDT", since)
        assert (high, low, current) == (110.0, 95.0, 99.0)
        assert captured["symbol"] == "BTCUSDT"
        assert captured["interval"] == binance.INTERVAL_M15
        assert captured["since"] == since

    def test_falls_back_to_ticker_when_since_none(self, monkeypatch):
        monkeypatch.setattr(binance, "get_ticker_24h", lambda symbol: {"high_24h": 1.2, "low_24h": 1.0, "price": 1.1})
        assert bot._range_since("XRPUSDT", None) == (1.2, 1.0, 1.1)

    def test_falls_back_to_ticker_when_klines_empty(self, monkeypatch):
        monkeypatch.setattr(binance, "get_klines_since", lambda *a, **k: [])
        monkeypatch.setattr(binance, "get_ticker_24h", lambda symbol: {"high_24h": 2.0, "low_24h": 1.8, "price": 1.9})
        since = datetime(2026, 8, 6, 13, 30, tzinfo=WIB)
        assert bot._range_since("BTCUSDT", since) == (2.0, 1.8, 1.9)

    def test_falls_back_to_ticker_when_klines_fails(self, monkeypatch):
        from data._client import DataSourceError

        def boom(*a, **k):
            raise DataSourceError("koneksi gagal")

        monkeypatch.setattr(binance, "get_klines_since", boom)
        monkeypatch.setattr(binance, "get_ticker_24h", lambda symbol: {"high_24h": 3.0, "low_24h": 2.9, "price": 2.95})
        since = datetime(2026, 8, 6, 13, 30, tzinfo=WIB)
        assert bot._range_since("BTCUSDT", since) == (3.0, 2.9, 2.95)

    def test_none_when_ticker_also_fails(self, monkeypatch):
        monkeypatch.setattr(binance, "get_klines_since", lambda *a, **k: [])
        monkeypatch.setattr(binance, "get_ticker_24h", lambda symbol: None)
        assert bot._range_since("BTCUSDT", None) is None
