# BOT-Sinyal-Trading-V2

Bot Telegram **Daily Briefing sinyal trading crypto** (versi lanjutan) dengan multi-sumber data gratis dan indikator advance: **RSI, MACD, Order Block, SMC (BOS/CHoCH/FVG), Support & Resistance, whale proxy, on-chain, dan sentiment pasar (Fear & Greed)**.

> Dibangun dari pengalaman BOT-Sinyal-Trading v1 (CoinGecko only). v2 memakai
> Binance (candle/ticker) sebagai sumber utama teknikal + CoinMarketCap (ranking) +
> Fear & Greed (sentiment) + Etherscan/blockchain.info (on-chain proxy).

## Cara Kerja

Dijalankan otomatis oleh GitHub Actions (cron `0 0 * * *` = **07:00 WIB**):

1. Satu panggilan ticker 24j Binance (`data-api.binance.vision` — tidak geo-block, aman untuk runner AS).
2. Pilih top coin: daftar CoinMarketCap bila `CMC_API_KEY` diisi, else **semua pasangan USDT Binance**. Filter aset non-koin (stablecoin, leveraged token, token saham Binance) + likuiditas (`MIN_VOLUME_USD`), urut volume, ambil `TOP_COINS` (maks 250).
3. Tiap coin: candle 1d + 4h dari Binance → RSI, MACD, OB, FVG, struktur market, level S&R; funding rate & long/short ratio (bila futures terjangkau — diprobe sekali di awal via `get_funding_rate("BTCUSDT")`).
4. Data agregat: Fear & Greed Index, whale netflow ETH (Etherscan), statistik jaringan BTC (blockchain.info).
5. Skoring **berbobot 5 kategori** → BUY/SELL/NEUTRAL + confidence, SL/TP berbasis level S&R/OB.
6. **Evaluasi sinyal kemarin** (`data/history.json`): cek harga 24j terakhir tiap sinyal → status TP2/TP1/SL/Floating + win rate harian → disisipkan tepat sebelum briefing.
7. Kirim Daily Briefing (Top 5) ke Telegram (HTML parse mode), lalu simpan sinyal hari ini ke `history.json` (di-commit balik oleh GitHub Actions).

## Sumber Data

| Sumber | Dipakai untuk | Akses |
|---|---|---|
| Binance Spot (`data-api.binance.vision`) | ticker 24j, klines 1d/4h → indikator teknikal | publik, tanpa key |
| Binance Futures (`fapi.binance.com`) | funding rate, long/short ratio | opsional — dapat diblokir region |
| CoinMarketCap (`pro-api.coinmarketcap.com`) | ranking top coin | `CMC_API_KEY` (opsional, free tier) |
| alternative.me | Fear & Greed Index | publik, tanpa key |
| Etherscan | whale netflow ETH (proxy) | `ETHERSCAN_API_KEY` (opsional) |
| blockchain.info | statistik BTC (`n_tx_24h`) | publik, tanpa key |

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

## Struktur

```
bot.py                     # Entry point: kumpulkan data → skoring → kirim Telegram
config.py                  # Kredensial & parameter dari .env
engine.py                  # Skoring 5 kategori berbobot + format pesan HTML
evaluation.py              # Riwayat sinyal (history.json) + evaluasi/recap kemarin
telegram_sender.py         # Kirim pesan ke Telegram
data/
  _client.py               # HTTP client (retry, backoff, rate-limit)
  binance.py               # Klines, ticker 24j, funding, long/short ratio
  cmc.py                   # Top coins + market overview (free tier)
  sentiment.py             # Fear & Greed Index + skoring contrarian
  onchain.py               # Whale netflow ETH, statistik BTC (proxy)
  history.json             # Riwayat sinyal harian (di-commit balik oleh CI)
indicators/
  rsi.py                   # RSI Wilder
  macd.py                  # EMA 12/26 + signal 9 + histogram
  support_resistance.py    # Swing high/low, pivot, level terdekat
  smc.py                   # Order Block, FVG, BOS/CHoCH, struktur market
tests/                     # pytest
.github/workflows/daily.yml
```

## Skoring (bobot)

| Kategori | Bobot | Isi |
|---|---|---|
| Teknikal | 40% | RSI, MACD histogram, momentum 24j |
| SMC & S&R | 20% | OB, FVG, BOS/CHoCH, jarak ke support/resistance |
| Sentiment | 15% | Fear & Greed (contrarian), funding rate, long/short ratio |
| Whale | 15% | Netflow exchange ETH (proxy) |
| On-chain | 10% | Aktivitas jaringan BTC (jumlah tx) |

Skor tiap kategori -1.0..+1.0. **BUY** ≥ `BUY_THRESHOLD` (0.10), **SELL** ≤ `SELL_THRESHOLD` (-0.10), selain itu NEUTRAL. Confidence `clamp(25, 95, 55 + |skor|*40)`. Konvensi **kontrarian**: RSI > 70 / funding tinggi / Fear & Greed ekstrem = negatif (antisipasi pullback). SL/TP diambil dari level S&R/OB terdekat, fallback ke persentase statis.

## Filter aset (bukan koin kripto yang valid)

Di `bot._eligible_pair()`:

- **Stablecoin** (`STABLECOINS`): USDT, USDC, DAI, BUSD, TUSD, USDD, FDUSD, EURS/EURC/EUR/EURI/EURIT, RLUSD, XUSD, FRAX, BFUSD, dsb.
- **Leveraged token** (`SKIP_SUFFIXES`): pasangan berakhiran `UP`, `DOWN`, `BULL`, `BEAR` (mis. BTCUP/BTCDOWN).
- **Token saham/ETF Binance (Binance Shares)** (`US_STOCK_TICKERS` + `_is_stock_token()`): base berbasis ticker saham/ETF US, umumnya berakhiran `B` — mis. `NVDAB`→NVDA, `QQQB`→QQQ, `SPYB`→SPY, `GOOGLB`→GOOGL, `TSLAB`→TSLA, `MUB` (ETF langsung), `BE`. Deteksi: `base == ticker` atau `base = ticker + "B"`. Koin kripto asli yang berakhiran `B` (BNB, ARB, SHIB, TRB, DGB, CKB, BB) **tetap diproses**.

## Format pesan Telegram

`engine.format_message()` — sinyal dikelompokkan per header, tiap sinyal memakai `#hashtag`, alasan `📝` dicetak dengan baris pertama (indikator utama) tanpa dash dan baris berikutnya diindentasi `    - `, dan baris `📊 Skor` merangkum semua komponen:

```
📊 DAILY BRIEFING — SINYAL TRADING v2
🕐 Friday, 07 Aug 2026, 16:08 WIB
🌐 Fear&Greed: 29

📈 SINYAL LONG (BUY)

#LIT (LITUSDT) — BUY · Confidence 73%
🔑 Entry: $0.743000
🛡️ SL: $0.724000
🎯 TP1: $0.819000
🎯 TP2: $0.895000
💹 24j: +5.24%
📝 MACD bullish (histogram +)
    - Momentum 24j +5.2%
    - Struktur bullish (BOS/higher high)
    - OB support 0.59
    - FVG bullish di bawah harga
    - Support dekat (2.6%)
    - Fear&Greed 29
📊 Skor: +0.45  (Tek +0.55 · SMC +0.85 · Sent +0.40 · Whale +0.00 · Onch +0.00)

───

📉 SINYAL SHORT (SELL)

#XRP (XRPUSDT) — SELL · Confidence 59%
🔑 Entry: $1.03
🛡️ SL: $1.07
🎯 TP1: $0.948520
🎯 TP2: $0.866040
💹 24j: -2.31%
📝 RSI 37 mendekati oversold
    - MACD bearish (histogram -)
    - Struktur bearish (CHoCH/lower low)
    - FVG bearish di atas harga
    - Fear&Greed 29
📊 Skor: -0.12  (Tek -0.25 · SMC -0.40 · Sent +0.40 · Whale +0.00 · Onch +0.00)

───

⚠️ Disclaimer: Sinyal berbasis indikator otomatis & data publik. Bukan saran finansial. Selalu lakukan riset sendiri (DYOR).
```

Sinyal NEUTRAL (bila ada) dikelompokkan di header `⚪ WATCHLIST (NEUTRAL)`.

## Evaluasi Sinyal Kemarin (Daily Recap)

`evaluation.py` + `data/history.json`:

- **Penyimpanan riwayat**: tiap run menyimpan sinyal terpilih hari itu (Symbol, Direction, Entry, SL, TP1, TP2, Timestamp) dengan key tanggal WIB (`YYYY-MM-DD`). Karena runner GitHub Actions di-reset tiap run, workflow meng-*commit balik* `history.json` ke repo (step "Commit balik riwayat sinyal").
- **Evaluasi sebelum briefing**: pada run berikutnya, bot membaca hari terakhir sebelum hari ini, mengambil **high/low/current 24j** tiap pair dari Binance, lalu menentukan status tiap sinyal dengan urutan cek **TP2 → TP1 → SL → Floating**.
- **Win rate harian** = % sinyal yang menyentuh TP1/TP2 dari seluruh sinyal yang dievaluasi (ditampilkan juga jumlah TP2/TP1/SL/Floating).
- Recap disisipkan tepat sebelum blok `📊 DAILY BRIEFING — SINYAL TRADING v2`:

```
📊 EVALUASI SINYAL KEMARIN — 06 Agu 2026
🏆 Win rate: 60% (3/5)  ·  🎯 TP2: 1 · ✅ TP1: 2 · ❌ SL: 0 · ⏳ Floating: 2

#BTC BUY · Entry $104,000 → 🎯 TP2
#XRP SELL · Entry $1.03 → ❌ SL
#LIT BUY · Entry $0.74 → ⏳ Floating
```

Bila belum ada riwayat (hari pertama) atau semua data harga gagal diambil, recap dilewati tanpa menggagalkan scan.

## Catatan penting

- **Binance futures** (funding/L-S ratio) dapat diblokir region tertentu. Bila tidak terjangkau, bot otomatis memakai Fear & Greed saja — tidak pernah gagal total.
- **CMC free tier**: data delay, tanpa candle historis → hanya untuk ranking. Candle tetap dari Binance.
- **Whale & on-chain** adalah *proxy* data gratis, bukan level Glassnode/Santiment. Parameter `WHALE_MIN_USD` / `WHALE_LOOKBACK_HOURS` di `.env`.

## Disclaimer

Sinyal berbasis indikator otomatis & data publik — bukan saran finansial. Selalu lakukan riset sendiri (DYOR).
