"""Test evaluasi sinyal sesi sebelumnya + penyimpanan riwayat (tanpa network)."""

from datetime import datetime

from engine import Signal
from evaluation import (
    STATUS_FLOATING,
    STATUS_SL,
    STATUS_TP1,
    STATUS_TP2,
    WIB,
    add_signals_today,
    build_recap,
    evaluate_signal,
    load_history,
    previous_session_signals,
    save_history,
    today_str,
)


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


class TestEvaluateSignal:
    def test_buy_tp2(self):
        assert evaluate_signal(_sig(), high=121, low=95, current=115) == STATUS_TP2

    def test_buy_tp1(self):
        assert evaluate_signal(_sig(), high=111, low=95, current=105) == STATUS_TP1

    def test_buy_sl(self):
        assert evaluate_signal(_sig(), high=103, low=89, current=92) == STATUS_SL

    def test_buy_floating(self):
        assert evaluate_signal(_sig(), high=105, low=95, current=102) == STATUS_FLOATING

    def test_sell_tp2(self):
        assert evaluate_signal(_sig("SELL", 100, 110, 90, 80), high=105, low=79, current=88) == STATUS_TP2

    def test_sell_tp1(self):
        assert evaluate_signal(_sig("SELL", 100, 110, 90, 80), high=105, low=89, current=88) == STATUS_TP1

    def test_sell_sl(self):
        assert evaluate_signal(_sig("SELL", 100, 110, 90, 80), high=111, low=95, current=100) == STATUS_SL

    def test_sell_floating(self):
        assert evaluate_signal(_sig("SELL", 100, 110, 90, 80), high=105, low=95, current=97) == STATUS_FLOATING

    def test_buy_both_tp_and_sl_touched_conservative_sl(self):
        # high >= TP1 DAN low <= SL di jendela sama -> tidak tahu urutannya -> SL (konservatif).
        assert evaluate_signal(_sig(), high=112, low=88, current=100) == STATUS_SL

    def test_sell_both_tp_and_sl_touched_conservative_sl(self):
        # SELL: low <= TP1 DAN high >= SL di jendela sama -> SL (konservatif).
        assert evaluate_signal(_sig("SELL", 100, 110, 90, 80), high=112, low=88, current=100) == STATUS_SL

    def test_buy_tp2_with_sl_touched_still_sl(self):
        # TP2 tersentuh tapi SL juga -> tetap konservatif SL.
        assert evaluate_signal(_sig(), high=122, low=88, current=100) == STATUS_SL

    def test_buy_tp2_no_sl(self):
        assert evaluate_signal(_sig(), high=122, low=95, current=100) == STATUS_TP2


class TestHistory:
    def test_save_load_roundtrip(self, tmp_path):
        path = str(tmp_path / "history.json")
        history = {"2026-08-06 13:30": [_sig()]}
        save_history(history, path)
        assert load_history(path) == history

    def test_load_missing_returns_empty(self, tmp_path):
        assert load_history(str(tmp_path / "nope.json")) == {}

    def test_load_corrupt_returns_empty(self, tmp_path):
        path = str(tmp_path / "history.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{{{bukan json")
        assert load_history(path) == {}

    def test_add_signals_today_overwrites_same_session(self, tmp_path):
        path = str(tmp_path / "history.json")
        s1 = Signal("BTCUSDT", "BTC", 100.0, 1.0, 0.5, "BUY", 70, 100.0, 90.0, 110.0, 120.0)
        s2 = Signal("XRPUSDT", "XRP", 1.0, -2.0, -0.2, "SELL", 60, 1.0, 1.1, 0.9, 0.8)
        add_signals_today([s1], "today", path, session_key="2026-08-07 13:30")
        add_signals_today([s2], "today", path, session_key="2026-08-07 13:30")
        history = load_history(path)
        assert "2026-08-07 13:30" in history
        assert len(history["2026-08-07 13:30"]) == 1
        assert history["2026-08-07 13:30"][0]["symbol"] == "XRPUSDT"

    def test_add_signals_today_defaults_to_now(self, tmp_path):
        path = str(tmp_path / "history.json")
        s1 = Signal("BTCUSDT", "BTC", 100.0, 1.0, 0.5, "BUY", 70, 100.0, 90.0, 110.0, 120.0)
        add_signals_today([s1], "today", path)
        history = load_history(path)
        assert today_str() in " ".join(history.keys())

    def test_previous_session_signals(self):
        history = {
            "2026-08-05 19:00": [_sig()],
            "2026-08-06 13:30": [_sig()],
            "2026-08-06 19:00": [],
        }
        date, sigs = previous_session_signals(history, now_key="2026-08-07 13:30")
        assert date == "2026-08-06 19:00"
        assert sigs == []

    def test_previous_session_signals_none_same_session(self):
        history = {"2026-08-07 13:30": [_sig()]}
        assert previous_session_signals(history, now_key="2026-08-07 13:30") == (None, [])

    def test_mixed_old_date_and_session_keys_ordered(self):
        history = {"2026-08-06": [_sig()], "2026-08-07 13:30": [_sig()]}
        date, sigs = previous_session_signals(history, now_key="2026-08-07 19:00")
        assert date == "2026-08-07 13:30"
        assert sigs

    def test_add_signals_today_skips_neutral(self, tmp_path):
        path = str(tmp_path / "history.json")
        buy = Signal("BTCUSDT", "BTC", 100.0, 1.0, 0.5, "BUY", 70, 100.0, 90.0, 110.0, 120.0)
        neutral = Signal("XRPUSDT", "XRP", 1.0, 0.0, 0.0, "NEUTRAL", 40, 1.0, 0.97, 1.03, 1.06)
        add_signals_today([buy, neutral], "today", path, session_key="2026-08-08 13:30")
        history = load_history(path)
        stored = history["2026-08-08 13:30"]
        assert len(stored) == 1
        assert stored[0]["symbol"] == "BTCUSDT"
        assert all(s["action"] != "NEUTRAL" for s in stored)


