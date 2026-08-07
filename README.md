# BOT-Sinyal-Trading-V2

Bot Telegram **Daily Briefing sinyal trading crypto** (versi lanjutan) dengan multi-sumber data gratis dan indikator advance: **RSI, MACD, Order Block, SMC (BOS/CHoCH/FVG), Support & Resistance, whale proxy, on-chain, dan sentiment pasar (Fear & Greed)**.

> Dibangun dari pengalaman BOT-Sinyal-Trading v1 (CoinGecko only). v2 memakai
> Binance (candle) sebagai sumber utama teknikal + CoinMarketCap (ranking) +
> Fear & Greed (sentiment) + Etherscan/blockchain.info (on-chain proxy).

## Cara Kerja

Dijalankan otomatis oleh GitHub Actions (cron `0 0 * * *` = **07:00 WIB**):

1. Satu panggilan ticker 24j Binance (`data-api.binance.vision` — tidak geo-block, aman untuk runner AS).
2. Pilih top coin: daftar CoinMarketCap bila `CMC_API_KEY` diisi, else **semua pasangan USDT Binance**. Filter stablecoin/leveraged token + likuiditas (`MIN_VOLUME_USD`), urut volume, ambil `TOP_COINS` (maks 250).
3. Tiap coin: candle 1d + 4h dari Binance → RSI, MACD, OB, FVG, struktur market, level S&R; funding rate & long/short ratio (bila futures terjangkau).4. Data agregat: Fear & Greed Index, whale netflow ETH (Etherscan), statistik jaringan BTC (blockchain.info).
5. Skoring **berbobot 5 kategori** → BUY/SELL/NEUTRAL + confidence, SL/TP berbasis level S&R/OB.
6. Kirim Daily Briefing (Top 5) ke Telegram (HTML).

## Setup

1. Bot di [@BotFather](https://t.me/BotFather) → salin token; cek chat ID via [@userinfobot](https://t.me/userinfobot).
2. Salin `.env.example` → `.env`, isi minimal:
   ```
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_CHAT_ID=<chat id>
   ```
3. (Opsional) kunci gratis:
   - `CMC_API_KEY` — ranking top coin dari CoinMarketCap.
   - `ETHERSCAN_API_KEY` / `BSCSCAN_API_KEY` — whale transfer proxy.
4. Uji lokal: `venv\Scripts\python.exe bot.py` (tanpa kredensial → hasil dicetak ke konsol).
5. Push ke GitHub + tambahkan secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (opsional: `CMC_API_KEY`, `ETHERSCAN_API_KEY`).

## Struktur

```
bot.py                     # Entry point: kumpulkan data → skoring → kirim Telegram
config.py                  # Kredensial & parameter dari .env
engine.py                  # Skoring 5 kategori berbobot + format pesan HTML
telegram_sender.py         # Kirim pesan ke Telegram
data/
  _client.py               # HTTP client (retry, backoff, rate-limit)
  binance.py               # Klines, ticker 24j, funding, long/short ratio
  cmc.py                   # Top coins + market overview (free tier)
  sentiment.py             # Fear & Greed Index + skoring contrarian
  onchain.py               # Whale netflow ETH, statistik BTC (proxy)
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

Skor tiap kategori -1.0..+1.0. **BUY** ≥ `BUY_THRESHOLD`, **SELL** ≤ `SELL_THRESHOLD`, selain itu NEUTRAL. Confidence `55 + |skor|*40` (25–95). RSI > 70 / funding tinggi / Fear & Greed ekstrem = kontrarian (bearish).

## Catatan penting

- **Binance futures** (funding/L-S ratio) dapat diblokir region tertentu. Bila tidak terjangkau, bot otomatis memakai Fear & Greed saja — tidak pernah gagal total.
- **CMC free tier**: data delay, tanpa candle historis → hanya untuk ranking. Candle tetap dari Binance.
- **Whale & on-chain** adalah *proxy* data gratis, bukan level Glassnode/Santiment. Daftar alamat bisa diubah via `ONCHAIN_TRACKED_ADDRESSES`.

## Disclaimer

Sinyal berbasis indikator otomatis & data publik — bukan saran finansial. Selalu lakukan riset sendiri (DYOR).
