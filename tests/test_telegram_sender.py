"""Test pemisahan pesan Telegram per blok koin (tanpa network)."""

from telegram_sender import _chunk_text, _is_signal_block_start, _split_signal_blocks, _strip_html

LIMIT = 4000


def _coin_block(base: str, symbol: str) -> list:
    return [
        f"<b>#{base} ({symbol})</b> — BUY · Confidence 70%",
        "🔑 Entry: <b>$1.00</b>",
        "🛡️ SL: <b>$0.90</b> (-10.00%)",
        "🎯 TP1: <b>$1.10</b> (+10.00%)",
        "🎯 TP2: <b>$1.20</b> (+20.00%)",
        "💹 24j: +5.20%",
        "📝 Demand Zone H1 Tersentuh",
        "    + [H4] Tren utama Bullish",
        "    + [H1] Harga masuk Demand Zone",
        "    + [M15] RSI Rebound",
        "📊 Skor: <b>+0.42</b>  (Tek +0.20 · SMC +0.20 · Sent +0.00 · Whale +0.00 · Onch +0.00)",
        "",
        "━━━━━━━━━━━━",
        "",
    ]


def _briefing_text(n_coins: int = 8, disclaimer: bool = True) -> str:
    lines = [
        "<b>📊 DAY TRADING BRIEFING — MTF SMC + S&amp;D</b>",
        "🕐 Jumat, 07 Agu 2026, 13:30 WIB",
        "⚙️ Analisa: Kompas H4/D1 → Zona H1 → Konfirmasi M15",
        "🌐 Fear&Greed: 29",
        "",
        "<b>📈 SINYAL LONG (BUY)</b>",
        "",
    ]
    for i in range(n_coins):
        lines.extend(_coin_block(f"COIN{i}", f"COIN{i}USDT"))
    if disclaimer:
        lines.append("⚠️ Disclaimer: Sinyal berbasis indikator otomatis & data publik.")
    return "\n".join(lines)


class TestSignalBlockDetection:
    def test_html_coin_title_detected(self):
        assert _is_signal_block_start("<b>#VIRTUAL (VIRTUALUSDT)</b> — BUY · Confidence 70%")

    def test_plain_coin_title_detected(self):
        assert _is_signal_block_start("#BTC (BTCUSDT) — BUY · Confidence 70%")

    def test_recap_coin_line_detected(self):
        assert _is_signal_block_start("#BTC BUY")

    def test_section_header_not_detected(self):
        assert not _is_signal_block_start("<b>📈 SINYAL LONG (BUY)</b>")
        assert not _is_signal_block_start("<b>📊 DAY TRADING BRIEFING — MTF SMC + S&amp;D</b>")
        assert not _is_signal_block_start("🕐 Jumat, 07 Agu 2026, 13:30 WIB")

    def test_disclaimer_not_detected(self):
        assert not _is_signal_block_start("⚠️ Disclaimer: Sinyal berbasis indikator otomatis.")


class TestSplitSignalBlocks:
    def test_short_message_stays_single_chunk(self):
        text = _briefing_text(n_coins=2)
        assert _split_signal_blocks(text) == [text]

    def test_long_message_split_into_multiple_chunks(self):
        text = _briefing_text(n_coins=12)
        chunks = _split_signal_blocks(text)
        assert len(chunks) > 1

    def test_reconstruction_preserves_exact_content(self):
        text = _briefing_text(n_coins=10)
        chunks = _split_signal_blocks(text)
        assert "\n".join(chunks) == text

    def test_each_chunk_within_limit(self):
        text = _briefing_text(n_coins=12)
        for chunk in _split_signal_blocks(text):
            assert len(chunk) <= LIMIT

    def test_coin_block_never_split_across_chunks(self):
        text = _briefing_text(n_coins=10)
        chunks = _split_signal_blocks(text)
        for i in range(10):
            title = f"<b>#COIN{i} (COIN{i}USDT)</b>"
            start = text.index(title)
            end = text.index("━━━━━━━━━━━━", start) + len("━━━━━━━━━━━━")
            block = text[start:end]
            containing = [c for c in chunks if block in c]
            assert len(containing) == 1, f"koin COIN{i} terpotong antar chunk"

    def test_header_stays_at_start_of_first_chunk(self):
        text = _briefing_text(n_coins=8)
        chunks = _split_signal_blocks(text)
        assert "DAY TRADING BRIEFING" in chunks[0]
        assert "SINYAL LONG" in chunks[0]

    def test_disclaimer_attached_to_last_coin(self):
        text = _briefing_text(n_coins=8)
        chunks = _split_signal_blocks(text)
        last = chunks[-1]
        assert "⚠️ Disclaimer" in last
        assert last.rfind("⚠️ Disclaimer") > last.rfind("#COIN")
        assert last.rfind("⚠️ Disclaimer") > last.rfind("━━━━━━━━━━━━")
        # Disclaimer ada di akhir chunk terakhir (tidak terpotong ke chunk lain)
        assert last.rstrip().endswith("data publik.")

    def test_disclaimer_without_coin_stays_in_single_chunk(self):
        text = _briefing_text(n_coins=0, disclaimer=True)
        chunks = _split_signal_blocks(text)
        assert "⚠️ Disclaimer" in chunks[-1]


class TestChunkTextFallback:
    def test_no_signal_blocks_falls_back_to_line_chunking(self):
        text = ("<b>📊 DAY TRADING BRIEFING</b>\n🕐 test\n" + "baris data\n" * 500)
        chunks = _chunk_text(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= LIMIT

    def test_chunk_text_keeps_short_lines_intact(self):
        text = "line A\n" + "line B\n" + "line C\n" * 3000
        for chunk in _chunk_text(text):
            assert len(chunk) <= LIMIT
            assert all(len(l) <= LIMIT for l in chunk.splitlines())

    def test_chunk_text_splits_only_overlong_line(self):
        # Satu baris > limit tetap harus dipecah, tapi tanpa merusak baris lain.
        text = "normal line\n" + "x" * 5000
        chunks = _chunk_text(text)
        assert chunks[0] == "normal line"
        assert all(c.count("x") for c in chunks[1:])
        assert sum(c.count("x") for c in chunks) == 5000
        assert all(len(c) <= LIMIT for c in chunks)


class TestStripHtml:
    def test_strip_html_removes_tags_and_unescapes(self):
        plain = _strip_html("<b>#BTC (BTCUSDT)</b> — BUY &amp; SELL")
        assert plain == "#BTC (BTCUSDT) — BUY & SELL"
