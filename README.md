# BOT-Sinyal-Trading-V2

Bot Telegram **Day Trading Briefing sinyal trading crypto** (versi lanjutan) dengan
analisa **Multi-Timeframe (MTF) berbasis Smart Money Concept (SMC) + Supply & Demand**:
**RSI, MACD, Order Block, FVG, BOS/CHoCH, Supply/Demand Zone, EQH/EQL, Liquidity Sweep,
Support & Resistance, whale proxy, on-chain, dan sentiment pasar (Fear & Greed)**.

> Dibangun dari pengalaman BOT-Sinyal-Trading v1 (CoinGecko only). v2 memakai
> Binance (candle/ticker) sebagai sumber utama teknikal + CoinMarketCap (ranking) +
> Fear & Greed (sentiment) + Etherscan/blockchain.info (on-chain proxy).

## Cara Kerja

Dijalankan otomatis **2x sehari** — jadwal dipicu dari luar oleh **Cron-Job.org**
(cron `30 6 * * *` UTC = **13:30 WIB** dan `0 12 * * *` UTC = **19:00 WIB**)
melalui API `workflow_dispatch` GitHub Actions (cron internal GitHub dihapus agar
tidak ada pemicu ganda):

1. Satu panggilan ticker 24j Binance (`data-api.binance.vision` — tidak geo-block, aman untuk runner AS).
2. Pilih top coin: daftar CoinMarketCap bila `CMC_API_KEY` diisi, else **semua pasangan USDT Binance**. Filter aset non-koin (stablecoin, leveraged token, token saham Binance) + likuiditas (`MIN_VOLUME_USD`), urut volume, ambil `TOP_COINS` (maks 250).
3. Tiap coin diambil klines **4 timeframe** untuk analisa MTF:
   - **D1 & H4** → *kompas* (tren utama & struktur SMC BOS/CHoCH skala besar).
   - **H1** → *pemetaan* zona institusional (S&D, OB, FVG, EQH/EQL, Liquidity Sweep, S&R pivot/swing). Entry/SL/TP dipetakan dari zona H1.
   - **M15** → *pelatuk* konfirmasi eksekusi (RSI / MACD cross / momentum / BOS M15).
   - Funding rate & long/short ratio (bila futures terjangkau — diprobe sekali di awal via `get_funding_rate("BTCUSDT")`).
