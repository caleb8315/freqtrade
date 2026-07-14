# SmartTrendMomentum — your automated crypto bot

This folder contains a ready-to-run Freqtrade setup: a tunable trend + momentum
strategy, a **safe dry-run** configuration, and everything you need to backtest,
optimize, and watch it trade on a dashboard — **with no real money at risk** until
*you* deliberately choose to go live.

> Disclaimer: This is educational, not financial advice. Most trading bots lose
> money, especially un-tuned ones. Prove everything in backtest + dry-run first,
> and never risk funds you can't afford to lose.

## What's here

- `strategies/SmartTrendMomentum.py` — the strategy (long-only spot). It only buys
  in an established uptrend (EMA stack + price above the long EMA), times entries with
  RSI momentum + MACD confirmation + a volume filter, and exits via ROI, a trailing
  stop, an RSI/trend-break exit signal, and a profit-tightening custom stoploss.
  Every threshold is a **Hyperopt parameter** so it can be auto-tuned.
- `config.json` — dry-run config (exchange OKX, `dry_run: true`, 1000 USDT paper
  wallet, majors whitelist, dashboard/API server enabled on `127.0.0.1:8080`).

## One-time setup

The TA-Lib C library and Python deps must be installed. From the repo root:

```bash
# 1. TA-Lib C library (Linux; see docs/installation.md for macOS/Windows)
cd build_helpers && sudo bash install_ta-lib.sh && cd ..

# 2. Python environment
python3 -m venv .venv
.venv/bin/pip install --upgrade pip wheel
# NOTE: pandas-ta==0.3.14b was yanked from PyPI and is not needed by the core bot,
# so install the pinned deps without it, then install freqtrade itself:
grep -v "pandas-ta" requirements.txt > /tmp/req_core.txt
.venv/bin/pip install -r /tmp/req_core.txt -r requirements-hyperopt.txt -r requirements-plot.txt
.venv/bin/pip install -e . --no-deps
```

## Workflow

All commands are run from the repo root. `--userdir user_data` points Freqtrade here.

### 1. Download historical data

```bash
.venv/bin/freqtrade download-data --config user_data/config.json --userdir user_data \
    --timeframe 1h --timerange 20240101-
```

### 2. Backtest

```bash
.venv/bin/freqtrade backtesting --config user_data/config.json --userdir user_data \
    --strategy SmartTrendMomentum --timeframe 1h --timerange 20240101-
```

### 3. Hyperopt (auto-tune the parameters — the "smart" part)

```bash
.venv/bin/freqtrade hyperopt --config user_data/config.json --userdir user_data \
    --strategy SmartTrendMomentum --hyperopt-loss SharpeHyperOptLoss \
    --spaces buy sell roi trailing --epochs 100 --timeframe 1h --timerange 20240101-
```

Then paste the printed `buy_params` / `sell_params` / `minimal_roi` / trailing values
back into the strategy (the current values are already tuned results from one such run).

### 4. Dry-run (paper trade live market data + dashboard)

```bash
.venv/bin/freqtrade trade --config user_data/config.json --userdir user_data \
    --strategy SmartTrendMomentum
```

Open the dashboard (FreqUI). First install it once with
`.venv/bin/freqtrade install-ui`, then browse to `http://127.0.0.1:8080`
(log in with the `username`/`password` from `config.json`).

## Going live (only when YOU decide)

This is a deliberate, manual step. To trade real money you would:

1. Choose a supported exchange you can legally use (US users: **Kraken** is the most
   bot-friendly officially-supported option; Binance is geo-restricted in many regions
   — this VM itself got an HTTP 451 from Binance).
2. Create API keys on that exchange and put them in `config.json` (`exchange.key` /
   `exchange.secret`) — better, use environment variables / a secrets manager.
3. Set `"dry_run": false` and rotate the placeholder `jwt_secret_key`, `ws_token`,
   and dashboard `password` to strong random values.
4. Start with tiny stake sizes.

## Honest performance note

On OKX 1h data (Jan 2024 – Jun 2025), the tuned strategy was **profitable and very
low-drawdown** (profit factor ~11, only a handful of losing days) but **traded rarely
and did NOT beat simply holding** (the market rose ~75% over the same window). That's
typical: this is a conservative, capital-preserving baseline. Improving it is exactly
what hyperopt, more pairs/timeframes, and the FreqAI machine-learning module are for.
