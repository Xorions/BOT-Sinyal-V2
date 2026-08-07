# AGENTS.md

Panduan untuk AI agent / developer yang bekerja di **BOT-Sinyal-Trading-V2**.

## 1. Overview Arsitektur & Tech Stack

Bot Telegram sinyal trading crypto versi lanjutan (**Day Trading MTF — SMC + Supply & Demand**).
Dijalankan **2x sehari (13:30 & 19:00 WIB)** via GitHub Actions (cron `30 6 * * *` & `0 12 * * *`).
Alur: kumpulkan data multi-sumber gratis → analisa **Multi-Timeframe** (Kompas H4/D1 → Zona H1 → Pelatuk M15) → skoring berbobot → evaluasi sinyal sesi sebelumnya → kirim Top-5 Day Trading Briefing ke Telegram.

```
bot.py                     # Entry point: orkestrasi data MTF → skoring → kirim
config.py                  # Kredensial & parameter (semua bisa via .env)
engine.py                  # Skoring MTF (kompas/zona/pelatuk) + format pesan HTML
evaluation.py              # Riwayat sinyal + evaluasi/recap sesi sebelumnya
telegram_sender.py         # HTTP Bot API sendMessage
data/
  _client.py               # http_get_json: retry + backoff + 429 Retry-After
  binance.py               # klines MTF (1d/4h/1h/15m), ticker 24j, funding, L/S ratio
  cmc.py                   # top symbols + market overview (free tier)
  sentiment.py             # Fear & Greed + score_fear_greed (contrarian)
  onchain.py               # whale netflow ETH (Etherscan), BTC stats (blockchain.info)
  history.json             # Riwayat sinyal per sesi — DI-COMMIT BALIK oleh CI
indicators/                # murni, tanpa I/O
  rsi.py                   # Wilder RSI
  macd.py                  # EMA + MACD line/signal/histogram + histogram series (cross)
  support_resistance.py    # find_swings, nearest_levels, pivot_points
  smc.py                   # detect_order_blocks, detect_fvg, detect_structure, EQH/EQL, Liquidity Sweep
  supply_demand.py         # detect_supply_demand, in_zone, nearest_demand/supply
tests/                     # pytest (57 kasus)
.github/workflows/daily.yml
```

- **Python 3.12**, `requests`, `python-dotenv`, `pytest` (dev).
- Sumber data: **Binance `data-api.binance.vision`** (spot, tidak geo-block) + **CMC** (opsional, free tier) + **alternative.me** (Fear & Greed) + **Etherscan / blockchain.info** (opsional proxy on-chain).
- Tidak ada server 24/7; GitHub Actions gratis.

## 2. Skoring (aturan baku — jangan diubah tanpa alasan)

Normalisasi tiap kategori ke **-1.0..+1.0**, gabung berbobot (`config.py`):

| Kategori | Bobot | Komponen |
|---|---|---|
| SMC & S&D (MTF) | 0.40 | **Kompas H4/D1** (BOS:+0.45 / CHoCH:-0.45, fallback D1 ±0.30) + **Zona H1**: Demand/Supply ter-sentuh (±0.30/dekat ±0.15), OB (±0.20), FVG (±0.15), Liquidity Sweep (±0.25), Support/Resistance dekat (±0.15) |
| Teknikal (M15) | 0.20 | RSI (<30:+0.20, >70:-0.20), MACD cross/histogram (±0.15–0.25), BOS/CHoCH M15 (±0.20), momentum 24j (≥3%:+0.20, ≤-3%:-0.20) |
| Sentiment | 0.15 | `score_fear_greed` (contrarian), funding (≥0.03%:-0.30, ≤-0.03%:+0.30), L/S ratio (≥1.5:-0.20, ≤0.7:+0.20) |
| Whale | 0.15 | netflow ETH: masuk exchange:-1.0, keluar:+1.0 |
| On-chain | 0.10 | BTC `n_tx_24h` ada:+0.5 |