4. Data agregat: Fear & Greed Index, whale netflow ETH (Etherscan), statistik jaringan BTC (blockchain.info).
5. Skoring **berbobot** (prioritas SMC + S&D) → BUY/SELL/NEUTRAL + confidence. **Aturan kompas:** H4 bullish → HANYA sinyal BUY; H4 bearish → HANYA SELL. Sinyal tervalidasi bila M15 searah H4/D1 **dan** harga menyentuh zona SMC/S&D H1. **Aturan RRR:** level Entry/SL/TP wajib memenuhi Risk-to-Reward minimal (lihat [Risk-to-Reward Ratio](#risk-to-reward-ratio-rrr)).
6. **Evaluasi sinyal sesi sebelumnya** (`data/history.json`): baca riwayat sesi terakhir sebelum sesi ini (termasuk sesi pagi yang sama), cek harga 24j terakhir tiap sinyal → status TP2/TP1/SL/Floating + win rate → dikirim sebagai **pesan Telegram terpisah (History Review)**.
7. Kirim **2 pesan Telegram** (HTML parse mode): pesan evaluasi (History Review) lalu Day Trading Briefing (Top 5) yang berakhir dengan disclaimer. Setelah terkirim, simpan sinyal sesi ini ke `history.json` (di-commit balik oleh GitHub Actions).

## Sumber Data

| Sumber | Dipakai untuk | Akses |
|---|---|---|
| Binance Spot (`data-api.binance.vision`) | ticker 24j, klines 1d/4h/1h/15m → indikator MTF | publik, tanpa key |
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
6. (Jadwal harian) Buat 2 cron job di [Cron-Job.org](https://cron-job.org) yang men-trigger workflow `daily.yml` via API `workflow_dispatch` (URL, header & body lihat komentar di `.github/workflows/daily.yml`): `30 6 * * *` UTC (13:30 WIB) dan `0 12 * * *` UTC (19:00 WIB).

## Struktur

```
bot.py                     # Entry point: kumpulkan data MTF → skoring → kirim Telegram
config.py                  # Kredensial & parameter dari .env
engine.py                  # Skoring MTF (Kompas H4/D1 → Zona H1 → Pelatuk M15) + format pesan HTML
evaluation.py              # Riwayat sinyal (history.json) + evaluasi/recap sesi sebelumnya
telegram_sender.py         # Kirim pesan ke Telegram
data/
  _client.py               # HTTP client (retry, backoff, rate-limit)
  binance.py               # Klines MTF (1d/4h/1h/15m), ticker 24j, funding, long/short ratio
  cmc.py                   # Top coins + market overview (free tier)
  sentiment.py             # Fear & Greed Index + skoring contrarian
  onchain.py               # Whale netflow ETH, statistik BTC (proxy)
  history.json             # Riwayat sinyal per sesi (di-commit balik oleh CI)
indicators/
  rsi.py                   # RSI Wilder
  macd.py                  # EMA 12/26 + signal 9 + histogram + histogram series (cross)
  support_resistance.py    # Swing high/low, pivot, level terdekat
  smc.py                   # Order Block, FVG, BOS/CHoCH, EQH/EQL, Liquidity Sweep
  supply_demand.py         # Supply & Demand Zone (base + pause + impuls)
tests/                     # pytest (88 kasus)
.github/workflows/daily.yml
```

## Skoring (bobot)

| Kategori | Bobot | Isi |
|---|---|---|
| SMC & S&D (MTF) | 40% | Kompas H4/D1 (BOS/CHoCH) + zona H1 (S&D, OB, FVG, EQH/EQL, Liquidity Sweep, S&R) |
| Teknikal (M15) | 20% | RSI, MACD (cross/histogram), BOS M15, momentum 24j |
| Sentiment | 15% | Fear & Greed (contrarian), funding rate, long/short ratio |
| Whale | 15% | Netflow exchange ETH (proxy) |
| On-chain | 10% | Aktivitas jaringan BTC (jumlah tx) |

Skor tiap kategori -1.0..+1.0. **BUY** ≥ `BUY_THRESHOLD` (0.10), **SELL** ≤ `SELL_THRESHOLD` (-0.10). **Aturan kompas MTF:** H4 bullish → HANYA BUY, H4 bearish → HANYA SELL (D1 sebagai fallback). Sinyal tervalidasi bila M15 searah kompas **dan** harga menyentuh zona SMC/S&D H1. Confidence `clamp(25, 95, 55 + |skor|*40)`. Entry/SL/TP dipetakan dari zona H1 (SL di luar Demand/Supply zone/OB; TP di level S&R H1), fallback persentase statis bila tak ada zona.

## Risk-to-Reward Ratio (RRR)

Level Entry/SL/TP di `engine._levels_mtf()` dihitung dengan **RRR wajib minimal** (parameter di `config.py`, bisa via `.env`):

| Parameter | Default | Arti |
|---|---|---|
| `RRR_MIN` | 1.5 | TP1 minimal = `RRR_MIN` x jarak SL (1:1.5) |
| `RRR_TP2` | 3.0 | TP2 proyeksi = `RRR_TP2` x jarak SL (1:3) |
| `SL_BUFFER_PCT` | 0.003 | Buffer 0.3% SL di luar zona Demand/Supply terdekat |

Alur perhitungan (`_levels_mtf` → `_rr_targets`):

1. **SL** = zona Demand/Supply H1 terdekat **+ buffer 0.3%** di luar zona (BUY: Demand/Support di bawah harga; SELL: Supply/Resistance di atas harga). Bila tak ada zona → fallback persentase statis.
2. **Jarak SL (%)** dihitung dari Entry.
3. **TP1** = target struktur H1 terdekat (swing high/low, zona Supply/Demand) **dengan syarat** jarak TP1 (%) ≥ `RRR_MIN` x jarak SL (%). 
4. Bila target H1 terdekat **terlalu dekat** (TP1 < `RRR_MIN` x SL): paksa TP1 = Entry ± (jarak SL x `RRR_MIN`) — **hanya bila tidak terhalang zona Supply/Demand kuat** di antara Entry dan proyeksi TP1. Bila terhalang → `_levels_mtf` mengembalikan `None` → **sinyal di-reject menjadi NEUTRAL** (alasan `[RR]` ditambahkan).
5. **TP2** = Entry ± (jarak SL x `RRR_TP2`).
6. **Urutan TP dijamin** (`_rr_targets`): TP1 selalu target **terdekat**. Bila target struktur H1 melewati proyeksi TP2 (1:3), posisi TP1/TP2 **ditukar**. BUY: `Entry < TP1 < TP2`; SELL: `Entry > TP1 > TP2`.

Contoh BUY: Entry $102, SL $99.70 (Demand $100 − 0.3%), jarak SL 2.30% → TP1 minimal $105.45 (1:1.5), TP2 $108.90 (1:3).

## Filter aset (bukan koin kripto yang valid)

Di `bot._eligible_pair()`:

- **Stablecoin** (`STABLECOINS`): USDT, USDC, DAI, BUSD, TUSD, USDD, FDUSD, EURS/EURC/EUR/EURI/EURIT, RLUSD, XUSD, FRAX, BFUSD, dsb.
- **Leveraged token** (`SKIP_SUFFIXES`): pasangan berakhiran `UP`, `DOWN`, `BULL`, `BEAR` (mis. BTCUP/BTCDOWN).
- **Token saham/ETF Binance (Binance Shares)** (`US_STOCK_TICKERS` + `_is_stock_token()`): base berbasis ticker saham/ETF US, umumnya berakhiran `B` — mis. `NVDAB`→NVDA, `QQQB`→QQQ, `SPYB`→SPY, `GOOGLB`→GOOGL, `TSLAB`→TSLA, `MUB` (ETF langsung), `BE`. Deteksi: `base == ticker` atau `base = ticker + "B"`. Koin kripto asli yang berakhiran `B` (BNB, ARB, SHIB, TRB, DGB, CKB, BB) **tetap diproses**.

## Format pesan Telegram

`engine.format_message()` — sinyal dikelompokkan per header, tiap sinyal memakai `#hashtag`. Alasan `📝` dicetak dengan baris pertama (headline zona SMC/S&D H1) tanpa dash; alasan selanjutnya **dikelompokkan per timeframe** — tag `+ [H4]`/`+ [H1]`/`+ [M15]` hanya ditulis 1x sebagai header grup, sub-alasan diindentasi `- ` (dengan 7 spasi) di bawah grup yang sama. Baris momentum dipisahkan emoji `💸` tepat sebelum `📊 Skor`:

```
📊 DAY TRADING BRIEFING — MTF SMC + S&D
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
       - FVG tervalidasi di bawah harga
       - Liquidity Sweep tereksekusi (EQL tersapu)
       - Support dekat (0.4%)
       - Resistance dekat (1.2%)
    + [M15] MACD Golden Cross & RSI Rebound
💸 Momentum 24j +1.9% | Fear&Greed 29
📊 Skor: +0.38  (Tek +0.30 · SMC +1.00 · Sent +0.40 · Whale +0.00 · Onch +0.00)

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
📊 Skor: -0.21  (Tek -0.30 · SMC -0.85 · Sent +0.40 · Whale +0.00 · Onch +0.00)

───

⚠️ Disclaimer: Sinyal berbasis indikator otomatis & data publik. Bukan saran finansial. Selalu lakukan riset sendiri (DYOR).
```

Sinyal NEUTRAL (bila ada) dikelompokkan di header `⚪ WATCHLIST (NEUTRAL)`.

Pesan briefing & recap yang panjangnya melebihi 4000 karakter dipotong **per blok koin** (`telegram_sender._split_signal_blocks`): setiap chunk berisi koin-koin **utuh** (tidak ada koin terpotong di tengah, mis. judul `#VIRTUAL` terpisah dari detailnya). Footer `⚠️ Disclaimer` selalu menempel di akhir sinyal koin terakhir (chunk terakhir).

## Evaluasi Sinyal Sesi Sebelumnya (Daily Recap)

`evaluation.py` + `data/history.json`:

- **Penyimpanan riwayat**: tiap sesi (2x sehari) menyimpan sinyal terpilih (Symbol, Direction, Entry, SL, TP1, TP2, Timestamp) dengan **key sesi WIB** `YYYY-MM-DD HH:MM` (kunci lama `YYYY-MM-DD` tetap didukung). Karena runner GitHub Actions di-reset tiap run, workflow meng-*commit balik* `history.json` ke repo.
- **Evaluasi sebelum briefing**: pada run berikutnya, bot membaca **sesi terakhir sebelum sesi sekarang** (termasuk sesi pagi yang sama), mengambil **high/low/current 24j** tiap pair dari Binance, lalu menentukan status tiap sinyal dengan urutan cek **TP2 → TP1 → SL → Floating**.
- **Win rate** = % sinyal yang menyentuh TP1/TP2 dari seluruh sinyal yang dievaluasi (ditampilkan juga jumlah TP1/TP2/SL/Floating).
- Recap dikirim sebagai **pesan Telegram terpisah** (History Review) sebelum blok `📊 DAY TRADING BRIEFING — MTF SMC + S&D`:

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

Bila belum ada riwayat (sesi pertama) atau semua data harga gagal diambil, recap dilewati tanpa menggagalkan scan.

## Catatan penting

- **Binance futures** (funding/L-S ratio) dapat diblokir region tertentu. Bila tidak terjangkau, bot otomatis memakai Fear & Greed saja — tidak pernah gagal total.
- **CMC free tier**: data delay, tanpa candle historis → hanya untuk ranking. Candle tetap dari Binance.
- **Whale & on-chain** adalah *proxy* data gratis, bukan level Glassnode/Santiment. Parameter `WHALE_MIN_USD` / `WHALE_LOOKBACK_HOURS` di `.env`.

## Disclaimer

Sinyal berbasis indikator otomatis & data publik — bukan saran finansial. Selalu lakukan riset sendiri (DYOR).
