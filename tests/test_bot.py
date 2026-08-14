"""Test evaluasi sinyal via kline sejak-sesi (`bot._range_since`) + carry-over — tanpa network."""

from datetime import datetime, timedelta

import bot
from data import bitget
from evaluation import WIB, build_recap


class TestRangeSince:
    def test_uses_klines_since_session(self, monkeypatch):
        captured = {}
        candles = [
            {"high": 100.0, "low": 95.0, "close": 97.0},
            {"high": 110.0, "low": 96.0, "close": 105.0},
            {"high": 108.0, "low": 99.0, "close": 99.0},
        ]

        def fake_since(symbol, interval, since, limit=1000):
            captured["symbol"] = symbol
            captured["interval"] = interval
            captured["since"] = since
            return candles

        monkeypatch.setattr(bitget, "get_klines_since", fake_since)
        since = datetime(2026, 8, 6, 13, 30, tzinfo=WIB)
        assert bot._range_since("BTCUSDT", since) == candles
        assert captured["symbol"] == "BTCUSDT"
        assert captured["interval"] == bitget.INTERVAL_M15
        assert captured["since"] == since

    def test_falls_back_to_ticker_when_since_none(self, monkeypatch):
        monkeypatch.setattr(bitget, "get_ticker_24h", lambda symbol: {"high_24h": 1.2, "low_24h": 1.0, "price": 1.1})
        assert bot._range_since("XRPUSDT", None) == [{"high": 1.2, "low": 1.0, "close": 1.1}]

    def test_falls_back_to_ticker_when_klines_empty(self, monkeypatch):
        monkeypatch.setattr(bitget, "get_klines_since", lambda *a, **k: [])
        monkeypatch.setattr(bitget, "get_ticker_24h", lambda symbol: {"high_24h": 2.0, "low_24h": 1.8, "price": 1.9})
        since = datetime(2026, 8, 6, 13, 30, tzinfo=WIB)
        assert bot._range_since("BTCUSDT", since) == [{"high": 2.0, "low": 1.8, "close": 1.9}]

    def test_falls_back_to_ticker_when_klines_fails(self, monkeypatch):
        from data._client import DataSourceError

        def boom(*a, **k):
            raise DataSourceError("koneksi gagal")

        monkeypatch.setattr(bitget, "get_klines_since", boom)
        monkeypatch.setattr(bitget, "get_ticker_24h", lambda symbol: {"high_24h": 3.0, "low_24h": 2.9, "price": 2.95})
        since = datetime(2026, 8, 6, 13, 30, tzinfo=WIB)
        assert bot._range_since("BTCUSDT", since) == [{"high": 3.0, "low": 2.9, "close": 2.95}]

    def test_none_when_ticker_also_fails(self, monkeypatch):
        monkeypatch.setattr(bitget, "get_klines_since", lambda *a, **k: [])
        monkeypatch.setattr(bitget, "get_ticker_24h", lambda symbol: None)
        assert bot._range_since("BTCUSDT", None) is None


def _sig(action="BUY", entry=100.0, sl=90.0, tp1=110.0, tp2=120.0, symbol="BTCUSDT", base="BTC"):
    return {
        "symbol": symbol,
        "base": base,
        "action": action,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
    }


