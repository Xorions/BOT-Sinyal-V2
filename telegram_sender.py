"""Mengirim pesan ke Telegram via Bot API (HTTP langsung, tanpa library besar)."""

import html
import re

import requests

from config import REQUEST_TIMEOUT, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class TelegramSendError(Exception):
    """Gagal mengirim pesan ke Telegram."""


def _chunk_text(text: str, limit: int = 4000) -> list:
    """Pecah pesan per baris agar tiap chunk <= limit karakter.

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
    for chunk in _chunk_text(text):
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
