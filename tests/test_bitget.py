"""Test data feeder Bitget V2 (klines/ticker/funding/L-S) — tanpa network.

Semua fetch di-*mock* di `data.bitget.http_get_json`; test memverifikasi bahwa
modul memanggil endpoint Bitget V2 yang benar (spot vs futures), mengirim
`granularity`/`productType`/symbol yang benar, dan memetakan respons Bitget
ke kontrak output kline lama ({open, high, low, close, volume, ts} dalam ms,
urutan kronologis) yang dipakai indikator / Kompas / Trigger M15 / evaluator.
"""

import pytest

from data import bitget
from data._client import DataSourceError


def _candle_row(ts: int, o=100.0, h=110.0, l=90.0, c=105.0, v=12.5, qv=1312.5) -> list:
    return [str(ts), f"{o}", f"{h}", f"{l}", f"{c}", f"{v}", f"{qv}", f"{qv}"]


def _envelope(rows, code="00000", msg="success") -> dict:
    return {"code": code, "msg": msg, "requestTime": 1692287544738, "data": rows}


class TestSymbolMapping:
    def test_to_spot_symbol_strips_futures_suffix(self):
        assert bitget.to_spot_symbol("BTCUSDT_UMCBL") == "BTCUSDT"
        assert bitget.to_spot_symbol("BTCUSDT") == "BTCUSDT"
        assert bitget.to_spot_symbol("btcusdt_umcbl") == "BTCUSDT"

    def test_to_futures_symbol_uses_plain_symbol(self):
        assert bitget.to_futures_symbol("BTCUSDT") == "BTCUSDT"
        assert bitget.to_futures_symbol("btcusdt") == "BTCUSDT"
        assert bitget.to_futures_symbol("BTCUSDT_UMCBL") == "BTCUSDT"
        assert bitget.to_futures_symbol("ETHUSDT") == "ETHUSDT"

    def test_granularity_spot(self):
        assert bitget._granularity(bitget.INTERVAL_M15, bitget.MARKET_SPOT) == "15min"
        assert bitget._granularity(bitget.INTERVAL_1H, bitget.MARKET_SPOT) == "1h"
        assert bitget._granularity(bitget.INTERVAL_4H, bitget.MARKET_SPOT) == "4h"
        assert bitget._granularity(bitget.INTERVAL_1D, bitget.MARKET_SPOT) == "1day"

    def test_granularity_futures(self):
        assert bitget._granularity(bitget.INTERVAL_M15, bitget.MARKET_FUTURES) == "15m"
        assert bitget._granularity(bitget.INTERVAL_1H, bitget.MARKET_FUTURES) == "1H"
        assert bitget._granularity(bitget.INTERVAL_4H, bitget.MARKET_FUTURES) == "4H"
        assert bitget._granularity(bitget.INTERVAL_1D, bitget.MARKET_FUTURES) == "1D"

    def test_unsupported_interval_raises(self):
        with pytest.raises(DataSourceError):
            bitget._granularity("99m", bitget.MARKET_SPOT)


