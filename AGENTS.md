# AGENTS.md

Panduan untuk AI agent / developer yang bekerja di **BOT-Sinyal-Trading-V2**.

## 1. Overview Arsitektur & Tech Stack

Bot Telegram sinyal trading crypto versi lanjutan. Dijalankan **sekali sehari (07:00 WIB)** via GitHub Actions (cron `0 0 * * *`). Alur: kumpulkan data multi-sumber gratis → hitung indikator teknikal + SMC + sentiment + whale + on-chain → skoring berbobot → kirim Top-5 Daily Briefing ke Telegram.

```
bot.py                     # Entry point: orkestrasi data → skoring → kirim
config.py                  # Kredensial & parameter (semua bisa via .env)
engine.py                  # Skoring 5 kategori + format pesan HTML
evaluation.py              # Riwayat sinyal + evaluasi/recap sinyal kemarin
telegram_sender.py         # HTTP Bot API sendMessage
data/
  _client.py               # http_get_json: retry + backoff + 429 Retry-After
  binance.py               # klines, ticker 24j (agregat), funding, L/S ratio
  cmc.py                   # top symbols + market overview (free tier)
  sentiment.py             # Fear & Greed + score_fear_greed (contrarian)
  onchain.py               # whale netflow ETH (Etherscan), BTC stats (blockchain.info)
  history.json             # Riwayat sinyal harian — DI-COMMIT BALIK oleh CI
indicators/                # murni, tanpa I/O
  rsi.py                   # Wilder RSI
  macd.py                  # EMA + MACD line/signal/histogram
  support_resistance.py    # find_swings, nearest_levels, pivot_points
  smc.py                   # detect_order_blocks, detect_fvg, detect_structure
tests/                     # pytest (39 kasus)
.github/workflows/daily.yml
```

- **Python 3.12**, `requests`, `python-dotenv`, `pytest` (dev).
- Sumber data: **Binance `data-api.binance.vision`** (spot, tidak geo-block) + **CMC** (opsional, free tier) + **alternative.me** (Fear & Greed) + **Etherscan / blockchain.info** (opsional proxy on-chain).
- Tidak ada server 24/7; GitHub Actions gratis.

## 2. Skoring (aturan baku — jangan diubah tanpa alasan)

Normalisasi tiap kategori ke **-1.0..+1.0**, gabung berbobot (`config.py`):

| Kategori | Bobot | Komponen |
|---|---|---|
| Teknikal | 0.40 | RSI (<30:+0.30, <40:+0.15, >70:-0.30, >60:-0.15), MACD histogram (±0.35), momentum 24j (≥3%:+0.20, ≤-3%:-0.20) |
| SMC & S&R | 0.20 | struktur BOS:+0.25 / CHoCH:-0.25, OB dekat:+0.25, FVG:+0.15, support/resistance dekat (±0.20) |
| Sentiment | 0.15 | `score_fear_greed` (contrarian), funding (≥0.03%:-0.30, ≤-0.03%:+0.30), L/S ratio (≥1.5:-0.20, ≤0.7:+0.20) |
| Whale | 0.15 | netflow ETH: masuk exchange:-1.0, keluar:+1.0 |
| On-chain | 0.10 | BTC `n_tx_24h` ada:+0.5 |

- Aksi: skor ≥ `BUY_THRESHOLD` (0.10) = BUY, ≤ `SELL_THRESHOLD` (-0.10) = SELL, else NEUTRAL.
- Confidence: `clamp(25, 95, CONFIDENCE_BASE + |skor|*40)`.
- **Konvensi RSI/funding = kontrarian**: overbought/euforia = negatif (antisipasi pullback).
- SL/TP di `engine._levels()`: dari level S&R terdekat; fallback persentase bila tidak ada level.

## 3. Filter aset (aturan baku — jangan diubah tanpa alasan)

Di `bot.py`, pasangan kandidat difilter lewat `_eligible_pair()` sebelum diskoring. Tiga lapis:

1. **Stablecoin** — set `STABLECOINS` di `bot.py` (USDT, USDC, DAI, USDD, FDUSD, RLUSD, XUSD, EURS/EURC/EUR/EURI/EURIT, FRAX, BFUSD, dll).
2. **Leveraged token** — `SKIP_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")` (mis. BTCUP/BTCDOWN).
3. **Token saham/ETF Binance (Binance Shares)** — `US_STOCK_TICKERS` + `_is_stock_token()`: base = ticker saham/ETF US atau `ticker + "B"` (NVDAB→NVDA, QQQB→QQQ, SPYB→SPY, GOOGLB→GOOGL, TSLAB→TSLA, SPCXB→SPCX, MUUB→MUU; langsung: MUB, BE).

