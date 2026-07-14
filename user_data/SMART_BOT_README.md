# Smart Crypto Bot — Kraken + Machine Learning + custom dashboard

A complete, tested Freqtrade setup built on top of this repo. Everything lives in
`user_data/` (the engine is untouched). It includes:

- **Two strategies** — a classic indicator strategy and a machine-learning (FreqAI) one.
- **A Kraken dry-run config** (US-friendly, USD pairs) — your live/paper target.
- **A custom web dashboard** that talks to the bot's REST API.

> Disclaimer: Educational, not financial advice. Most bots lose money, especially
> un-tuned ones. Prove everything in backtest + dry-run first. Never risk funds you
> can't afford to lose. Nothing here trades real money until *you* deliberately set
> `dry_run: false` with your own API keys.

---

## Files

| Path | What it is |
|------|------------|
| `strategies/SmartTrendMomentum.py` | Classic trend + momentum strategy (EMA/RSI/MACD/volume), hyperopt-tuned. |
| `strategies/FreqaiSmartStrategy.py` | **Machine-learning** strategy: engineers features, trains a model to predict future returns, retrains on a rolling window. Hybrid entry (ML prediction must agree with the trend). |
| `config_kraken.json` | **Kraken** dry-run config, USD stake + USD pairs, dashboard enabled. Use this for live/dry-run. |
| `config_freqai.json` | FreqAI config for **backtesting** the ML strategy (exchange OKX, see note on data below). |
| `config.json` | Original OKX dry-run config (kept for backtesting the classic strategy). |
| `dashboard/index.html` | Self-contained custom dashboard (no build step). |

### Why two exchanges?
You're on **Kraken** (correct choice for a US user). But Kraken's API does **not**
serve historical candles for fast backtesting, and Binance is geo-blocked in the US.
So we **backtest on OKX data** (a good public-data proxy) and **run live/dry-run on
Kraken**. Signals/behavior transfer well; only fees differ slightly.

---

## One-time setup

From the repo root:

```bash
# 1. TA-Lib C library (Linux; see docs/installation.md for macOS/Windows)
cd build_helpers && sudo bash install_ta-lib.sh && cd ..

# 2. Python env + dependencies (note: pandas-ta==0.3.14b was yanked from PyPI and
#    is NOT needed by the core bot, so we skip it)
python3 -m venv .venv
.venv/bin/pip install --upgrade pip wheel
grep -v "pandas-ta" requirements.txt > /tmp/req_core.txt
.venv/bin/pip install -r /tmp/req_core.txt -r requirements-hyperopt.txt -r requirements-plot.txt
.venv/bin/pip install -e . --no-deps

# 3. (Only for the ML strategy) FreqAI dependencies
.venv/bin/pip install scikit-learn==1.5.2 joblib==1.4.2 catboost==1.2.7 \
    matplotlib==3.9.2 lightgbm==4.5.0 xgboost==2.0.3 tensorboard==2.18.0 datasieve==0.1.7
```

---

## A) Classic strategy — backtest, tune, dry-run

```bash
# Backtest on OKX data
.venv/bin/freqtrade download-data --config user_data/config.json --userdir user_data --timeframe 1h --timerange 20240101-
.venv/bin/freqtrade backtesting --config user_data/config.json --userdir user_data --strategy SmartTrendMomentum --timerange 20240101-

# Auto-tune parameters (the printed values are already baked into the strategy)
.venv/bin/freqtrade hyperopt --config user_data/config.json --userdir user_data \
    --strategy SmartTrendMomentum --hyperopt-loss SharpeHyperOptLoss \
    --spaces buy sell roi trailing --epochs 100 --timerange 20240101-

# Paper-trade LIVE on Kraken (no real money; dry_run: true)
.venv/bin/freqtrade trade --config user_data/config_kraken.json --userdir user_data --strategy SmartTrendMomentum
```

## B) Machine-learning strategy (FreqAI)

```bash
# Needs 1h data + ~30 days of warm-up before your backtest window for training
.venv/bin/freqtrade download-data --config user_data/config_freqai.json --userdir user_data --timeframe 1h --timerange 20240101-

# Backtest the ML model (it trains + retrains automatically)
.venv/bin/freqtrade backtesting --config user_data/config_freqai.json --userdir user_data \
    --strategy FreqaiSmartStrategy --freqaimodel LightGBMRegressor --timerange 20240401-20240601

# Dry-run live (edit config_freqai.json exchange -> kraken + USD pairs first for your account)
.venv/bin/freqtrade trade --config user_data/config_freqai.json --userdir user_data \
    --strategy FreqaiSmartStrategy --freqaimodel LightGBMRegressor
```

## C) The dashboard

The bot exposes a REST API (enabled in the configs). There are two ways to see it:

- **Built-in FreqUI** (full featured): `.venv/bin/freqtrade install-ui` once, then browse
  to `http://127.0.0.1:8080` while the bot runs.
- **This custom dashboard** (`dashboard/index.html`): serve it and connect.

```bash
# With the bot running (config has api_server.enabled = true), in another terminal:
cd user_data/dashboard && python3 -m http.server 8000
# Open http://127.0.0.1:8000 and log in with the api_server username/password.
```

If the dashboard can't connect, the usual cause is CORS: make sure the address you
open it from is listed in the config's `api_server.CORS_origins` (8000 and 8080 are
already included).

---

## ✅ What YOU need to do

1. **Run the one-time setup** above (installs the bot + ML deps).
2. **Create a Kraken account** and generate API keys
   (Kraken → Settings → API → create key with *Query* + *Trade* permissions; do **not**
   enable withdrawals). US-based: you're all set on Kraken.
3. **Edit `user_data/config_kraken.json`:**
   - Put your keys in `exchange.key` / `exchange.secret` (or use env vars / a secrets
     manager — don't commit real keys).
   - Change the three placeholders: `jwt_secret_key`, `ws_token`, and the dashboard
     `password` to strong random values.
   - Optionally adjust `pair_whitelist`, `stake_amount`, `max_open_trades`.
4. **Backtest** (strategy A and/or B) to understand the behavior — see commands above.
5. **Dry-run on Kraken** (`dry_run: true`, the default) and watch it on the dashboard for
   a while. This uses real live prices but **zero real money**.
6. **Only when you're satisfied**, and with money you can afford to lose: set
   `"dry_run": false` in `config_kraken.json`, start with a **small** `stake_amount`, and
   run the `trade` command. That single flag is the line between practice and real money —
   cross it deliberately.

---

## Honest performance notes

- **Classic strategy (tuned):** on OKX 1h data (Jan 2024–Jun 2025) it was profitable and
  very low-drawdown (profit factor ~11, few losing days) but traded rarely and **did not
  beat buy-and-hold** (market rose ~75%). A safe, capital-preserving baseline.
- **ML strategy:** the pipeline is fully working (trains, predicts, retrains, trades). Out
  of the box with a small feature set it's roughly break-even/slightly negative — normal
  for un-researched ML. It's a genuine research surface: add features, try other models
  (`XGBoostRegressor`, `CatboostRegressor`), tune `label_period_candles`, thresholds, and
  the training window. The hybrid trend guard already made it far more sensible.

There is no "smartest" auto-profitable bot. The edge comes from research + testing, which
this setup is built to let you do safely.
