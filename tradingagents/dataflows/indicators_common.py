"""Vendor-neutral technical-indicator computation over an OHLCV DataFrame.

The indicator math (stockstats) and the windowed-string output are identical
regardless of where the OHLCV data came from. Only the data source differs,
so callers load their own OHLCV frame and pass it in. This lets the yfinance
and FMP vendors share one implementation.
"""

from datetime import datetime

import pandas as pd
from dateutil.relativedelta import relativedelta
from stockstats import wrap

# Supported indicator keys mapped to their human-readable descriptions,
# appended to every window result so the agent gets usage guidance.
BEST_IND_PARAMS = {
    "close_50_sma": (
        "50 SMA: A medium-term trend indicator. "
        "Usage: Identify trend direction and serve as dynamic support/resistance. "
        "Tips: It lags price; combine with faster indicators for timely signals."
    ),
    "close_200_sma": (
        "200 SMA: A long-term trend benchmark. "
        "Usage: Confirm overall market trend and identify golden/death cross setups. "
        "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
    ),
    "close_10_ema": (
        "10 EMA: A responsive short-term average. "
        "Usage: Capture quick shifts in momentum and potential entry points. "
        "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
    ),
    "macd": (
        "MACD: Computes momentum via differences of EMAs. "
        "Usage: Look for crossovers and divergence as signals of trend changes. "
        "Tips: Confirm with other indicators in low-volatility or sideways markets."
    ),
    "macds": (
        "MACD Signal: An EMA smoothing of the MACD line. "
        "Usage: Use crossovers with the MACD line to trigger trades. "
        "Tips: Should be part of a broader strategy to avoid false positives."
    ),
    "macdh": (
        "MACD Histogram: Shows the gap between the MACD line and its signal. "
        "Usage: Visualize momentum strength and spot divergence early. "
        "Tips: Can be volatile; complement with additional filters in fast-moving markets."
    ),
    "rsi": (
        "RSI: Measures momentum to flag overbought/oversold conditions. "
        "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
        "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
    ),
    "boll": (
        "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
        "Usage: Acts as a dynamic benchmark for price movement. "
        "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
    ),
    "boll_ub": (
        "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
        "Usage: Signals potential overbought conditions and breakout zones. "
        "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
    ),
    "boll_lb": (
        "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
        "Usage: Indicates potential oversold conditions. "
        "Tips: Use additional analysis to avoid false reversal signals."
    ),
    "atr": (
        "ATR: Averages true range to measure volatility. "
        "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
        "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
    ),
    "vwma": (
        "VWMA: A moving average weighted by volume. "
        "Usage: Confirm trends by integrating price action with volume data. "
        "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
    ),
    "mfi": (
        "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
        "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
        "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
    ),
}


def _bulk_indicator(ohlcv_df: pd.DataFrame, indicator: str) -> dict:
    """Compute ``indicator`` for every row, returning {date_str: value_str}."""
    df = wrap(ohlcv_df.copy())
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    df[indicator]  # trigger stockstats to calculate the indicator column

    result = {}
    for _, row in df.iterrows():
        value = row[indicator]
        result[row["Date"]] = "N/A" if pd.isna(value) else str(value)
    return result


def indicator_window_from_frame(
    ohlcv_df: pd.DataFrame,
    indicator: str,
    curr_date: str,
    look_back_days: int,
) -> str:
    """Return a dated window of ``indicator`` values plus its description.

    ``ohlcv_df`` must have a datetime ``Date`` column and OHLCV columns.
    """
    if indicator not in BEST_IND_PARAMS:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(BEST_IND_PARAMS.keys())}"
        )

    end_date = curr_date
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_date_dt - relativedelta(days=look_back_days)

    indicator_data = _bulk_indicator(ohlcv_df, indicator)

    ind_string = ""
    current_dt = curr_date_dt
    while current_dt >= before:
        date_str = current_dt.strftime("%Y-%m-%d")
        value = indicator_data.get(
            date_str, "N/A: Not a trading day (weekend or holiday)"
        )
        ind_string += f"{date_str}: {value}\n"
        current_dt = current_dt - relativedelta(days=1)

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {end_date}:\n\n"
        + ind_string
        + "\n\n"
        + BEST_IND_PARAMS.get(indicator, "No description available.")
    )