> **Penting:** koin kripto asli yang berakhiran `B` (**BNB, ARB, SHIB, TRB, DGB, CKB, BB**) **tidak boleh** terkena filter — deteksi selalu via lookup ke `US_STOCK_TICKERS`, bukan sekadar cek suffix `B`. Jika ada token saham baru, tambahkan ticker polosnya (tanpa `B`) ke set.

## 5. Evaluasi Sinyal & Riwayat (`evaluation.py`) — aturan baku

- **`add_signals_today()`** dipanggil di `bot.py` **setelah pesan berhasil dikirim** — menyimpan sinyal terpilih hari itu ke `data/history.json` (key tanggal WIB `YYYY-MM-DD`, menimpa entri hari yang sama).
- **`build_recap()`** dijalankan **sebelum** briefing baru dikirim: membaca hari terakhir **sebelum** hari ini (`previous_day_signals`, robust terhadap hari kosong), mengambil `(high, low, current)` 24j via `fetch_fn` (dari `binance.get_ticker_24h`), lalu menentukan status.
- Urutan cek status (`evaluate_signal`): **TP2 → TP1 → SL → Floating** (TP lebih dulu; lihat catatan kontrarian). BUY pakai `high` untuk TP dan `low` untuk SL; SELL kebalikannya.
- **Win rate** = % sinyal yang menyentuh TP1/TP2 dari **seluruh** sinyal yang dievaluasi (Floating ikut penyebut). Nilai SL/TP dari `history.json`.
- Recap **jangan menggagalkan scan**: fetch gagal → status `None` → sinyal itu dilewati; tak ada riwayat/semua gagal → `build_recap` mengembalikan `None`.
- **CI commit-back**: runner di-reset tiap run, jadi workflow wajib men-*commit balik* `data/history.json` (step "Commit balik riwayat sinyal", `permissions: contents: write` + `concurrency` agar tidak race). Jangan pernah menambahkan `data/history.json` ke `.gitignore`.

## 4. Format pesan (baca `engine.format_message()`)

- Header seksi: `<b>📈 SINYAL LONG (BUY)</b>`, `<b>📉 SINYAL SHORT (SELL)</b>`, `<b>⚪ WATCHLIST (NEUTRAL)</b>` (seksi kosong di-skip).
- Tiap sinyal = blok dari `_signal_lines()`: baris judul `#BASE (SYMBOL)` → Entry → SL → TP1 → TP2 → perubahan 24j → alasan `📝` (baris pertama berprefix `📝`, sisanya **per-baris** tanpa prefix) → `📊 Skor` (total + breakdown Tek/SMC/Sent/Whale/Onch) → pemisah `───`.
- Urutan header & baris sinyal adalah **kontrak visual** — ubah hanya bila diminta user. Format harga lewat `_fmt_price()` (≥1000: 0 desimal, ≥1: 2 desimal, <1: 6 desimal).
- Kirim memakai Telegram HTML parse mode (`telegram_sender.py`).

## 6. Panduan Pengembangan

### Menambah indikator baru
1. Fungsi **murni** di `indicators/<nama>.py` (list angka/candle → angka/None), lalu tambah unit test di `tests/`.
2. Buat `score_<kategori>()` di `engine.py` → normalisasi -1.0..+1.0 → jumlahkan ke `total`.
3. Daftarkan bobot di `config.py` (pastikan total bobot ≈ 1.0).

### Data sumber baru
- Tambah fetcher di `data/` memakai `data._client.http_get_json` (sudah ada retry + 429 handling).
- **Jangan pernah** meng-inline request `requests.get` langsung tanpa lewat `_client` (kecuali WebSocket khusus).

### Sifat opsional / graceful degradation
- **Binance futures** (funding/L-S ratio) dapat diblokir region → fungsi mengembalikan `[]` / `None`, dan `bot.py` memakai `futures_ok` probe sekali di awal. **Jangan jadikan futures wajib** — bot harus tetap jalan hanya dengan spot + Fear & Greed.
- Whale & on-chain butuh API key opsional → `None` bila tidak dikonfigurasi.
- Pola ini wajib dipertahankan: satu sumber gagal ≠ seluruh scan gagal.

### Menjalankan & menguji
```powershell
venv\Scripts\python.exe -m pytest tests -v   # 39 test
venv\Scripts\python.exe bot.py               # scan nyata; tanpa kredensial → print konsol
```

## 7. Keamanan Kredensial
- `.env` di-ignore (`gitignore`) — jangan pernah commit token/key.
- Secrets GitHub Actions: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, opsional `CMC_API_KEY`, `ETHERSCAN_API_KEY`.
- Jangan print token/secret ke log.