class TestSessionSince:
    def test_parses_session_key_wib(self):
        from evaluation import _session_since

        since = _session_since("2026-08-07 13:30")
        assert since == datetime(2026, 8, 7, 13, 30, tzinfo=WIB)
        assert since.tzinfo is not None

    def test_parses_old_date_key_to_midnight(self):
        from evaluation import _session_since

        since = _session_since("2026-08-06")
        assert since == datetime(2026, 8, 6, 0, 0, tzinfo=WIB)

    def test_unknown_key_returns_none(self):
        from evaluation import _session_since

        assert _session_since("garbage") is None
        assert _session_since(None) is None
        assert _session_since("") is None


class TestBuildRecap:
    HISTORY = {
        "2026-08-06 13:30": [
            _sig("BUY", 100.0, 90.0, 110.0, 120.0),
            _sig("BUY", 50.0, 45.0, 55.0, 60.0, symbol="LITUSDT", base="LIT"),
            _sig("SELL", 1.0, 1.1, 0.9, 0.8, symbol="XRPUSDT", base="XRP"),
        ]
    }

    def _fetch(self, pair, since=None):
        assert since is not None  # build_recap wajib meneruskan waktu sesi sinyal
        # fetch_fn kini mengembalikan list candle M15 kronologis (Fix R4).
        return {
            "BTCUSDT": [{"high": 121.0, "low": 95.0, "close": 115.0}],  # TP2 (high >= tp2)
            "LITUSDT": [{"high": 54.0, "low": 44.0, "close": 52.0}],  # SL (low <= sl)
            "XRPUSDT": [{"high": 1.05, "low": 0.89, "close": 0.98}],  # TP1 (low <= tp1)
        }[pair]

    def test_recap_winrate_and_statuses(self):
        recap = build_recap(self.HISTORY, self._fetch, now_key="2026-08-07 13:30")
        assert recap is not None
        assert "EVALUASI SINYAL SESI SEBELUMNYA" in recap
        assert "06 Aug 2026 13:30" in recap
        assert "🏆 Win rate: <b>67%</b> (2/3) | 💰 TP1: 1 | 🎯 TP2: 1 | 🛡️ SL: 1 | ⏳ Floating: 0" in recap
        assert "SEKSI 1 — SESI UTAMA" in recap
        assert "🔑 Entry" in recap
        assert "📋 Hit" in recap
        assert "#BTC" in recap and "TP2" in recap
        assert "#LIT" in recap and "SL" in recap
        assert "#XRP" in recap and "TP1" in recap

    def test_recap_pnl_pct_and_separators(self):
        recap = build_recap(self.HISTORY, self._fetch, now_key="2026-08-07 13:30")
        assert recap is not None
        assert recap.count("━━━━━━━━━━━━") == 2
        assert "📋 Hit TP2 di $120.00 (+20.00%)" in recap
        assert "📋 Hit SL di $45.00 (-10.00%)" in recap
        assert "📋 Hit TP1 di $0.900000 (+10.00%)" in recap
        lines = recap.splitlines()
        assert lines[1] == "🏆 Win rate: <b>67%</b> (2/3) | 💰 TP1: 1 | 🎯 TP2: 1 | 🛡️ SL: 1 | ⏳ Floating: 0"
        assert lines[2] == "━━━━━━━━━━━━"
        assert lines[3] == "📋 <b>SEKSI 1 — SESI UTAMA</b>"
        assert lines[7] == "───"
        assert lines[11] == "───"
        assert lines[-1] == "━━━━━━━━━━━━"

    def test_recap_floating_includes_pnl(self):
        history = {"2026-08-06 13:30": [_sig("BUY", 100.0, 90.0, 110.0, 120.0)]}
        recap = build_recap(history, lambda pair, since=None: [{"high": 105.0, "low": 95.0, "close": 102.0}], now_key="2026-08-07 13:30")
        assert recap is not None
        assert "📋 Harga saat ini $102.00 (+2.00%)" in recap

    def test_recap_none_when_no_previous(self):
        assert build_recap({}, self._fetch, now_key="2026-08-07 13:30") is None

    def test_recap_none_when_all_fetch_fail(self):
        history = {"2026-08-06 13:30": [_sig()]}
        assert build_recap(history, lambda pair, since=None: None, now_key="2026-08-07 13:30") is None

    def test_recap_falls_back_to_older_session_when_recent_fetch_fails(self):
        # Fix #5: sesi terbaru gagal dievaluasi -> recap dari sesi lebih lama.
        history = {
            "2026-08-05 13:30": [_sig("BUY", 100.0, 90.0, 110.0, 120.0)],
            "2026-08-06 13:30": [_sig("BUY", 50.0, 45.0, 55.0, 60.0, symbol="LITUSDT", base="LIT")],
        }

        def fetch(pair, since=None):
            if since == datetime(2026, 8, 6, 13, 30, tzinfo=WIB):
                return None  # sesi terbaru gagal (harga tak terambil)
            return {
                "BTCUSDT": [{"high": 121.0, "low": 95.0, "close": 115.0}],
                "LITUSDT": [{"high": 54.0, "low": 44.0, "close": 52.0}],
            }[pair]

        recap = build_recap(history, fetch, now_key="2026-08-07 13:30")
        assert recap is not None
        assert "05 Aug 2026 13:30" in recap
        assert "#BTC" in recap

    def test_recap_falls_back_to_older_session_when_recent_empty(self):
        # Fix #5: sesi terbaru tanpa sinyal -> recap dari sesi lebih lama.
        history = {
            "2026-08-05 13:30": [_sig("BUY", 100.0, 90.0, 110.0, 120.0)],
            "2026-08-06 19:00": [],
        }
        recap = build_recap(history, self._fetch, now_key="2026-08-07 13:30")
        assert recap is not None
        assert "05 Aug 2026 13:30" in recap

    def test_recap_passes_session_time_to_fetch(self):
        captured = {}

        def fetch(pair, since=None):
            captured["since"] = since
            return [{"high": 105.0, "low": 95.0, "close": 100.0}]

        history = {"2026-08-06 13:30": [_sig()]}
        build_recap(history, fetch, now_key="2026-08-07 13:30")
        assert captured["since"] == datetime(2026, 8, 6, 13, 30, tzinfo=WIB)