class TestCarryOver:
    """Carry-over = HANYA sinyal FLOATING (Fix duplikasi 14-Aug-2026).

    Sinyal FLOATING dari sesi terdahulu dibawa ke rekap sesi berikutnya dan
    dievaluasi ulang sampai menyentuh TP1/TP2/SL atau melebihi EVAL_MAX_HOURS.
    Begitu mencapai status final (TP1/TP2/SL/EXPIRED) LANGSUNG keluar dari
    antrean carry-over di sesi berikutnya (tidak diseret berulang kali), dan
    koin yang sama hanya tampil SEKALI (yang paling baru).
    """

    def test_floating_signal_from_previous_session_not_dropped(self):
        # RFXI FLOATING di sesi 13:30, LIT TP2 di sesi 19:00. Rekap di sesi
        # berikutnya harus TETAP mengevaluasi RFXI (carry-over) + menampilkan
        # umur sinyalnya, bukan menghapusnya dari antrean.
        history = {
            "2026-08-06 13:30": [_sig(symbol="RFXIUSDT", base="RFXI")],
            "2026-08-06 19:00": [_sig(entry=50.0, sl=45.0, tp1=55.0, tp2=60.0, symbol="LITUSDT", base="LIT")],
        }

        def fetch(pair, since=None):
            return {
                "RFXIUSDT": [{"high": 105.0, "low": 95.0, "close": 102.0}],  # FLOATING
                "LITUSDT": [{"high": 121.0, "low": 95.0, "close": 115.0}],   # TP2
            }[pair]

        recap = build_recap(history, fetch, now_key="2026-08-07 13:30")
        assert recap is not None
        assert "CARRY-OVER" in recap
        assert "#RFXI" in recap
        assert "FLOATING - 24 jam" in recap
        assert "Harga saat ini" in recap
        assert "#LIT" in recap and "TP2" in recap

    def test_carry_over_status_duration_format(self):
        # Umur sinyal dibulatkan per jam (sesi 13:30 -> 19:00 = 5,5 jam -> '5 jam').
        from evaluation import _age_str

        assert _age_str(timedelta(minutes=30)) == "30 menit"
        assert _age_str(timedelta(hours=5, minutes=30)) == "5 jam"
        assert _age_str(timedelta(hours=29)) == "29 jam"
        assert _age_str(timedelta(hours=49)) == "2 hari 1 jam"

    def test_carry_over_signal_resolved_to_tp1_in_next_session(self):
        # Sinyal yang tadinya FLOATING kini menyentuh TP1 di sesi berikutnya
        # -> status FINAL (TP1): LANGSUNG keluar dari antrean carry-over,
        # tidak ditampilkan lagi (Fix duplikasi & penumpukan).
        history = {
            "2026-08-06 13:30": [_sig(symbol="RFXIUSDT", base="RFXI")],
            "2026-08-06 19:00": [_sig(entry=50.0, sl=45.0, tp1=55.0, tp2=60.0, symbol="LITUSDT", base="LIT")],
        }

        def fetch(pair, since=None):
            return {
                "RFXIUSDT": [{"high": 111.0, "low": 95.0, "close": 110.0}],  # TP1
                "LITUSDT": [{"high": 121.0, "low": 95.0, "close": 115.0}],
            }[pair]

        recap = build_recap(history, fetch, now_key="2026-08-07 13:30")
        assert recap is not None
        assert "CARRY-OVER" not in recap
        assert "#RFXI" not in recap
        assert "#LIT" in recap and "TP2" in recap

    def test_carry_over_signal_resolved_to_sl_in_next_session(self):
        # Status FINAL (SL): keluar dari antrean carry-over.
        history = {
            "2026-08-06 13:30": [_sig(symbol="RFXIUSDT", base="RFXI")],
            "2026-08-06 19:00": [_sig(entry=50.0, sl=45.0, tp1=55.0, tp2=60.0, symbol="LITUSDT", base="LIT")],
        }

        def fetch(pair, since=None):
            return {
                "RFXIUSDT": [{"high": 100.0, "low": 89.5, "close": 95.0}],  # SL
                "LITUSDT": [{"high": 121.0, "low": 95.0, "close": 115.0}],
            }[pair]

        recap = build_recap(history, fetch, now_key="2026-08-07 13:30")
        assert recap is not None
        assert "CARRY-OVER" not in recap
        assert "#RFXI" not in recap
        assert "#LIT" in recap and "TP2" in recap

    def test_carry_over_signal_expired_after_eval_max_hours(self):
        # RFXI tak menyentuh TP/SL dan jendela EVAL_MAX_HOURS (24 jam) sudah
        # lewat -> status EXPIRED = FINAL: langsung keluar dari carry-over.
        history = {
            "2026-08-06 13:30": [_sig(symbol="RFXIUSDT", base="RFXI")],
            "2026-08-07 13:30": [_sig(entry=50.0, sl=45.0, tp1=55.0, tp2=60.0, symbol="LITUSDT", base="LIT")],
        }

        def fetch(pair, since=None):
            return {
                "RFXIUSDT": [{"high": 105.0, "low": 95.0, "close": 102.0}],  # FLOATING -> EXPIRED
                "LITUSDT": [{"high": 121.0, "low": 95.0, "close": 115.0}],
            }[pair]

        # now = 07 19:00 -> RFXI berumur 29,5 jam > EVAL_MAX_HOURS 24.
        recap = build_recap(history, fetch, now_key="2026-08-07 19:00")
        assert recap is not None
        assert "CARRY-OVER" not in recap
        assert "#RFXI" not in recap
        assert "#LIT" in recap and "TP2" in recap

    def test_carry_over_dropped_when_past_window_and_grace(self):
        # Sesi yang lewat jendela EVAL_MAX_HOURS + CARRYOVER_GRACE_HOURS tidak
        # ikut antrean carry-over lagi (status akhirnya sudah pernah ditampilkan).
        history = {
            "2026-08-05 13:30": [_sig(symbol="RFXIUSDT", base="RFXI")],
            "2026-08-08 13:30": [_sig(entry=50.0, sl=45.0, tp1=55.0, tp2=60.0, symbol="LITUSDT", base="LIT")],
        }

        def fetch(pair, since=None):
            return {
                "RFXIUSDT": [{"high": 105.0, "low": 95.0, "close": 102.0}],
                "LITUSDT": [{"high": 121.0, "low": 95.0, "close": 115.0}],
            }[pair]

        recap = build_recap(history, fetch, now_key="2026-08-08 19:00")
        assert recap is not None
        assert "CARRY-OVER" not in recap
        assert "#RFXI" not in recap
        assert "#LIT" in recap

    def test_floating_carry_over_kept_evaluating_until_expired(self):
        # Simulasi 3 sesi: RFXI floating -> floating lagi di sesi berikutnya
        # (tetap dibawa) -> akhirnya EXPIRED (status final) di sesi berikutnya.
        def fetch(pair, since=None):
            return [{"high": 105.0, "low": 95.0, "close": 102.0}]

        history = {
            "2026-08-05 13:30": [_sig(symbol="RFXIUSDT", base="RFXI")],
            "2026-08-05 19:00": [_sig(entry=50.0, sl=45.0, tp1=55.0, tp2=60.0, symbol="LITUSDT", base="LIT")],
        }

        recap_1 = build_recap(history, fetch, now_key="2026-08-06 13:30")  # RFXI 24 jam: FLOATING -> dibawa
        assert recap_1 is not None
        assert "CARRY-OVER" in recap_1
        assert "#RFXI BUY (FLOATING - 24 jam)" in recap_1

        recap_2 = build_recap(history, fetch, now_key="2026-08-06 19:00")  # RFXI 29,5 jam: EXPIRED -> keluar
        assert recap_2 is not None
        assert "CARRY-OVER" not in recap_2
        assert "#RFXI" not in recap_2

        recap_3 = build_recap(history, fetch, now_key="2026-08-08 19:00")  # lewat grace: dibuang
        assert recap_3 is not None
        assert "CARRY-OVER" not in recap_3
        assert "#RFXI" not in recap_3

    def test_carry_over_dedupe_same_symbol_keeps_newest(self):
        # Koin sama (RFXI) muncul di 2 sesi berbeda & keduanya FLOATING
        # -> carry-over hanya menampilkan 1 sinyal TERBARU (sesi paling akhir).
        history = {
            "2026-08-07 13:30": [_sig(entry=35.13, sl=34.10, tp1=106.0, tp2=107.0, symbol="RFXIUSDT", base="RFXI")],
            "2026-08-07 19:00": [_sig(entry=35.15, sl=34.12, tp1=106.0, tp2=107.0, symbol="RFXIUSDT", base="RFXI")],
            "2026-08-08 07:00": [_sig(entry=50.0, sl=45.0, tp1=110.0, tp2=120.0, symbol="LITUSDT", base="LIT")],
        }

        def fetch(pair, since=None):
            return [{"high": 104.0, "low": 96.0, "close": 101.0}]  # semua FLOATING

        recap = build_recap(history, fetch, now_key="2026-08-08 13:30")
        assert recap is not None
        carry = recap.split("CARRY-OVER")[1]
        assert carry.count("#RFXI") == 1
        assert "Entry $35.15" in carry   # sinyal terbaru (sesi 07 19:00)
        assert "Entry $35.13" not in carry

    def test_carry_over_excludes_symbol_already_in_primary_session(self):
        # Koin yang sudah tampil di sesi utama (primary) tidak boleh diduplikasi
        # ke seksi CARRY-OVER walau muncul lagi di sesi lebih lama.
        history = {
            "2026-08-07 19:00": [_sig(entry=35.13, sl=34.10, tp1=106.0, tp2=107.0, symbol="RFXIUSDT", base="RFXI")],
            "2026-08-08 07:00": [_sig(entry=35.16, sl=34.13, tp1=106.0, tp2=107.0, symbol="RFXIUSDT", base="RFXI")],
        }

        def fetch(pair, since=None):
            return [{"high": 104.0, "low": 96.0, "close": 101.0}]  # FLOATING

        recap = build_recap(history, fetch, now_key="2026-08-08 13:30")
        assert recap is not None
        assert "CARRY-OVER" not in recap
        assert recap.count("#RFXI") == 1
        assert "Entry $35.16" in recap  # hanya yang terbaru (sesi primary)
