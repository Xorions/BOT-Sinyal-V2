# AGENTS.md

Panduan untuk AI agent / developer yang bekerja di **BOT-Sinyal-Trading-V2**.

## 1. Overview Arsitektur & Tech Stack

Bot Telegram sinyal trading crypto versi lanjutan (**Day Trading MTF — SMC + Supply & Demand**).
Dijalankan **2x sehari (13:30 & 19:00 WIB)** — jadwal dipicu dari luar oleh **Cron-Job.org** (cron `30 6 * * *` & `0 12 * * *` UTC) via API `workflow_dispatch` GitHub Actions. Cron internal GitHub dihapus agar tidak ada pemicu ganda.
Alur: kumpulkan data multi-sumber gratis → analisa **Multi-Timeframe** (Kompas H4/D1 → Zona H1 → Pelatuk M15) → skoring berbobot → evaluasi sinyal sesi sebelumnya → kirim Top-5 Day Trading Briefing ke Telegram.

```
bot.py                     # Entry point: orkestrasi data MTF → skoring → kirim. Anti sinyal berulang di `_apply_cooldown` (cooldown re-entry level sama)
backtest.py                # Backtest offline: walk M15 tanpa look-ahead → win rate/EV per jendela (3 jendela x 7 hari dipakai untuk tuning RRR/EMA20, Fix R5)
_exp.py                    # Eksperimen tuning parameter (RRR/EMA) atas data histori
config.py                  # Kredensial & parameter (semua bisa via .env)
engine.py                  # Skoring MTF (kompas/zona/pelatuk) + format pesan HTML
evaluation.py              # Riwayat sinyal + evaluasi/recap sesi sebelumnya
telegram_sender.py         # HTTP Bot API sendMessage
data/
  _client.py               # http_get_json: retry + backoff + 429 Retry-After
  bitget.py               # klines MTF (1d/4h/1h/15m), ticker 24j, funding, L/S ratio
  cmc.py                   # top symbols + market overview (free tier)
  sentiment.py             # Fear & Greed + score_fear_greed (contrarian)
  onchain.py               # whale netflow ETH (Etherscan), BTC stats (blockchain.info)
  history.json             # Riwayat sinyal per sesi — DI-COMMIT BALIK oleh CI
indicators/                # murni, tanpa I/O
  rsi.py                   # Wilder RSI
  macd.py                  # EMA + MACD line/signal/histogram + histogram series (cross)
  ema.py                   # EMA 20/50 dynamic S/R + deteksi pullback (analyze_ema)
  fibonacci.py             # Fib level 0.5/0.618/0.786 + Golden Zone (analyze_fibonacci)
  support_resistance.py    # find_swings, nearest_levels, pivot_points
  smc.py                   # detect_order_blocks, detect_fvg, detect_structure, EQH/EQL, Liquidity Sweep
  supply_demand.py         # detect_supply_demand, in_zone, nearest_demand/supply
tests/                     # pytest (233 kasus)
.github/workflows/daily.yml
```

- **Python 3.12**, `requests`, `python-dotenv`, `pytest` (dev).
- Sumber data: **Bitget V2 `api.bitget.com`** (spot + futures, tanpa API key) + **CMC** (opsional, free tier) + **alternative.me** (Fear & Greed) + **Etherscan / blockchain.info** (opsional proxy on-chain).
- Konvensi symbol Bitget: **Spot** `BTCUSDT`, **Futures USDT-M Perpetual** `BTCUSDT_UMCBL` (konversi via `bitget.to_spot_symbol` / `bitget.to_futures_symbol`); granularity spot `15min/1h/4h/1day`, futures `15m/1H/4H/1D`.
- Tidak ada server 24/7; GitHub Actions gratis.

## 2. Skoring (aturan baku — jangan diubah tanpa alasan)

Normalisasi tiap kategori ke **-1.0..+1.0**, gabung berbobot (`config.py`):

