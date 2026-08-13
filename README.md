# BOT-Sinyal-Trading-V2

Bot Telegram **Day Trading Briefing sinyal trading crypto** (versi lanjutan) dengan
analisa **Multi-Timeframe (MTF) berbasis S&R Kompas + Smart Money Concept (SMC) + Supply & Demand**:
**RSI, MACD, Order Block, FVG, BOS/CHoCH, Supply/Demand Zone, EQH/EQL, Liquidity Sweep,
Support & Resistance, Fibonacci Golden Zone (0.5/0.618/0.786), EMA 20/50 dynamic S/R,
whale proxy, on-chain, dan sentiment pasar (Fear & Greed)**.

> Dibangun dari pengalaman BOT-Sinyal-Trading v1 (CoinGecko only). v2 memakai
> Bitget (candle/ticker) sebagai sumber utama teknikal + CoinMarketCap (ranking) +
> Fear & Greed (sentiment) + Etherscan/blockchain.info (on-chain proxy).

## Cara Kerja

Dijalankan otomatis **2x sehari** — jadwal dipicu dari luar oleh **Cron-Job.org**
(cron `30 6 * * *` UTC = **13:30 WIB** dan `0 12 * * *` UTC = **19:00 WIB**)
melalui API `workflow_dispatch` GitHub Actions (cron internal GitHub dihapus agar
tidak ada pemicu ganda):

1. Satu panggilan ticker 24j Bitget (`https://api.bitget.com/api/v2/spot/market/tickers` — publik, tanpa key).
2. Pilih top coin: daftar CoinMarketCap bila `CMC_API_KEY` diisi, else **semua pasangan USDT Bitget**. Filter aset non-koin (stablecoin, leveraged token, token saham) + likuiditas (`MIN_VOLUME_USD`), urut volume, ambil `TOP_COINS` (maks 250).
3. Tiap coin diambil klines **4 timeframe** untuk analisa MTF:
   - **D1 & H4** → *kompas* (tren utama & struktur SMC BOS/CHoCH skala besar).
   - **H1** → *pemetaan* zona institusional (S&D, OB, FVG, EQH/EQL, Liquidity Sweep, S&R pivot/swing). Entry/SL/TP dipetakan dari zona H1.
   - **M15** → *pelatuk* konfirmasi eksekusi (RSI / MACD cross / momentum / BOS M15).
   - Funding rate & long/short ratio (bila futures terjangkau — diprobe sekali di awal via `get_funding_rate("BTCUSDT")`).
