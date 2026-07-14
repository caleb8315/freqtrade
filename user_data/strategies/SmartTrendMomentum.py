# pragma pylint: disable=missing-docstring, invalid-name, pointless-string-statement
# isort: skip_file
"""
SmartTrendMomentum
==================

A trend-following + momentum strategy for Freqtrade (spot, long-only by default).

Idea
----
Only take longs when the market is in an established uptrend (fast EMA above slow
EMA and price above a long-term EMA), and time the entry using a momentum pullback
(RSI recovering from oversold) confirmed by MACD turning up and healthy volume.
Exits are handled by a combination of ROI, a trailing stop, and a momentum-based
exit signal (RSI overbought / trend weakening).

Every meaningful threshold is exposed as a Hyperopt parameter so the strategy can
be auto-tuned with `freqtrade hyperopt`.

This is a starting point, NOT financial advice. Always backtest and dry-run before
risking real funds.
"""

from datetime import datetime
from typing import Optional

import talib.abstract as ta
from pandas import DataFrame
from technical import qtpylib

from freqtrade.persistence import Trade
from freqtrade.strategy import (
    IStrategy,
    DecimalParameter,
    IntParameter,
)


class SmartTrendMomentum(IStrategy):
    """Trend-following + momentum strategy with hyperoptable parameters."""

    INTERFACE_VERSION = 3

    # Spot, long-only. Set to True + configure margin/futures to allow shorts.
    can_short: bool = False

    # Timeframe for the strategy.
    timeframe = "1h"

    # Optimized entry parameters (found via `freqtrade hyperopt`, SharpeHyperOptLoss).
    # Re-run hyperopt on your own data/timerange to refresh these.
    buy_params = {
        "buy_ema_fast": 23,
        "buy_ema_slow": 105,
        "buy_ema_trend": 150,
        "buy_rsi": 64,
        "buy_volume_factor": 2.4,
    }

    # Optimized exit parameters.
    sell_params = {
        "sell_rsi": 65,
    }

    # Minimal ROI table (tuned by hyperopt; overridable by config).
    # Take profit tiers that relax the longer a trade is open (minutes -> ROI).
    minimal_roi = {
        "0": 0.346,
        "267": 0.147,
        "500": 0.071,
        "799": 0.0,
    }

    # Hard stoploss (protective floor; trailing stop usually triggers first).
    stoploss = -0.10

    # Trailing stop: lock in profit once a trade moves in our favour (tuned by hyperopt).
    trailing_stop = True
    trailing_stop_positive = 0.254
    trailing_stop_positive_offset = 0.292
    trailing_only_offset_is_reached = True

    # Only run indicator calculation once per new candle (faster).
    process_only_new_candles = True

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Enable the custom_stoploss() callback below.
    use_custom_stoploss = True

    # Need enough candles for the 200-period EMA to be valid.
    startup_candle_count: int = 210

    order_types = {
        "entry": "limit",
        "exit": "limit",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    order_time_in_force = {"entry": "GTC", "exit": "GTC"}

    # -------------------------------------------------------------------------
    # Hyperoptable parameters
    # -------------------------------------------------------------------------
    # Entry
    # RSI level the momentum must reclaim to confirm the trend is resuming.
    buy_rsi = IntParameter(40, 65, default=50, space="buy", optimize=True)
    buy_ema_fast = IntParameter(10, 50, default=21, space="buy", optimize=True)
    buy_ema_slow = IntParameter(50, 120, default=55, space="buy", optimize=True)
    buy_ema_trend = IntParameter(150, 200, default=200, space="buy", optimize=True)
    # Volume must be at least this multiple of its recent average.
    buy_volume_factor = DecimalParameter(
        0.8, 2.5, default=1.0, decimals=1, space="buy", optimize=True
    )

    # Exit
    sell_rsi = IntParameter(60, 90, default=72, space="sell", optimize=True)

    def informative_pairs(self):
        return []

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Trend: exponential moving averages.
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=int(self.buy_ema_fast.value))
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=int(self.buy_ema_slow.value))
        dataframe["ema_trend"] = ta.EMA(dataframe, timeperiod=int(self.buy_ema_trend.value))

        # Momentum: RSI.
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # Momentum confirmation: MACD.
        macd = ta.MACD(dataframe)
        dataframe["macd"] = macd["macd"]
        dataframe["macdsignal"] = macd["macdsignal"]
        dataframe["macdhist"] = macd["macdhist"]

        # Volume filter: rolling mean of volume.
        dataframe["volume_mean"] = dataframe["volume"].rolling(window=30).mean()

        # Volatility context (Bollinger Bands) - used as a plotting aid / guard.
        bollinger = qtpylib.bollinger_bands(qtpylib.typical_price(dataframe), window=20, stds=2)
        dataframe["bb_lowerband"] = bollinger["lower"]
        dataframe["bb_middleband"] = bollinger["mid"]
        dataframe["bb_upperband"] = bollinger["upper"]

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                # Established uptrend: fast EMA above slow EMA, price above long trend EMA.
                (dataframe["ema_fast"] > dataframe["ema_slow"])
                & (dataframe["close"] > dataframe["ema_trend"])
                # Momentum pullback recovering: RSI crossing back up through threshold.
                & (qtpylib.crossed_above(dataframe["rsi"], self.buy_rsi.value))
                # MACD confirms upward momentum.
                & (dataframe["macd"] > dataframe["macdsignal"])
                # Volume confirmation.
                & (dataframe["volume"] > dataframe["volume_mean"] * self.buy_volume_factor.value)
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                # Overbought momentum OR trend breaking down.
                (
                    (dataframe["rsi"] > self.sell_rsi.value)
                    | (dataframe["ema_fast"] < dataframe["ema_slow"])
                )
                & (dataframe["volume"] > 0)
            ),
            "exit_long",
        ] = 1

        return dataframe

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> Optional[float]:
        """
        Tighten the stop as the trade becomes profitable.

        Returns a stoploss relative to `current_rate` (negative = below current price).
        Returning None keeps the previously set stoploss.
        """
        if current_profit > 0.10:
            return -0.03
        if current_profit > 0.05:
            return -0.05
        # Keep the configured/default stoploss otherwise.
        return None