| Kategori | Bobot | Komponen |
|---|---|---|
| S&R (kompas utama) | 0.35 | **Kompas H4/D1**: BOS skala besar:+0.35 / CHoCH:-0.35 (fallback D1 ±0.25) + **H1**: di Demand/Supply zone (±0.25, dekat ≤2% ±0.15), Support/Resistance dekat ≤3% (±0.20), breakout Resistance kunci +0.20 / Support -0.20 |
| SMC (konfluensi) | 0.20 | **H1 searah kompas**: Bullish OB +0.35 / Bearish OB -0.35, FVG ±0.25, Liquidity Sweep sell +0.40 / buy -0.40 — komponen campuran (bull+bear) **tidak** dinilai agar setup campuran tidak meniadakan skor kompas |
| Fibonacci | 0.15 | **Golden Zone 0.5/0.618/0.786**: di zone ±0.45, dekat ≤1% ±0.20; konfluensi Golden Zone ∩ Key Level S&R +0.25 / Order Block +0.20; arah ikut kompas (tanpa kompas → netral 0) |
| EMA 20/50 | 0.15 | **Dynamic S/R** (H1, fallback H4): uptrend/downtrend ±0.30, pullback ke EMA 20 ≤0.5% ±0.30, RSI hook UP 30-40 naik +0.40 / hook DOWN 60-70 turun -0.40 |
| Teknikal (M15) | 0.08 | RSI (<30:+0.20, >70:-0.20), MACD cross (±0.25) / histogram (±0.15) — cross berbobot LEBIH BESAR dari histogram (Fix #1), BOS/CHoCH M15 (±0.20), momentum 24j kontrarian (≤-3%:+0.20, ≥3%:-0.20) (Fix #6) |
| On-chain / Whale | 0.05 | netflow ETH ±0.5 (proxy) & statistik BTC +0.5 — HANYA untuk koin ETH/BTC; kategori **opsional** (`None` = dilewati). Whale digabung ke kategori On-chain (tidak ada bobot Whale terpisah) |
| Sentimen | 0.02 | `score_fear_greed` (contrarian), funding (≥0.03%:-0.30, ≤-0.03%:+0.30), L/S ratio (≥1.5:-0.20, ≤0.7:+0.20) |

- **Aturan kompas (baku):** H4 bullish → **HANYA** izinkan sinyal BUY; H4 bearish → **HANYA** SELL (D1 fallback bila H4 netral).
- **Validasi setup:** sinyal hanya BUY/SELL bila M15 searah kompas **dan** harga menyentuh zona SMC/S&D H1 (`engine._setup_valid`). Di luar itu → NEUTRAL.
- **Trigger M15 diperketat (Fix R4)**: konfirmasi histogram MACD butuh `TRIG_MIN_BARS`=3 bar (45 menit) beruntun searah dengan margin ≥ `TRIG_MARGIN_RATIO` (20%) dari puncak histogram jendela terakhir (`_trigger_valid`); histogram sendirian TANPA struktur M15 searah (bounce 30-45 menit di tengah tren lawan) **tidak** valid. Cross MACD / BOS / CHoCH M15 tetap valid langsung.
- Aksi: skor ≥ `BUY_THRESHOLD` (0.10) = BUY, ≤ `SELL_THRESHOLD` (-0.10) = SELL, else NEUTRAL.
- Confidence: `clamp(25, 95, CONFIDENCE_BASE + |skor|*40)`.
- **Konvensi RSI/funding/momentum = kontrarian**: overbought/euforia = negatif (antisipasi pullback). Momentum 24j mengikuti konvensi ini (Fix #6).
- **Renormalisasi (Fix #2)**: total skor dibagi jumlah bobot kategori yang **benar-benar dipakai** untuk koin itu — koin non-ETH/BTC TIDAK dihitung bobot On-chain; ETH/BTC juga tidak dihitung bila datanya tidak tersedia (`None` = kategori dilewati, sesuai prinsip graceful degradation). Kategori S&R/SMC/Fibo/EMA/Teknikal/Sentimen selalu tersedia (derivasi candle/price), hanya On-chain yang opsional. Skor tetap sebanding lintas koin (bukan rata-rata parsial).
- **S&R adalah kompas utama** (bobot 0.35): skor arah dari tren struktur H4/D1 dan zone/key level H1; arah BUY/SELL ditentukan oleh kompas, bukan konfluensi kecil. **Fibonacci & EMA hanya menambah/mengurangi skor bila searah kompas** (Fibo: tanpa kompas → netral; EMA: arah melekat pada trend EMA itu sendiri, bukan kompas).
- Entry/SL/TP di `engine._levels_mtf()`: dari zona H1 (Demand/Supply zone atau OB; SL di luar zona, TP di level S&R H1); fallback persentase bila tidak ada zona.

### Risk-to-Reward Ratio (RRR) — aturan baku level SL/TP

`engine._levels_mtf()` (via `engine._rr_targets()`) wajib menghasilkan **RRR minimal** sebelum sinyal diterima. Parameter di `config.py` (bisa via `.env`): `RRR_MIN` (0.7), `RRR_TP2` (1.4), `SL_BUFFER_PCT` (0.003), `SL_MIN_DIST_PCT` (0.017), `SL_ATR_MULT` (1.2).

> **Fix R5 — RRR didekatkan (0.7/1.4, hasil backtest 3 jendela x 7 hari)**: TP1 minimal 0.7x jarak SL & TP2 1.4x SL. Target dekat = eksekusi cepat (day trade); win rate naik ~41% → ~60% (61.8/55.0/61.4% di 3 jendela independen), EV per trade tetap positif tipis. Jangan kembalikan ke 1.5/3.0 tanpa backtest ulang (`python backtest.py --days 7 --pairs 20 --step 15m --out backtest_report.txt`).

- **SL** = zona Demand/Supply H1 terdekat + buffer `SL_BUFFER_PCT` (0.3%) di luar zona (BUY: Demand/Support di bawah; SELL: Supply/Resistance di atas). Tanpa zona → fallback statis.
- **TP1** = target struktur H1 terdekat (swing high/low, zona Supply/Demand) **hanya bila** jarak TP1 (%) ≥ `RRR_MIN` x jarak SL (%). Bila target terdekat terlalu dekat (< 1:0.7), paksa TP1 = Entry ± (jarak SL x `RRR_MIN`) **hanya bila tidak terhalang** zona Supply/Demand kuat (`_blocked_by_zone`).
- **SL dinamis ATR (Fix R4)**: jarak SL dipaksa minimal `max(SL_MIN_DIST_PCT, SL_ATR_MULT * ATR(H1)/price)` agar SL terlalu dekat tidak tersapu noise pasar (koin volatil dapat SL lebih lebar).
- **Terhalang → reject**: `_rr_targets` mengembalikan `None` → `_levels_mtf` `None` → `assemble_signal` mengubah sinyal jadi **NEUTRAL** dan menambah alasan `[RR]`. Helper target: `_above_targets` (BUY) / `_below_targets` (SELL).
- **TP2** = Entry ± (jarak SL x `RRR_TP2`).
- **Urutan TP1/TP2 dijamin** (di `_rr_targets`): TP1 selalu target terdekat. Bila target struktur melewati proyeksi TP2 (1:1.4), TP1/TP2 **ditukar**. BUY: `Entry < TP1 < TP2`; SELL: `Entry > TP1 > TP2`. Uji: `TestLevelsRRR::test_buy/sell_swaps_tp_when_target_beyond_tp2_projection`.
- **Filter EMA20 H1 (Fix R5)**: sinyal BUY/SELL **wajib alignment tren H1** — BUY hanya bila `price > EMA20(H1)`, SELL hanya bila `price < EMA20(H1)` (dicek di `assemble_signal` sebelum aksi final; bila EMA20 tidak tersedia, filter dilewati / graceful degradation). Pelanggaran → aksi jadi NEUTRAL + alasan `[EMA] Ditahan`. Alasan ini anti counter-trend SMC murni yang win rate-nya rendah (backtest: ~26% SELL, ~45% BUY → alignment EMA20 menaikkan ke ~62%).
- **Filter Trend Induk (BTC Market Regime)**: BTC sebagai kompas makro — saat BTC bearish di salah satu timeframe yang dipantau, **sinyal BUY dilarang** (diturunkan jadi NEUTRAL + alasan `[BTC]`). Verdict per TF dari `engine._regime_tf` (struktur CHoCH/LH+LL ATAU price < EMA20 < EMA50 H1), agregasi di `engine.btc_regime`: bearish bila ≥ 1 TF bearish. Timeframe default `BTC_REGIME_TIMEFRAMES = 15m,1h,4h,1d`, aktif via `BTC_REGIME_ENABLED` (default true); data BTC tidak tersedia → filter dilewati (graceful degradation, jangan jadikan BTC wajib). `bot.py` menghitung regime SEKALI per scan (hemat API) dan meneruskannya ke `assemble_signal(btc_regime_info=...)`. Dasar: audit 12-Aug-2026 — 3 sinyal BUY (PENGU/ETH/LINK) kena SL karena dump altcoin lebih dalam dari BTC (PENGU −4.8% vs BTC −1.4%) saat BTC H4/D1 bearish walaupun M15/H1 masih bullish (bounce M15 melawan tren H4/D1). Uji: `tests/test_engine.py::TestBtcRegime`.
- **Filter kualitas (14-Aug-2026, hasil backtest 2 jendela x 7 hari)**: `CONFIDENCE_MIN` (default 0 = mati) — sinyal berarah dengan confidence di bawah ambang diturunkan jadi NEUTRAL + alasan `[Conf]`. **JANGAN diaktifkan**: validasi 2 jendela menunjukkan WR justru turun (57.4%→56.7%, 65.5%→63.8%) — hipotesis bucket conf 60-69 = 43% WR ternyata sampel kecil (3W/4L), tidak robust. `MAX_ATR_REL` (default 0.03) — koin dengan ATR H1 / harga melebihi ambang DILEWATI dari scan (di `bot._fetch_candidate` & `backtest.run_backtest`); validasi: WR naik tipis di 1 jendela (63.8%→65.7%) tanpa menurunkan jendela lain, aman dipertahankan.
- Jangan menghapus cek RRR — ini penjaga minimal risk/reward; bila cek diubah, sesuaikan `tests/test_engine.py::TestLevelsRRR`.

## 3. Filter aset (aturan baku — jangan diubah tanpa alasan)

Di `bot.py`, pasangan kandidat difilter lewat `_eligible_pair()` sebelum diskoring. Tiga lapis:

1. **Stablecoin** — set `STABLECOINS` di `bot.py` (USDT, USDC, DAI, USDD, FDUSD, RLUSD, XUSD, EURS/EURC/EUR/EURI/EURIT, FRAX, BFUSD, dll).
2. **Leveraged token** — `SKIP_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")` (mis. BTCUP/BTCDOWN).
3. **Token saham/ETF Bitget (Bitget Shares)** — `US_STOCK_TICKERS` + `_is_stock_token()`: base = ticker saham/ETF US atau `ticker + "B"` (NVDAB→NVDA, QQQB→QQQ, SPYB→SPY, GOOGLB→GOOGL, TSLAB→TSLA, SPCXB→SPCX, MUUB→MUU; langsung: MUB, BE).

> **Penting:** koin kripto asli yang berakhiran `B` (**BNB, ARB, SHIB, TRB, DGB, CKB, BB**) **tidak boleh** terkena filter — deteksi selalu via lookup ke `US_STOCK_TICKERS`, bukan sekadar cek suffix `B`. Jika ada token saham baru, tambahkan ticker polosnya (tanpa `B`) ke set.

## 4. Data: Bitget & Sumber Lain

- **MTF**: `get_klines_multi(symbol)` mengembalikan `{interval: [candle, ...]}` untuk `1d`, `4h`, `1h`, `15m` (konstanta `INTERVAL_1D/4H/1H/M15`, limit default `MTF_LIMITS`). Satu interval gagal → interval itu dilewati, analisa tetap jalan (sinyal di-skip).
- **Evaluasi presisi**: `get_klines_since(symbol, interval, since)` memakai `startTime` API — Bitget V2 membulatkan startTime ke bawah per granularity, sehingga candle yang memuat `since` bisa ikut dikembalikan; di sisi klien candle dengan `openTime < since` dibuang, jadi candle pertama = `openTime >= since` — candle yang memuat waktu entry ikut dihitung tanpa mengikutsertakan aksi harga pra-entry dari candle sebelumnya (Fix #4: TIDAK mundur 1 interval).
- `get_ticker_24h(symbol)` → `ticker_24h` di `bot.py` (1 panggilan agregat).
- CMC opsional (free tier): tanpa candle historis; bila kosong → fallback semua pasangan USDT by volume Bitget.
- `get_funding_rate` / `get_long_short_ratio` (futures): diprobe sekali murah; bila gagal → sentiment pakai Fear & Greed saja.
- Whale/on-chain opsional; gagal → kategori dilewati tanpa mempengaruhi kategori lain.

## 5. Evaluasi Sinyal & Riwayat (`evaluation.py`) — aturan baku

- **`add_signals_today()`** dipanggil di `bot.py` **setelah pesan berhasil dikirim** — menyimpan sinyal terpilih sesi itu ke `data/history.json` (key sesi WIB `YYYY-MM-DD HH:MM`; kunci lama `YYYY-MM-DD` tetap didukung).
- **`build_recap()`** dijalankan **sebelum** briefing baru dikirim: membaca **sesi terakhir sebelum sesi sekarang** (robust terhadap hari/sesi kosong), mengambil **list candle M15 kronologis** via `fetch_fn` (dari `bitget.get_klines_since` — kline M15 sejak sesi sinyal, `_range_since` di `bot.py`), lalu menentukan status. Evaluasi di-walk **candle-per-candle dalam urutan waktu** (`_evaluate_candles`): TP menang bila tersentuh di candle yang tak menyentuh SL di candle-candle sebelumnya; SL menang bila tersentuh di candle yang juga menyentuh TP (urutan tak bisa dipastikan → SL konservatif). Jendela dibatasi `EVAL_MAX_HOURS` (24 jam) sejak sesi sinyal (`_within_window`) — harga bergerak lebih lama dianggap di luar cakupan day trade. Bila sesi terakhir tidak dapat dievaluasi (tanpa sinyal / semua fetch harga gagal), recap **mundur ke sesi lebih lama yang valid** (Fix #5).
- **Carry-over sinyal FLOATING (Fix carry-over, ketat 14-Aug-2026)**: sinyal yang masih FLOATING di sesi-sesi sebelumnya **TIDAK dihapus** dari antrean evaluasi — `build_recap` mengevaluasi ulang semua sesi di dalam jendela `EVAL_MAX_HOURS` (+ toleransi `CARRYOVER_GRACE_HOURS` 24 jam) dan menampilkannya sebagai seksi **`CARRY-OVER — POSISI AKTIF DARI SESI SEBELUMNYA`** di bawah rekap sesi utama. Seksi ini **HANYA berisi sinyal berstatus ⏳ FLOATING**: begitu sinyal mencapai status final (💰 TP1 / 🎯 TP2 / 🛡️ SL / ⏰ EXPIRED) ia **LANGSUNG keluar** dari antrean carry-over di sesi berikutnya (tidak diseret berulang). **Deduplikasi per symbol/base**: bila koin sama muncul di beberapa sesi (mis. RFXI 2 entry), hanya **1 sinyal TERBARU** (sesi paling akhir) yang ditampilkan — mencegah chat penuh duplikasi. Sinyal yang sudah tampil di sesi utama juga tidak diduplikasi ke seksi carry-over.
- **Anti sinyal berulang (Fix R4, `_apply_cooldown` di `bot.py`)**: bila base + arah + entry (toleransi `COOLDOWN_ENTRY_TOL_PCT` 0.5%) sudah disinyalkan pada `COOLDOWN_SESSIONS` sesi terakhir, sinyal diturunkan jadi NEUTRAL + alasan `[Cooldown]` (tetap tampil di WATCHLIST). Mencegah bot menyuruh re-entry level yang sama berulang sesi (mis. UTK 0.00795 diulang 7 sesi).
- **Presisi evaluasi**: `build_recap` meng-parse kunci sesi WIB ke datetime (`_session_since`) dan meneruskannya sebagai `since` ke `fetch_fn(pair, since)`. `bot._range_since()` mengambil candle M15 **setelah sesi sinyal** (bukan ticker 24j rolling yang bisa mencakup pergerakan harga SEBELUM entry). Fallback otomatis ke ticker 24j (`_ticker_range` → 1 candle sintetis) bila klines sejak-sesi gagal / kosong.
- Urutan cek status (`evaluate_signal`): **TP2 → TP1 → SL → Floating** (TP lebih dulu; lihat catatan kontrarian). BUY pakai `high` untuk TP dan `low` untuk SL; SELL kebalikannya.
- **Win rate** = % sinyal yang menyentuh TP1/TP2 dari **seluruh** sinyal yang dievaluasi (Floating ikut penyebut). Nilai SL/TP dari `history.json`.
- **Format recap**: statistik di baris terpisah (`🏆 Win rate` → `💰 TP1` → `🎯 TP2` → `🛡️ SL` → `⏳ Floating`), lalu pemisah `───`, lalu tiap sinyal = `#BASE AKSI` + `🔑 Entry $.. → <emoji> STATUS` + `📋 Hit <STATUS> di $..` (floating: `📋 Harga saat ini $..`). Harga acuan disimpan sebagai `ref` oleh `_evaluate()`; `STATUS_EMOJI`: TP2=🎯, TP1=💰, SL=🛡️, FLOATING=⏳.
- Recap **jangan menggagalkan scan**: fetch gagal → status `None` → sinyal itu dilewati; tak ada riwayat/semua sesi gagal → `build_recap` mengembalikan `None`.
- **CI commit-back**: runner di-reset tiap run, jadi workflow wajib men-*commit balik* `data/history.json` (step "Commit balik riwayat sinyal", `permissions: contents: write` + `concurrency` agar tidak race). Jangan pernah menambahkan `data/history.json` ke `.gitignore`.

## 6. Format pesan (baca `engine.format_message()`)

- Header seksi: `<b>📈 SINYAL LONG (BUY)</b>`, `<b>📉 SINYAL SHORT (SELL)</b>`, `<b>⚪ WATCHLIST (NEUTRAL)</b>` (seksi kosong di-skip).
- Baris meta briefing (dari `engine.meta_lines()`): `<b>🚨 NEW SIGNAL ALERTS 🚨</b>` (baris paling atas), `<b>📊 DAY TRADING BRIEFING — MTF S&R + SMC + FIBO + EMA</b>`, `🕐 <tanggal jam> WIB`, `⚙️ Analisa: Kompas H4/D1 → Zona H1 → Konfirmasi M15`, `🌐 Fear&Greed: N`.
- Tiap sinyal = blok dari `_signal_lines()`: baris judul `#BASE (SYMBOL)` → Entry → SL → TP1 → TP2 → perubahan 24j → alasan `📝` (baris pertama berprefix `📝` tanpa dash) → `💸` momentum → `📊 Skor` (total + breakdown SR/SMC/Fibo/EMA/Tek/Onch/Sent) → pemisah `───`.
- **Alasan wajib MTF (kelompok per timeframe)**: baris pertama `📝` = headline zona H1 (mis. "Demand Zone & Bullish OB H1 Tersentuh"). Alasan berikutnya dipakai `engine._group_reason_lines()`: tag `+ [H4]`/`+ [H1]`/`+ [M15]` ditulis **1x** sebagai header grup (indent 4 spasi); sub-alasan berindent **7 spasi + `- `**. Grup dgn 1 item → inline (`+ [H4] Tren utama Bullish`); banyak item → header lalu daftar `- ...`. Baris non-tag (momentum) dicetak ber-prefix `💸` sebagai baris terpisah. **Deduplikasi**: item terulang dari loop (FVG per-gap, Liquidity Sweep per-sweep) diringkas jadi 1 baris + jumlah `(xN)` — mis. `FVG bullish tervalidasi di bawah harga (x8)` — agar kalimat tidak dicetak berulang-ulang.
- Urutan header & baris sinyal adalah **kontrak visual** — ubah hanya bila diminta user. Format harga lewat `_fmt_price()` (≥1000: 0 desimal, ≥1: 2 desimal, <1: 6 desimal).
- Kirim memakai Telegram HTML parse mode (`telegram_sender.py`).
- **Pecah pesan >4000 karakter**: `telegram_sender._split_signal_blocks()` memotong **per blok koin** (batas judul `#BASE`) PLUS header seksi (📈/📉/⚪, Fix #3) agar tidak ada koin terpotong di tengah dan header WATCHLIST tidak terpisah dari koin NEUTRAL-nya; footer Disclaimer tetap di akhir sinyal koin terakhir. Jangan menggantinya dengan pemotongan string asal di tengah baris.

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
- **Bitget futures** (funding/L-S ratio) dapat diblokir region → fungsi mengembalikan `[]` / `None`, dan `bot.py` memakai `futures_ok` probe sekali di awal. **Jangan jadikan futures wajib** — bot harus tetap jalan hanya dengan spot + Fear & Greed.
- **Data MTF**: interval yang gagal dilewati; jika `15m`/`1h` tidak tersedia, `engine` memakai kompas + zona tanpa trigger (sinyal tetap bisa NEUTRAL).
- Whale & on-chain butuh API key opsional → `None` bila tidak dikonfigurasi.
- Pola ini wajib dipertahankan: satu sumber gagal ≠ seluruh scan gagal.

### Menjalankan & menguji
```powershell
venv\Scripts\python.exe -m pytest tests -v   # 217 test
venv\Scripts\python.exe bot.py               # scan nyata; tanpa kredensial → print konsol
```

## 8. Keamanan Kredensial
- `.env` di-ignore (`gitignore`) — jangan pernah commit token/key.
- Secrets GitHub Actions: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, opsional `CMC_API_KEY`, `ETHERSCAN_API_KEY`.
- Jangan print token/secret ke log.
