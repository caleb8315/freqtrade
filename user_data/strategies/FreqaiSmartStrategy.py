import logging
from functools import reduce

import talib.abstract as ta
from pandas import DataFrame
from technical import qtpylib

from freqtrade.strategy import IStrategy


logger = logging.getLogger(__name__)


class FreqaiSmartStrategy(IStrategy):
    """
    FreqAI-powered strategy (spot, long-only).

    Instead of hand-coded entry rules, this strategy feeds a rich set of
    engineered features to a machine-learning regression model (configured via
    the `freqai` section of the config). The model predicts the normalized
    future return (`&-s_close`) over `label_period_candles`, self-retraining on
    a rolling window as new data arrives.

    We go long when the model predicts a return above a threshold AND FreqAI is
    confident the current market resembles its training data (`do_predict == 1`).

    Run with, e.g.:
        freqtrade backtesting --strategy FreqaiSmartStrategy \
            --freqaimodel LightGBMRegressor --config user_data/config_freqai.json

    This is educational, not financial advice. Backtest + dry-run before going live.
    """

    INTERFACE_VERSION = 3

    # Spot, long-only.
    can_short: bool = False

    timeframe = "1h"

    # ROI / stoploss act as risk guardrails around the model's own exit signal.
    minimal_roi = {"0": 0.10, "240": 0.05, "480": 0.02, "720": 0.0}
    stoploss = -0.05

    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Prediction thresholds (normalized return the model must forecast).
    # Higher entry threshold => fewer, higher-conviction trades (less fee drag).
    entry_threshold = 0.02
    exit_threshold = 0.0

    # FreqAI drives features; talib periods here are modest.
    startup_candle_count: int = 40

    plot_config = {
        "main_plot": {},
        "subplots": {
            "prediction": {"&-s_close": {"color": "blue"}},
            "do_predict": {"do_predict": {"color": "brown"}},
        },
    }

    def feature_engineering_expand_all(
        self, dataframe: DataFrame, period: int, metadata: dict, **kwargs
    ) -> DataFrame:
        """Features auto-expanded across periods / timeframes / shifted candles."""
        dataframe["%-rsi-period"] = ta.RSI(dataframe, timeperiod=period)
        dataframe["%-mfi-period"] = ta.MFI(dataframe, timeperiod=period)
        dataframe["%-adx-period"] = ta.ADX(dataframe, timeperiod=period)
        dataframe["%-sma-period"] = ta.SMA(dataframe, timeperiod=period)
        dataframe["%-ema-period"] = ta.EMA(dataframe, timeperiod=period)
        dataframe["%-roc-period"] = ta.ROC(dataframe, timeperiod=period)

        bollinger = qtpylib.bollinger_bands(
            qtpylib.typical_price(dataframe), window=period, stds=2.2
        )
        dataframe["bb_lowerband-period"] = bollinger["lower"]
        dataframe["bb_middleband-period"] = bollinger["mid"]
        dataframe["bb_upperband-period"] = bollinger["upper"]
        dataframe["%-bb_width-period"] = (
            dataframe["bb_upperband-period"] - dataframe["bb_lowerband-period"]
        ) / dataframe["bb_middleband-period"]
        dataframe["%-close-bb_lower-period"] = (
            dataframe["close"] / dataframe["bb_lowerband-period"]
        )

        dataframe["%-relative_volume-period"] = (
            dataframe["volume"] / dataframe["volume"].rolling(period).mean()
        )
        return dataframe

    def feature_engineering_expand_basic(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        """Features expanded across timeframes / shifted candles (not periods)."""
        dataframe["%-pct-change"] = dataframe["close"].pct_change()
        dataframe["%-raw_volume"] = dataframe["volume"]
        dataframe["%-raw_price"] = dataframe["close"]
        return dataframe

    def feature_engineering_standard(
        self, dataframe: DataFrame, metadata: dict, **kwargs
    ) -> DataFrame:
        """Non-expanded features (e.g. calendar features)."""
        dataframe["%-day_of_week"] = dataframe["date"].dt.dayofweek
        dataframe["%-hour_of_day"] = dataframe["date"].dt.hour
        return dataframe

    def set_freqai_targets(self, dataframe: DataFrame, metadata: dict, **kwargs) -> DataFrame:
        """Target: mean normalized return over the next `label_period_candles`."""
        label_period = self.freqai_info["feature_parameters"]["label_period_candles"]
        dataframe["&-s_close"] = (
            dataframe["close"].shift(-label_period).rolling(label_period).mean()
            / dataframe["close"]
            - 1
        )
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Plain trend indicator (not a model feature) used as an entry guard.
        dataframe["ema_trend"] = ta.EMA(dataframe, timeperiod=50)

        # FreqAI injects features, trains/loads the model, and attaches predictions.
        dataframe = self.freqai.start(dataframe, metadata, self)
        return dataframe

    def populate_entry_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        enter_long_conditions = [
            df["do_predict"] == 1,
            df["&-s_close"] > self.entry_threshold,
            # Hybrid guard: only trust bullish predictions inside an uptrend.
            df["close"] > df["ema_trend"],
            df["volume"] > 0,
        ]
        df.loc[
            reduce(lambda x, y: x & y, enter_long_conditions),
            ["enter_long", "enter_tag"],
        ] = (1, "freqai_long")
        return df

    def populate_exit_trend(self, df: DataFrame, metadata: dict) -> DataFrame:
        exit_long_conditions = [
            df["do_predict"] == 1,
            df["&-s_close"] < self.exit_threshold,
        ]
        df.loc[reduce(lambda x, y: x & y, exit_long_conditions), "exit_long"] = 1
        return df