class TestSessionSeparation:
    """Pemisahan sesi evaluasi vs sinyal baru (Fix 15-Aug-2026).

    Rekap sesi berjalan (mis. 19:06) HANYA mengevaluasi sinyal dari sesi
    SEBELUMNYA (mis. 13:35) + antrean Carry-Over FLOATING. Sinyal yang BARU
    SAJA dibuat pada sesi 19:06 tersebut TIDAK dievaluasi prematur (belum
    punya umur/progress harga) — baru dievaluasi pada sesi berikutnya.
    """

    def _floating_fetch(self, pair, since=None):
        return [{"high": 105.0, "low": 95.0, "close": 102.0}]

    def test_fresh_session_t_signals_not_evaluated_prematurely(self):
        # Sinyal sesi 19:06 baru berumur 4 menit saat rekap 19:10 -> dilewati;
        # rekap hanya mengevaluasi sesi sebelumnya 13:35.
        history = {
            "2026-08-15 13:35": [_sig(symbol="RFXIUSDT", base="RFXI")],
            "2026-08-15 19:06": [_sig(symbol="SOLUSDT", base="SOL")],
        }
        recap = build_recap(history, self._floating_fetch, now_key="2026-08-15 19:10")
        assert recap is not None
        assert "15 Aug 2026 13:35" in recap     # sesi sebelumnya = primary
        assert "15 Aug 2026 19:06" not in recap
        assert "#SOL" not in recap              # sinyal baru tidak dievaluasi
        assert "#RFXI" in recap                 # sinyal T-1 tetap dievaluasi

    def test_fresh_session_t_preserved_in_carryover_flow(self):
        # Sesi T-1 (13:35) floating dibawa ke Carry-Over meski ada sesi baru T
        # (19:06) yang tidak boleh dievaluasi.
        history = {
            "2026-08-15 13:35": [
                _sig(symbol="RFXIUSDT", base="RFXI"),
                _sig(symbol="RCDNSUSDT", base="RCDNS"),
            ],
            "2026-08-15 19:06": [_sig(symbol="SOLUSDT", base="SOL")],
        }
        recap = build_recap(history, self._floating_fetch, now_key="2026-08-15 19:10")
        assert recap is not None
        assert "CARRY-OVER" in recap
        assert "#RFXI" in recap and "#RCDNS" in recap
        assert "#SOL" not in recap

    def test_only_fresh_session_returns_none(self):
        history = {"2026-08-15 19:06": [_sig(symbol="SOLUSDT", base="SOL")]}
        assert build_recap(history, self._floating_fetch, now_key="2026-08-15 19:10") is None

    def test_current_session_key_itself_never_evaluated(self):
        # Kunci sesi yang SAMA dengan sesi berjalan (19:06) bukan "sesi
        # sebelumnya" — sinyalnya tidak ikut dievaluasi rekap ini.
        history = {
            "2026-08-15 13:35": [_sig(symbol="RFXIUSDT", base="RFXI")],
            "2026-08-15 19:06": [_sig(symbol="SOLUSDT", base="SOL")],
        }
        recap = build_recap(history, self._floating_fetch, now_key="2026-08-15 19:06")
        assert recap is not None
        assert "13:35" in recap
        assert "#SOL" not in recap

    def test_mature_previous_session_still_evaluated(self):
        # Sesi T-1 yang sudah berumur (5,5 jam) tetap dievaluasi di sesi T.
        history = {"2026-08-15 13:35": [_sig(symbol="RFXIUSDT", base="RFXI")]}
        recap = build_recap(history, self._floating_fetch, now_key="2026-08-15 19:06")
        assert recap is not None
        assert "15 Aug 2026 13:35" in recap
        assert "#RFXI" in recap

    def test_previous_session_signals_skips_fresh_session(self):
        from evaluation import previous_session_signals

        history = {
            "2026-08-15 13:35": [_sig(symbol="RFXIUSDT", base="RFXI")],
            "2026-08-15 19:06": [_sig(symbol="SOLUSDT", base="SOL")],
        }
        date, sigs = previous_session_signals(history, now_key="2026-08-15 19:10")
        assert date == "2026-08-15 13:35"
        assert sigs[0]["base"] == "RFXI"