- **Aturan kompas (baku):** H4 bullish → **HANYA** izinkan sinyal BUY; H4 bearish → **HANYA** SELL (D1 fallback bila H4 netral).
- **Validasi setup:** sinyal hanya BUY/SELL bila M15 searah kompas **dan** harga menyentuh zona SMC/S&D H1 (`engine._setup_valid`). Di luar itu → NEUTRAL.
- Aksi: skor ≥ `BUY_THRESHOLD` (0.10) = BUY, ≤ `SELL_THRESHOLD` (-0.10) = SELL, else NEUTRAL.
- Confidence: `clamp(25, 95, CONFIDENCE_BASE + |skor|*40)`.
- **Konvensi RSI/funding = kontrarian**: overbought/euforia = negatif (antisipasi pullback).
- Entry/SL/TP di `engine._levels_mtf()`: dari zona H1 (Demand/Supply zone atau OB; SL di luar zona, TP di level S&R H1); fallback persentase bila tidak ada zona.

## 3. Filter aset (aturan baku — jangan diubah tanpa alasan)

Di `bot.py`, pasangan kandidat difilter lewat `_eligible_pair()` sebelum diskoring. Tiga lapis:

1. **Stablecoin** — set `STABLECOINS` di `bot.py` (USDT, USDC, DAI, USDD, FDUSD, RLUSD, XUSD, EURS/EURC/EUR/EURI/EURIT, FRAX, BFUSD, dll).
2. **Leveraged token** — `SKIP_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")` (mis. BTCUP/BTCDOWN).
3. **Token saham/ETF Binance (Binance Shares)** — `US_STOCK_TICKERS` + `_is_stock_token()`: base = ticker saham/ETF US atau `ticker + "B"` (NVDAB→NVDA, QQQB→QQQ, SPYB→SPY, GOOGLB→GOOGL, TSLAB→TSLA, SPCXB→SPCX, MUUB→MUU; langsung: MUB, BE).

> **Penting:** koin kripto asli yang berakhiran `B` (**BNB, ARB, SHIB, TRB, DGB, CKB, BB**) **tidak boleh** terkena filter — deteksi selalu via lookup ke `US_STOCK_TICKERS`, bukan sekadar cek suffix `B`. Jika ada token saham baru, tambahkan ticker polosnya (tanpa `B`) ke set.

## 4. Data: Binance & Sumber Lain

- **MTF**: `get_klines_multi(symbol)` mengembalikan `{interval: [candle, ...]}` untuk `1d`, `4h`, `1h`, `15m` (konstanta `INTERVAL_1D/4H/1H/M15`, limit default `MTF_LIMITS`). Satu interval gagal → interval itu dilewati, analisa tetap jalan (sinyal di-skip).
- `get_ticker_24h(symbol)` → `ticker_24h` di `bot.py` (1 panggilan agregat).
- CMC opsional (free tier): tanpa candle historis; bila kosong → fallback semua pasangan USDT by volume Binance.
- `get_funding_rate` / `get_long_short_ratio` (futures): diprobe sekali murah; bila gagal → sentiment pakai Fear & Greed saja.
- Whale/on-chain opsional; gagal → kategori dilewati tanpa mempengaruhi kategori lain.

## 5. Evaluasi Sinyal & Riwayat (`evaluation.py`) — aturan baku

- **`add_signals_today()`** dipanggil di `bot.py` **setelah pesan berhasil dikirim** — menyimpan sinyal terpilih sesi itu ke `data/history.json` (key sesi WIB `YYYY-MM-DD HH:MM`; kunci lama `YYYY-MM-DD` tetap didukung).
- **`build_recap()`** dijalankan **sebelum** briefing baru dikirim: membaca **sesi terakhir sebelum sesi sekarang** (`previous_session_signals`, robust terhadap hari/sesi kosong), mengambil `(high, low, current)` 24j via `fetch_fn` (dari `binance.get_ticker_24h`), lalu menentukan status.
- Urutan cek status (`evaluate_signal`): **TP2 → TP1 → SL → Floating** (TP lebih dulu; lihat catatan kontrarian). BUY pakai `high` untuk TP dan `low` untuk SL; SELL kebalikannya.
- **Win rate** = % sinyal yang menyentuh TP1/TP2 dari **seluruh** sinyal yang dievaluasi (Floating ikut penyebut). Nilai SL/TP dari `history.json`.
- Recap **jangan menggagalkan scan**: fetch gagal → status `None` → sinyal itu dilewati; tak ada riwayat/semua gagal → `build_recap` mengembalikan `None`.
- **CI commit-back**: runner di-reset tiap run, jadi workflow wajib men-*commit balik* `data/history.json` (step "Commit balik riwayat sinyal", `permissions: contents: write` + `concurrency` agar tidak race). Jangan pernah menambahkan `data/history.json` ke `.gitignore`.