class TestKlinesSpot:
    def test_parses_rows_into_ohlcv_contract_and_sorts_chronological(self, monkeypatch):
        captured = {}
        rows = [
            _candle_row(3000, o=110.0, h=115.0, l=105.0, c=112.0, v=9.5),
            _candle_row(1000, o=100.0, h=110.0, l=90.0, c=105.0, v=12.5),
            _candle_row(2000, o=105.0, h=112.0, l=98.0, c=108.0, v=8.0),
        ]

        def fake_get(url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return _envelope(rows)

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        out = bitget.get_klines("BTCUSDT", bitget.INTERVAL_M15, 200)

        assert captured["url"].endswith("/api/v2/spot/market/candles")
        assert captured["params"]["symbol"] == "BTCUSDT"
        assert captured["params"]["granularity"] == "15min"
        assert captured["params"]["limit"] == 200
        assert [c["ts"] for c in out] == [1000, 2000, 3000]
        assert out[0]["open"] == 100.0
        assert out[0]["high"] == 110.0
        assert out[0]["low"] == 90.0
        assert out[0]["close"] == 105.0
        assert out[0]["volume"] == 12.5

    def test_start_time_filters_open_time_below_start(self, monkeypatch):
        captured = {}
        rows = [
            _candle_row(3000),
            _candle_row(1000),
            _candle_row(2000),
        ]

        def fake_get(url, params=None, **kwargs):
            captured["params"] = params
            return _envelope(rows)

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        out = bitget._klines("BTCUSDT", bitget.INTERVAL_M15, 200, start_time=1500)
        assert captured["params"]["startTime"] == 1500
        # candle openTime < startTime (Fix #4) dibuang — kontrak evaluator terjaga.
        assert [c["ts"] for c in out] == [2000, 3000]

    def test_default_limit_from_mtf_limits(self, monkeypatch):
        captured = {}

        def fake_get(url, params=None, **kwargs):
            captured["params"] = params
            return _envelope([])

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        bitget.get_klines("BTCUSDT", bitget.INTERVAL_1D)
        assert captured["params"]["granularity"] == "1day"
        assert captured["params"]["limit"] == 60

    def test_error_code_raises_data_source_error(self, monkeypatch):

        def fake_get(url, params=None, **kwargs):
            return _envelope([], code="40001", msg="Invalid symbol")

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        with pytest.raises(DataSourceError):
            bitget.get_klines("NOTREAL", bitget.INTERVAL_1H, 10)

    def test_multi_returns_each_interval(self, monkeypatch):
        captured = []

        def fake_get(url, params=None, **kwargs):
            captured.append((url, dict(params)))
            return _envelope([_candle_row(1000)])

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        out = bitget.get_klines_multi("BTCUSDT")
        assert set(out.keys()) == set(bitget.MTF_LIMITS.keys())
        for interval, candles in out.items():
            assert candles and candles[0]["close"] == 105.0
        urls = {u for u, _ in captured}
        assert len(urls) == 1 and urls.pop().endswith("/api/v2/spot/market/candles")
        granularities = {p["granularity"] for _, p in captured}
        assert granularities == {"15min", "1h", "4h", "1day"}


class TestKlinesFutures:
    def test_routes_to_mix_endpoint_with_umcbl_symbol(self, monkeypatch):
        captured = {}

        def fake_get(url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return _envelope([_candle_row(1000)])

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        out = bitget.get_klines_futures("BTCUSDT", bitget.INTERVAL_1H, 50)

        assert captured["url"].endswith("/api/v2/mix/market/candles")
        assert captured["params"]["symbol"] == "BTCUSDT"
        assert captured["params"]["productType"] == bitget.FUTURES_PRODUCT_TYPE
        assert captured["params"]["granularity"] == "1H"
        assert out[0]["ts"] == 1000


class TestTicker:
    def test_ticker_24h_spot_parses_bitget_fields(self, monkeypatch):
        captured = {}
        tick = {
            "symbol": "BTCUSDT",
            "open": "65000.5",
            "high24h": "66000.0",
            "low24h": "64000.0",
            "lastPr": "65900.25",
            "quoteVolume": "123456789.123",
            "baseVolume": "1875.123",
            "usdtVolume": "123456789.123",
            "ts": "1700532903261",
            "change24h": "0.00321",
        }

        def fake_get(url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return _envelope([tick])

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        out = bitget.get_ticker_24h("BTCUSDT")

        assert captured["url"].endswith("/api/v2/spot/market/tickers")
        assert out["symbol"] == "BTCUSDT"
        assert out["price"] == 65900.25
        # Bitget `change24h` = pecahan; kontrak lama dalam persen.
        assert out["price_change_pct_24h"] == pytest.approx(0.321)
        assert out["quote_volume"] == pytest.approx(123456789.123)
        assert out["volume"] == pytest.approx(1875.123)
        assert out["high_24h"] == 66000.0
        assert out["low_24h"] == 64000.0

    def test_ticker_24h_futures_uses_plain_symbol(self, monkeypatch):
        captured = {}
        tick = {
            "symbol": "BTCUSDT",
            "lastPr": "65900.25",
            "high24h": "66000.0",
            "low24h": "64000.0",
            "change24h": "-0.00478",
            "baseVolume": "1875.123",
            "quoteVolume": "123456789.123",
            "ts": "1750332210370",
        }

        def fake_get(url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return _envelope([tick])

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        out = bitget.get_ticker_24h("BTCUSDT", market=bitget.MARKET_FUTURES)

        assert captured["url"].endswith("/api/v2/mix/market/tickers")
        assert captured["params"]["symbol"] == "BTCUSDT"
        assert captured["params"]["productType"] == bitget.FUTURES_PRODUCT_TYPE
        assert out["price_change_pct_24h"] == pytest.approx(-0.478)

    def test_ticker_24h_futures_filters_matching_row(self, monkeypatch):
        """Endpoint mix tickers mengabaikan param `symbol` (mengembalikan SEMUA
        ticker pasar) -> klien harus memfilter baris yang tepat."""
        captured = {}
        rows = [
            {"symbol": "ETHUSDT", "lastPr": "3500.0", "change24h": "0.01", "baseVolume": "1", "quoteVolume": "1", "high24h": "1", "low24h": "1", "ts": "1750332210370"},
            {"symbol": "BTCUSDT", "lastPr": "65900.25", "change24h": "-0.00478", "baseVolume": "1875.123", "quoteVolume": "123456789.123", "high24h": "66000.0", "low24h": "64000.0", "ts": "1750332210370"},
            {"symbol": "SOLUSDT", "lastPr": "150.0", "change24h": "-0.02", "baseVolume": "5", "quoteVolume": "5", "high24h": "1", "low24h": "1", "ts": "1750332210370"},
        ]

        def fake_get(url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return _envelope(rows)

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        out = bitget.get_ticker_24h("BTCUSDT", market=bitget.MARKET_FUTURES)

        assert captured["params"]["symbol"] == "BTCUSDT"
        assert out["symbol"] == "BTCUSDT"
        assert out["price"] == 65900.25
        assert out["quote_volume"] == pytest.approx(123456789.123)

    def test_ticker_24h_returns_none_when_symbol_missing(self, monkeypatch):
        rows = [{"symbol": "ETHUSDT", "lastPr": "3500.0", "change24h": "0.01", "baseVolume": "1", "quoteVolume": "1", "high24h": "1", "low24h": "1", "ts": "1"}]

        def fake_get(url, params=None, **kwargs):
            return _envelope(rows)

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        assert bitget.get_ticker_24h("BTCUSDT", market=bitget.MARKET_FUTURES) is None

    def test_all_tickers_filters_usdt_pairs(self, monkeypatch):
        captured = {}
        rows = [
            {"symbol": "BTCUSDT", "lastPr": "65900", "change24h": "0.001", "baseVolume": "10", "quoteVolume": "659000", "high24h": "1", "low24h": "1"},
            {"symbol": "ETHUSDT", "lastPr": "3500", "change24h": "-0.002", "baseVolume": "100", "quoteVolume": "350000", "high24h": "1", "low24h": "1"},
            {"symbol": "BTCUSDC", "lastPr": "65900", "change24h": "0.001", "baseVolume": "10", "quoteVolume": "659000", "high24h": "1", "low24h": "1"},
        ]

        def fake_get(url, params=None, **kwargs):
            captured["url"] = url
            return _envelope(rows)

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        out = bitget.get_all_tickers_24h()

        assert captured["url"].endswith("/api/v2/spot/market/tickers")
        assert set(out.keys()) == {"BTCUSDT", "ETHUSDT"}
        assert out["BTCUSDT"]["price"] == 65900.0
        assert out["ETHUSDT"]["price_change_pct_24h"] == pytest.approx(-0.2)
        assert "high_24h" not in out["BTCUSDT"]

    def test_ticker_invalid_row_raises(self, monkeypatch):

        def fake_get(url, params=None, **kwargs):
            return _envelope([{"symbol": "BTCUSDT", "lastPr": "x"}])

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        with pytest.raises(DataSourceError):
            bitget.get_ticker_24h("BTCUSDT")


class TestFuturesDerivatives:
    def test_funding_rate_parses_history_fund_rate(self, monkeypatch):
        captured = {}
        rows = [
            {"symbol": "BTCUSDT", "fundingRate": "-0.0003", "fundingTime": "1652396400000"},
            {"symbol": "BTCUSDT", "fundingRate": "0.0002", "fundingTime": "1652400000000"},
        ]

        def fake_get(url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return _envelope(rows)

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        out = bitget.get_funding_rate("BTCUSDT", limit=8)

        assert captured["url"].endswith("/api/v2/mix/market/history-fund-rate")
        assert captured["params"]["symbol"] == "BTCUSDT"
        assert captured["params"]["productType"] == bitget.FUTURES_PRODUCT_TYPE
        assert captured["params"]["pageSize"] == 8
        assert out == [-0.0003, 0.0002]

    def test_funding_rate_graceful_on_error(self, monkeypatch):

        def fake_get(url, params=None, **kwargs):
            raise DataSourceError("timeout")

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        assert bitget.get_funding_rate("BTCUSDT") == []

    def test_long_short_ratio_returns_newest(self, monkeypatch):
        captured = {}
        rows = [
            {"longAccountRatio": "0.40", "shortAccountRatio": "0.60", "longShortAccountRatio": "0.67", "ts": "1714020600000"},
            {"longAccountRatio": "0.60", "shortAccountRatio": "0.40", "longShortAccountRatio": "1.50", "ts": "1714021200000"},
        ]

        def fake_get(url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return _envelope(rows)

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        out = bitget.get_long_short_ratio("BTCUSDT")

        assert captured["url"].endswith("/api/v2/mix/market/account-long-short")
        assert captured["params"]["symbol"] == "BTCUSDT"
        assert captured["params"]["period"] == "5m"
        assert out == pytest.approx(1.5)

    def test_long_short_ratio_graceful_on_error(self, monkeypatch):

        def fake_get(url, params=None, **kwargs):
            return _envelope([], code="50000", msg="error")

        monkeypatch.setattr(bitget, "http_get_json", fake_get)
        assert bitget.get_long_short_ratio("BTCUSDT") is None