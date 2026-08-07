"""HTTP client bersama: retry + backoff untuk semua sumber data."""

import time
from typing import Any, Dict, Optional

import requests

from config import REQUEST_RETRIES, REQUEST_TIMEOUT


class DataSourceError(Exception):
    """Gagal mengambil data dari sumber eksternal."""


def _sleep_seconds(attempt: int) -> float:
    return float(2 * (attempt + 1))


def http_get_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    source: str = "api",
    timeout: Optional[int] = None,
    retries: Optional[int] = None,
) -> Any:
    """GET dengan retry & backoff; menangani 429 (rate limit) via Retry-After."""
    timeout = timeout or REQUEST_TIMEOUT
    attempts = retries if retries is not None else REQUEST_RETRIES
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = min(float(retry_after), 60.0)
                except (TypeError, ValueError):
                    wait = 15.0
                time.sleep(wait)
                last_error = requests.HTTPError(f"429 rate limit, menunggu {wait:.0f}s")
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(_sleep_seconds(attempt))
    raise DataSourceError(f"[{source}] Gagal mengambil data {url}: {last_error}")