## 6. Format pesan (baca `engine.format_message()`)

- Header seksi: `<b>📈 SINYAL LONG (BUY)</b>`, `<b>📉 SINYAL SHORT (SELL)</b>`, `<b>⚪ WATCHLIST (NEUTRAL)</b>` (seksi kosong di-skip).
- Baris meta briefing (dari `engine.meta_lines()`): `📊 DAY TRADING BRIEFING — MTF SMC + S&D`, `🕐 <tanggal jam> WIB`, `⚙️ Analisa: Kompas H4/D1 → Zona H1 → Konfirmasi M15`, `🌐 Fear&Greed: N`.
- Tiap sinyal = blok dari `_signal_lines()`: baris judul `#BASE (SYMBOL)` → Entry → SL → TP1 → TP2 → perubahan 24j → alasan `📝` (baris pertama berprefix `📝` tanpa dash, sisanya diindentasi **4 spasi + `- `**) → `📊 Skor` (total + breakdown SMC/Tek/Sent/Whale/Onch) → pemisah `───`.
- **Alasan wajib MTF**: baris pertama `📝` = headline zona H1 (mis. "Demand Zone & Bullish OB H1 Tersentuh"); baris alasan menyertakan prefix `[H4]`/`[D1]` (kompas) → `[H1]` (zona/OB/FVG/Sweep/S&R) → `[M15]` (RSI/MACD/BOS trigger); baris terakhir `Momentum 24j ... | Fear&Greed N`.
- Urutan header & baris sinyal adalah **kontrak visual** — ubah hanya bila diminta user. Format harga lewat `_fmt_price()` (≥1000: 0 desimal, ≥1: 2 desimal, <1: 6 desimal).
- Kirim memakai Telegram HTML parse mode (`telegram_sender.py`).

## 7. Panduan Pengembangan

### Menambah indikator baru
1. Fungsi **murni** di `indicators/<nama>.py` (list angka/candle → angka/None), lalu tambah unit test di `tests/`.
2. Buat `score_<kategori>()` di `engine.py` → normalisasi -1.0..+1.0 → jumlahkan ke `total`.
3. Daftarkan bobot di `config.py` (pastikan total bobot ≈ 1.0).

### Menambah logika MTF baru
- **Kompas** (H4/D1) → `engine.analyze_compass()`, keluarkan arah `bullish/bearish/netral` + alasan.
- **Zona H1** → `engine.map_h1_zones()` (panggil `indicators/supply_demand.py`, `smc.py`, `support_resistance.py`).
- **Pelatuk M15** → `engine.score_trigger()`.
- Validasi arah BUY/SELL: `engine._setup_valid()` — JANGAN short-circuit aturan kompas; sinyal baru boleh masuk bila M15 searah kompas **dan** harga menyentuh zona SMC/S&D H1.

### Data sumber baru
- Tambah fetcher di `data/` memakai `data._client.http_get_json` (sudah ada retry + 429 handling).
- **Jangan pernah** meng-inline request `requests.get` langsung tanpa lewat `_client` (kecuali WebSocket khusus).

### Sifat opsional / graceful degradation
- **Binance futures** (funding/L-S ratio) dapat diblokir region → fungsi mengembalikan `[]` / `None`, dan `bot.py` memakai `futures_ok` probe sekali di awal. **Jangan jadikan futures wajib** — bot harus tetap jalan hanya dengan spot + Fear & Greed.
- **Data MTF**: interval yang gagal dilewati; jika `15m`/`1h` tidak tersedia, `engine` memakai kompas + zona tanpa trigger (sinyal tetap bisa NEUTRAL).
- Whale & on-chain butuh API key opsional → `None` bila tidak dikonfigurasi.
- Pola ini wajib dipertahankan: satu sumber gagal ≠ seluruh scan gagal.

### Menjalankan & menguji
```powershell
venv\Scripts\python.exe -m pytest tests -v   # 57 test
venv\Scripts\python.exe bot.py               # scan nyata; tanpa kredensial → print konsol
```

## 8. Keamanan Kredensial
- `.env` di-ignore (`gitignore`) — jangan pernah commit token/key.
- Secrets GitHub Actions: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, opsional `CMC_API_KEY`, `ETHERSCAN_API_KEY`.
- Jangan print token/secret ke log.