class TestCarryOverContinuity:
    """Continuity sinyal FLOATING (Fix 15-Aug-2026).

    Semua sinyal FLOATING dari sesi sebelumnya (T-1) otomatis masuk daftar
    Carry-Over untuk sesi berjalan (T) selama belum tersentuh TP1/TP2/SL/
    EXPIRED — posisi aktif tidak pernah hilang dari tracking antar sesi.
    """

    def _floating_fetch(self, pair, since=None):
        return [{"high": 105.0, "low": 95.0, "close": 102.0}]

    def test_previous_session_floating_auto_enters_carryover(self):
        # RFXI, RCDNS, RMCK, RIBIT, RNXPI dari sesi 13:35 semuanya FLOATING
        # -> otomatis masuk Carry-Over untuk sesi 19:06 (masing-masing sekali).
        history = {
            "2026-08-15 13:35": [
                _sig(symbol="RFXIUSDT", base="RFXI"),
                _sig(symbol="RCDNSUSDT", base="RCDNS"),
                _sig(symbol="RMCKUSDT", base="RMCK"),
                _sig(symbol="RIBITUSDT", base="RIBIT"),
                _sig(symbol="RNXPIUSDT", base="RNXPI"),
            ]
        }
        recap = build_recap(history, self._floating_fetch, now_key="2026-08-15 19:06")
        assert recap is not None
        assert "CARRY-OVER" in recap
        assert "🧾 5 sinyal ⏳ FLOATING dibawa dari sesi sebelumnya" in recap
        for base in ("RFXI", "RCDNS", "RMCK", "RIBIT", "RNXPI"):
            assert recap.count(f"#{base}") == 1  # tidak duplikat di seksi utama

    def test_resolved_signals_exit_queue_only_floating_carried(self):
        # Yang sudah menyentuh SL (status final) TIDAK masuk Carry-Over;
        # hanya yang FLOATING yang dibawa (continuity).
        history = {
            "2026-08-15 13:35": [
                _sig(symbol="RFXIUSDT", base="RFXI"),
                _sig(symbol="LITUSDT", base="LIT", entry=50.0, sl=45.0, tp1=55.0, tp2=60.0),
            ]
        }

        def fetch(pair, since=None):
            return {
                "RFXIUSDT": [{"high": 105.0, "low": 95.0, "close": 102.0}],  # FLOATING
                "LITUSDT": [{"high": 54.0, "low": 44.0, "close": 52.0}],     # SL
            }[pair]

        recap = build_recap(history, fetch, now_key="2026-08-15 19:06")
        assert recap is not None
        assert "🧾 1 sinyal ⏳ FLOATING dibawa dari sesi sebelumnya" in recap
        main_part = recap.split("CARRY-OVER")[0]
        assert "#RFXI" not in main_part            # floating tidak tampil di seksi utama
        assert "#LIT" in main_part and "SL" in main_part  # final tetap di seksi utama
        assert recap.count("#RFXI") == 1           # di seksi carry-over, sekali saja

    def test_floating_kept_carried_until_tp1_in_next_session(self):
        # Continuity: RFXI floating di sesi T-1, di sesi T+1 menyentuh TP1
        # -> status final, keluar dari Carry-Over (tidak diseret terus).
        history = {
            "2026-08-15 13:35": [_sig(symbol="RFXIUSDT", base="RFXI")],
            "2026-08-15 19:06": [_sig(symbol="LITUSDT", base="LIT", entry=50.0, sl=45.0, tp1=55.0, tp2=60.0)],
        }

        def fetch(pair, since=None):
            return {
                "RFXIUSDT": [{"high": 111.0, "low": 95.0, "close": 110.0}],  # TP1
                "LITUSDT": [{"high": 121.0, "low": 95.0, "close": 115.0}],   # TP2
            }[pair]

        recap = build_recap(history, fetch, now_key="2026-08-16 13:30")
        assert recap is not None
        assert "CARRY-OVER" not in recap
        assert "#RFXI" not in recap
        assert "#LIT" in recap and "TP2" in recap

    def test_carryover_separated_when_main_section_empty(self):
        # Semua sinyal sesi utama FLOATING -> seksi utama kosong; seksi
        # CARRY-OVER tetap tampil terpisah rapi dengan pembatas garis.
        history = {
            "2026-08-15 13:35": [
                _sig(symbol="RFXIUSDT", base="RFXI"),
                _sig(symbol="RCDNSUSDT", base="RCDNS"),
            ]
        }
        recap = build_recap(history, self._floating_fetch, now_key="2026-08-15 19:06")
        assert recap is not None
        lines = recap.splitlines()
        assert "SEKSI 1 — SESI UTAMA" in recap
        assert "Tidak ada sinyal selesai di sesi utama" in recap
        assert "SEKSI 2 — CARRY-OVER — POSISI AKTIF DARI SESI SEBELUMNYA" in recap
        carry_idx = next(i for i, l in enumerate(lines) if "SEKSI 2 — CARRY-OVER" in l)
        assert lines[carry_idx - 1] == "━━━━━━━━━━━━"
        assert lines[carry_idx + 1] == "🧾 2 sinyal ⏳ FLOATING dibawa dari sesi sebelumnya"
        assert "#RFXI" in recap and "#RCDNS" in recap

    def test_floating_from_older_sessions_also_carried(self):
        # Continuity antar beberapa sesi: floating dari sesi lebih lama (masih
        # dalam jendela EVAL_MAX_HOURS 24 jam) tetap dievaluasi & dibawa,
        # sesi terbaru tampil lebih dulu.
        history = {
            "2026-08-15 07:00": [_sig(symbol="XYZUSDT", base="XYZ")],
            "2026-08-15 13:35": [_sig(symbol="RFXIUSDT", base="RFXI")],
        }
        recap = build_recap(history, self._floating_fetch, now_key="2026-08-15 19:06")
        assert recap is not None
        assert "🧾 2 sinyal ⏳ FLOATING dibawa dari sesi sebelumnya" in recap
        assert "#RFXI" in recap and "#XYZ" in recap
        assert recap.index("#RFXI") < recap.index("#XYZ")  # terbaru dulu