4. Data agregat: Fear & Greed Index, whale netflow ETH (Etherscan), statistik jaringan BTC (blockchain.info).
5. Skoring **berbobot** (prioritas S&R kompas + konfluensi SMC/Fibo/EMA) → BUY/SELL/NEUTRAL + confidence. **Aturan kompas:** H4 bullish → HANYA sinyal BUY; H4 bearish → HANYA SELL. Sinyal tervalidasi bila M15 searah H4/D1 (histogram MACD perlu konfirmasi 3 bar beruntun **dan** struktur M15 searah, anti bounce 30-45 menit) **dan** harga menyentuh zona SMC/S&D H1 **dan** alignment EMA20 H1 (BUY: price>EMA20, SELL: price<EMA20). **Aturan RRR:** level Entry/SL/TP wajib memenuhi Risk-to-Reward minimal (lihat [Risk-to-Reward Ratio](#risk-to-reward-ratio-rrr)).
6. **Evaluasi sinyal sesi sebelumnya** (`data/history.json`): baca riwayat sesi terakhir sebelum sesi ini (termasuk sesi pagi yang sama), walk **candle M15 sejak sesi sinyal secara berurutan** (jendela `EVAL_MAX_HOURS` 24 jam) tiap sinyal → status TP2/TP1/SL/Floating + win rate → dikirim sebagai **pesan Telegram terpisah (History Review)**.
7. Kirim **2 pesan Telegram** (HTML parse mode): pesan evaluasi (History Review) lalu Day Trading Briefing (Top 5) yang berakhir dengan disclaimer. Setelah terkirim, simpan sinyal sesi ini ke `history.json` (di-commit balik oleh GitHub Actions). Sinyal yang mengulang setup sesi-sesi terakhir (base + arah + entry nyaris sama) diturunkan ke NEUTRAL oleh **anti sinyal berulang** (`_apply_cooldown`, toleransi 0.5%, 2 sesi terakhir).

## Sumber Data

| Sumber | Dipakai untuk | Akses |
|---|---|---|
| Bitget Spot V2 (`api.bitget.com/api/v2/spot/market/*`) | ticker 24j, klines 1d/4h/1h/15m → indikator MTF | publik, tanpa key |
| Bitget Futures V2 (`api.bitget.com/api/v2/mix/market/*`) | funding rate, long/short ratio | opsional — dapat diblokir region |
| CoinMarketCap (`pro-api.coinmarketcap.com`) | ranking top coin | `CMC_API_KEY` (opsional, free tier) |
| alternative.me | Fear & Greed Index | publik, tanpa key |
| Etherscan | whale netflow ETH (proxy) | `ETHERSCAN_API_KEY` (opsional) |
| blockchain.info | statistik BTC (`n_tx_24h`) | publik, tanpa key |

Symbol Bitget: **Spot** memakai `BTCUSDT`, **Futures** (USDT-M Perpetual) memakai
`BTCUSDT_UMCBL` — konversi transparan di `data/bitget.py` (`to_spot_symbol` /
`to_futures_symbol`). Semua kline memakai `granularity` Bitget yang sesuai
(spot: `15min/1h/4h/1day`; futures: `15m/1H/4H/1D`).

Semua data via `data/_client.py` (retry + backoff + handling HTTP 429). Satu sumber gagal **tidak** menggagalkan seluruh scan (graceful degradation).

## Setup

1. Bot di [@BotFather](https://t.me/BotFather) → salin token; cek chat ID via [@userinfobot](https://t.me/userinfobot).
2. Salin `.env.example` → `.env`, isi minimal:
   ```
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_CHAT_ID=<chat id>
   ```
3. (Opsional) kunci gratis:
   - `CMC_API_KEY` — ranking top coin dari CoinMarketCap.
   - `ETHERSCAN_API_KEY` — whale transfer proxy.
4. Uji lokal: `venv\Scripts\python.exe bot.py` (tanpa kredensial → hasil dicetak ke konsol).
5. Push ke GitHub + tambahkan secrets Actions: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (opsional: `CMC_API_KEY`, `ETHERSCAN_API_KEY`).
6. (Jadwal harian) Buat 2 cron job di [Cron-Job.org](https://cron-job.org) yang men-trigger workflow `daily.yml` via API `workflow_dispatch` (URL, header & body lihat komentar di `.github/workflows/daily.yml`): `30 6 * * *` UTC (13:30 WIB) dan `0 12 * * *` UTC (19:00 WIB).

## Struktur

```
bot.py                     # Entry point: kumpulkan data MTF → skoring → kirim Telegram (termasuk anti sinyal berulang)
backtest.py                # Backtest offline: walk M15 tanpa look-ahead → win rate/EV per jendela (tuning RRR/EMA20)
_exp.py                    # Eksperimen tuning parameter (RRR/EMA) atas data histori
config.py                  # Kredensial & parameter dari .env
engine.py                  # Skoring MTF (Kompas H4/D1 → Zona H1 → Pelatuk M15) + format pesan HTML
evaluation.py              # Riwayat sinyal (history.json) + evaluasi/recap sesi sebelumnya
telegram_sender.py         # Kirim pesan ke Telegram
data/
  _client.py               # HTTP client (retry, backoff, rate-limit)
  bitget.py                # Klines MTF (1d/4h/1h/15m), ticker 24j, funding, long/short ratio
  cmc.py                   # Top coins + market overview (free tier)
  sentiment.py             # Fear & Greed Index + skoring contrarian
  onchain.py               # Whale netflow ETH, statistik BTC (proxy)
  history.json             # Riwayat sinyal per sesi (di-commit balik oleh CI)
indicators/
  rsi.py                   # RSI Wilder
  macd.py                  # EMA 12/26 + signal 9 + histogram + histogram series (cross)
  ema.py                   # EMA 20/50 dynamic S/R + deteksi pullback (analyze_ema)
  fibonacci.py             # Fib level 0.5/0.618/0.786 + Golden Zone (analyze_fibonacci)
  support_resistance.py    # Swing high/low, pivot, level terdekat
  smc.py                   # Order Block, FVG, BOS/CHoCH, EQH/EQL, Liquidity Sweep
  supply_demand.py         # Supply & Demand Zone (base + pause + impuls)
tests/                     # pytest (217 kasus)
.github/workflows/daily.yml
```

## Skoring (bobot)

| Kategori | Bobot | Isi |
|---|---|---|
| S&R (kompas utama) | 35% | Tren struktur H4/D1 (BOS/CHoCH skala besar ±0.35, fallback D1 ±0.25) + harga di/dekat Demand-Supply zone H1 (±0.25/±0.15) + Support/Resistance dekat (±0.20) + breakout key level (±0.20) |
| SMC (konfluensi) | 20% | Order Block (±0.35), FVG (±0.25), Liquidity Sweep (±0.40) — hanya komponen **searah kompas** (setup campuran tidak dinilai); tiap tipe dinilai **sekali** (kehadiran, bukan per-gap) |
| Fibonacci | 15% | Golden Zone 0.5/0.618/0.786 (±0.45, dekat ≤1%: ±0.20) + konfluensi Key Level S&R (+0.25) / Order Block (+0.20); arah ikut kompas |
| EMA 20/50 | 15% | Dynamic S/R: trend (±0.30) + pullback ke EMA 20 ≤0.5% (±0.30) + RSI hook 30-40 naik / 60-70 turun (±0.40); bila arah trend EMA **berlawanan kompas**, skor dinetralkan (0) |
| Teknikal (M15) | 8% | RSI (contrarian), MACD (cross > histogram), BOS M15, momentum 24j (contrarian) |
| On-chain / Whale | 5% | Netflow ETH (proxy, ETH) & aktivitas BTC (BTC) — kategori opsional (None = dilewati) |
| Sentimen | 2% | Fear & Greed (contrarian), funding rate, long/short ratio |

Skor tiap kategori -1.0..+1.0. **BUY** ≥ `BUY_THRESHOLD` (0.10), **SELL** ≤ `SELL_THRESHOLD` (-0.10). **Aturan kompas MTF:** H4 bullish → HANYA BUY, H4 bearish → HANYA SELL (D1 sebagai fallback). Sinyal tervalidasi bila M15 searah kompas **dan** harga menyentuh zona SMC/S&D H1. Confidence `clamp(25, 95, 55 + |skor|*40)`. Total skor **dinormalisasi** ke jumlah bobot yang benar-benar dipakai (On-chain dilewati bila tidak berlaku/tersedia). Entry/SL/TP dipetakan dari zona H1 (SL di luar Demand/Supply zone/OB; TP di level S&R H1), fallback persentase statis bila tak ada zona.

## Risk-to-Reward Ratio (RRR)

Level Entry/SL/TP di `engine._levels_mtf()` dihitung dengan **RRR wajib minimal** (parameter di `config.py`, bisa via `.env`):

| Parameter | Default | Arti |
|---|---|---|
| `RRR_MIN` | 0.7 | TP1 minimal = `RRR_MIN` x jarak SL (1:0.7) |
| `RRR_TP2` | 1.4 | TP2 proyeksi = `RRR_TP2` x jarak SL (1:1.4) |
| `SL_BUFFER_PCT` | 0.003 | Buffer 0.3% SL di luar zona Demand/Supply terdekat |
| `SL_MIN_DIST_PCT` | 0.017 | Jarak SL minimal dari Entry (1.7%) agar tidak tersapu noise |
| `SL_ATR_MULT` | 1.2 | Pengali ATR(H1) untuk SL dinamis volatil: jarak SL = `max(SL_MIN_DIST_PCT, SL_ATR_MULT x ATR/price)` |

Alur perhitungan (`_levels_mtf` → `_rr_targets`):

1. **SL** = zona Demand/Supply H1 terdekat **+ buffer 0.3%** di luar zona (BUY: Demand/Support di bawah harga; SELL: Supply/Resistance di atas harga). Bila tak ada zona → fallback persentase statis.
2. **Jarak SL (%)** dihitung dari Entry.
3. **TP1** = target struktur H1 terdekat (swing high/low, zona Supply/Demand) **dengan syarat** jarak TP1 (%) ≥ `RRR_MIN` x jarak SL (%). 
4. Bila target H1 terdekat **terlalu dekat** (TP1 < `RRR_MIN` x SL): paksa TP1 = Entry ± (jarak SL x `RRR_MIN`) — **hanya bila tidak terhalang zona Supply/Demand kuat** di antara Entry dan proyeksi TP1. Zona yang **berisi Entry tidak dianggap terhalang** (BUY: harga meninggalkan zona ke atas; SELL: ke bawah). Bila terhalang → `_levels_mtf` mengembalikan `None` → **sinyal di-reject menjadi NEUTRAL** (alasan `[RR]` ditambahkan).
5. **TP2** = Entry ± (jarak SL x `RRR_TP2`).
6. **Urutan TP dijamin** (`_rr_targets`): TP1 selalu target **terdekat**. Bila target struktur H1 melewati proyeksi TP2 (1:1.4), posisi TP1/TP2 **ditukar**. BUY: `Entry < TP1 < TP2`; SELL: `Entry > TP1 > TP2`.
7. **Filter alignment EMA20 H1**: BUY hanya bila `price > EMA20(H1)`, SELL hanya bila `price < EMA20(H1)` — entry counter-trend SMC murni (menembus EMA20 melawan tren H1) ditolak jadi NEUTRAL dengan alasan `[EMA]` (anti sinyal win rate rendah).
8. **Filter Trend Induk (BTC Market Regime)**: saat BTC bearish di salah satu timeframe yang dipantau (struktur CHoCH / EMA 20<50 pada `15m/1h/4h/1d`, default), **sinyal BUY dilarang** → diturunkan jadi NEUTRAL + alasan `[BTC]`. Regime dihitung sekali per scan (`engine.btc_regime`, data BTC dari Bitget; gagal → filter dilewati / graceful degradation). Dasar: audit 12-Aug-2026 — 3 BUY (PENGU/ETH/LINK) kena SL karena dump altcoin lebih dalam dari BTC (PENGU −4.8% vs BTC −1.4%) saat BTC H4/D1 bearish meski M15/H1 masih bullish. Parameter: `BTC_REGIME_ENABLED` (default `true`), `BTC_REGIME_TIMEFRAMES` (default `15m,1h,4h,1d`).

> Nilai 0.7/1.4 hasil **backtest 3 jendela independen x 7 hari** (`backtest.py`): win rate naik ~41% → ~60% (61.8/55.0/61.4%) dan EV per trade tetap positif tipis. Target dekat = eksekusi cepat, sesuai day trading.

Contoh BUY: Entry $102, SL $99.70 (Demand $100 − 0.3%), jarak SL 2.30% → TP1 minimal $103.64 (1:0.7), TP2 $105.28 (1:1.4).

## Filter aset (bukan koin kripto yang valid)

Di `bot._eligible_pair()`:

- **Stablecoin** (`STABLECOINS`): USDT, USDC, DAI, BUSD, TUSD, USDD, FDUSD, EURS/EURC/EUR/EURI/EURIT, RLUSD, XUSD, FRAX, BFUSD, dsb.
- **Leveraged token** (`SKIP_SUFFIXES`): pasangan berakhiran `UP`, `DOWN`, `BULL`, `BEAR` (mis. BTCUP/BTCDOWN).
- **Token saham/ETF (Binance Shares style)** (`US_STOCK_TICKERS` + `_is_stock_token()`): base berbasis ticker saham/ETF US, umumnya berakhiran `B` — mis. `NVDAB`→NVDA, `QQQB`→QQQ, `SPYB`→SPY, `GOOGLB`→GOOGL, `TSLAB`→TSLA, `MUB` (ETF langsung), `BE`. Deteksi: `base == ticker` atau `base = ticker + "B"`. Koin kripto asli yang berakhiran `B` (BNB, ARB, SHIB, TRB, DGB, CKB, BB) **tetap diproses**.

## Format pesan Telegram

`engine.format_message()` — baris paling atas = **alert header** `🚨 NEW SIGNAL ALERTS 🚨`, lalu judul briefing (S&R + SMC + FIBO + EMA). Sinyal dikelompokkan per header, tiap sinyal memakai `#hashtag`. Alasan `📝` dicetak dengan baris pertama (headline zona SMC/S&D H1) tanpa dash; alasan selanjutnya **dikelompokkan per timeframe** — tag `+ [H4]`/`+ [H1]`/`+ [M15]` hanya ditulis 1x sebagai header grup, sub-alasan diindentasi `- ` (dengan 7 spasi) di bawah grup yang sama. **Deduplikasi alasan**: skor SMC **berbasis kehadiran** — FVG dan Liquidity Sweep dinilai **sekali per tipe** (banyak FVG searah = +0.25 sekali), sehingga alasan tidak lagi tercetak berulang-ulang; ringkasan `(xN)` tetap ada sebagai jaring pengaman untuk alasan duplikat lain. Baris momentum dipisahkan emoji `💸` tepat sebelum `📊 Skor`:

```
🚨 NEW SIGNAL ALERTS 🚨
📊 DAY TRADING BRIEFING — MTF S&R + SMC + FIBO + EMA
🕐 Friday, 07 Aug 2026, 13:30 WIB
⚙️ Analisa: Kompas H4/D1 → Zona H1 → Konfirmasi M15
🌐 Fear&Greed: 29

📈 SINYAL LONG (BUY)

#BTC (BTCUSDT) — BUY · Confidence 73%
🔑 Entry: $65,263
🛡️ SL: $64,064
🎯 TP1: $70,484
🎯 TP2: $75,705
💹 24j: +1.92%
📝 Demand Zone & Bullish OB H1 Tersentuh
    + [H4] Tren utama Bullish (higher high)
    + [H1] 
       - Harga masuk Demand Zone
       - Bullish OB di bawah harga
       - FVG bullish tervalidasi di bawah harga
       - Liquidity Sweep tereksekusi (EQL tersapu)
       - Support dekat (0.4%)
       - Resistance dekat (1.2%)
    + [M15] MACD Golden Cross & RSI Rebound
💸 Momentum 24j +1.9% | Fear&Greed 29
📊 Skor: +0.38  (SR +0.35 · SMC +1.00 · Fibo +0.45 · EMA +0.60 · Tek +0.30 · Onch +0.00 · Sent +0.40)

───

📉 SINYAL SHORT (SELL)

#XRP (XRPUSDT) — SELL · Confidence 59%
🔑 Entry: $1.03
🛡️ SL: $1.07
🎯 TP1: $0.948520
🎯 TP2: $0.866040
💹 24j: -2.31%
📝 Supply Zone & Bearish OB H1 Tersentuh
    + [H4] Tren utama Bearish (CHoCH skala besar)
    + [H1] 
       - Harga masuk Supply Zone
       - Bearish OB di atas harga
       - FVG tervalidasi di atas harga
    + [M15] MACD Death Cross & RSI Melemah
💸 Momentum 24j -2.3% | Fear&Greed 29
📊 Skor: -0.21  (SR -0.35 · SMC -0.85 · Fibo +0.00 · EMA -0.30 · Tek -0.30 · Onch +0.00 · Sent +0.40)

───

⚠️ Disclaimer: Sinyal berbasis indikator otomatis & data publik. Bukan saran finansial. Selalu lakukan riset sendiri (DYOR).
```

Sinyal NEUTRAL (bila ada) dikelompokkan di header `⚪ WATCHLIST (NEUTRAL)`.

Pesan briefing & recap yang panjangnya melebihi 4000 karakter dipotong **per blok koin** PLUS header seksi (`telegram_sender._split_signal_blocks`): setiap chunk berisi koin-koin **utuh** (tidak ada koin terpotong di tengah, mis. judul `#VIRTUAL` terpisah dari detailnya), dan header `⚪ WATCHLIST` tidak terpisah dari koin NEUTRAL-nya. Footer `⚠️ Disclaimer` selalu menempel di akhir sinyal koin terakhir (chunk terakhir).

## Evaluasi Sinyal Sesi Sebelumnya (Daily Recap)

`evaluation.py` + `data/history.json`:

- **Penyimpanan riwayat**: tiap sesi (2x sehari) menyimpan sinyal terpilih (Symbol, Direction, Entry, SL, TP1, TP2, Timestamp) dengan **key sesi WIB** `YYYY-MM-DD HH:MM` (kunci lama `YYYY-MM-DD` tetap didukung). Karena runner GitHub Actions di-reset tiap run, workflow meng-*commit balik* `history.json` ke repo.
- **Evaluasi sebelum briefing**: pada run berikutnya, bot membaca **sesi terakhir sebelum sesi sekarang** (termasuk sesi pagi yang sama), mengambil **candle M15 kronologis** tiap pair dari Bitget (jendela dibatasi `EVAL_MAX_HOURS` = 24 jam sejak sesi), lalu **walk candle-per-candle dalam urutan waktu** — TP menang bila tersentuh di candle yang tidak menyentuh SL sebelumnya; SL menang bila menyentuh keduanya di candle yang sama (urutan tak bisa dipastikan → konservatif SL). High/low diambil dari **kline M15 sejak sesi sinyal** (`get_klines_since`) — bukan ticker 24j rolling — sehingga pergerakan harga **sebelum** entry tidak ikut menentukan hasil; fallback ke ticker 24j (1 candle sintetis) bila data sejak-sesi tidak tersedia. Bila sesi terakhir gagal dievaluasi (tanpa sinyal / fetch gagal), recap **mundur ke sesi lebih lama yang valid**.
- **Win rate** = % sinyal yang menyentuh TP1/TP2 dari seluruh sinyal yang dievaluasi (ditampilkan juga jumlah TP1/TP2/SL/Floating).
- Recap dikirim sebagai **pesan Telegram terpisah** (History Review) sebelum blok `📊 DAY TRADING BRIEFING — MTF S&R + SMC + FIBO + EMA`:

```
📊 EVALUASI SINYAL SESI SEBELUMNYA — 07 Aug 2026 13:30
🏆 Win rate: 60% (3/5)
💰 TP1: 1
🎯 TP2: 2
🛡️ SL: 1
⏳ Floating: 1
───

#BTC BUY
🔑 Entry $104,000 → 🎯 TP2
📋 Hit TP2 di $112,000

#XRP SELL
🔑 Entry $1.03 → 🛡️ SL
📋 Hit SL di $1.070000

#LIT BUY
🔑 Entry $0.74 → ⏳ FLOATING
📋 Harga saat ini $0.752000
───
```

Bila belum ada riwayat (sesi pertama) atau semua sesi gagal diambil datanya, recap dilewati tanpa menggagalkan scan.

## Catatan penting

- **Bitget futures** (funding/L-S ratio) dapat diblokir region tertentu. Bila tidak terjangkau, bot otomatis memakai Fear & Greed saja — tidak pernah gagal total.
- **CMC free tier**: data delay, tanpa candle historis → hanya untuk ranking. Candle tetap dari Bitget.
- **Whale & on-chain** adalah *proxy* data gratis, bukan level Glassnode/Santiment. Parameter `WHALE_MIN_USD` / `WHALE_LOOKBACK_HOURS` di `.env`.

## Disclaimer

Sinyal berbasis indikator otomatis & data publik — bukan saran finansial. Selalu lakukan riset sendiri (DYOR).
