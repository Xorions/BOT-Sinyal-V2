"""Mengirim pesan ke Telegram via Bot API (HTTP langsung, tanpa library besar)."""

import html
import re

import requests

from config import REQUEST_TIMEOUT, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramSendError(Exception):
    """Gagal mengirim pesan ke Telegram."""


# Baris judul blok sinyal koin: `<b>#VIRTUAL (VIRTUALUSDT)</b>` (HTML briefing),
# `#BTC (BTCUSDT) — BUY ...` (plain text), atau `#BTC BUY` (recap evaluasi).
# Header seksi (`<b>📈 SINYAL LONG (BUY)</b>` dll.) tidak diawali `#`, sehingga
# hanya judul koin yang dikenali sebagai awal blok sinyal.
_SIGNAL_BLOCK_RE = re.compile(r"^\s*(?:<b>)?#\w")

# Header seksi briefing (`📈 SINYAL LONG (BUY)`, `📉 SINYAL SHORT (SELL)`,
# `⚪ WATCHLIST (NEUTRAL)`) ikut dijadikan batas blok — Fix #3: tanpa ini,
# header WATCHLIST menempel di akhir chunk seksi sebelumnya dan koin NEUTRAL-nya
# muncul di chunk berikutnya tanpa header.
_SECTION_HEADER_RE = re.compile(r"^\s*(?:<b>)?[📈📉⚪]")


def _is_signal_block_start(line: str) -> bool:
    """True bila baris ini memulai blok sinyal sebuah koin (judul `#BASE`)."""
    return bool(_SIGNAL_BLOCK_RE.match(line))


def _is_section_header_start(line: str) -> bool:
    """True bila baris ini header seksi (LONG / SHORT / WATCHLIST)."""
    return bool(_SECTION_HEADER_RE.match(line))


def _chunk_text(text: str, limit: int = 4000) -> list:
    """Pecah pesan per baris agar tiap chunk <= limit karakter. (fallback)

    Aman untuk parse_mode=HTML: baris demi baris utuh (tag dibuka & ditutup
    dalam baris yang sama), sehingga tidak ada tag yang terpotong.
    """
    lines = text.splitlines()
    chunks: list = []
    current: list = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        while line_len > limit:
            head, line = line[: limit - 1], line[limit - 1:]
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            chunks.append(head)
            line_len = len(line) + 1
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def _split_signal_blocks(text: str, limit: int = 4000) -> list:
    """Pecah pesan per BLOK KOIN (batas antar sinyal `#BASE`), bukan asal baris.

    Satu blok koin = dari baris judul `#BASE` sampai sebelum judul koin
    berikutnya. Header seksi (📈/📉/⚪) juga dijadikan batas blok (Fix #3) agar
    header WATCHLIST tidak terpisah dari koin NEUTRAL-nya saat pesan dipecah.
    Header/meta menempel di awal chunk pertama; footer Disclaimer berada setelah
    pemisah koin terakhir, sehingga selalu ikut di akhir sinyal koin terakhir.
    Konsekuensinya setiap chunk berisi koin-koin UTUH — tidak ada koin yang
    terpotong di tengah (mis. judul `#VIRTUAL` di satu pesan dan detailnya di
    pesan lain). Tanpa blok koin -> fallback `_chunk_text`.
    """
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if _is_signal_block_start(line) or _is_section_header_start(line)]
    if not starts:
        return _chunk_text(text, limit)

    ranges: list = []
    if starts[0] > 0:
        ranges.append((0, starts[0]))
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        ranges.append((start, end))

    chunks: list = []
    current: list = []
    current_len = 0
    for start, end in ranges:
        block = lines[start:end]
        block_len = sum(len(line) + 1 for line in block)
        if current and current_len + block_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if block_len > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            chunks.extend(_chunk_text("\n".join(block), limit))
        else:
            current.extend(block)
            current_len += block_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def _split_telegram(text: str, limit: int = 4000) -> list:
    """Pecah pesan untuk Telegram: utamakan batas blok koin, else per baris."""
    return _split_signal_blocks(text, limit)


def _strip_html(text: str) -> str:
    """Hapus tag HTML & unescape entity untuk fallback plain text."""
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def send_telegram(text: str, chat_id: str = "") -> None:
    token = TELEGRAM_BOT_TOKEN.strip()
    chat = (chat_id or TELEGRAM_CHAT_ID).strip()
    if not token or not chat:
        raise TelegramSendError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diisi di .env")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in _split_telegram(text):
        payload = {
            "chat_id": chat,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 400:
            plain = {
                "chat_id": chat,
                "text": _strip_html(chunk),
                "disable_web_page_preview": True,
            }
            resp = requests.post(url, json=plain, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise TelegramSendError(f"Telegram API error {resp.status_code}: {resp.text[:200]}")
