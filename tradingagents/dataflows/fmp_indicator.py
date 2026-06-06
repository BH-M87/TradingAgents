"""FMP technical indicators, computed locally with stockstats on FMP OHLCV.

FMP's native indicator endpoint has partial coverage (no Bollinger bands,
MACD sub-series, or VWMA) and costs one call per indicator. Instead we reuse
the same stockstats math the yfinance path uses, sourcing prices from FMP.
"""

from .fmp_stock import load_fmp_ohlcv
from .indicators_common import indicator_window_from_frame


def get_indicator(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int,
    interval: str = "daily",
    time_period: int = 14,
    series_type: str = "close",
) -> str:
    """Return a dated window of ``indicator`` values from FMP-sourced OHLCV.

    ``interval`` / ``time_period`` / ``series_type`` are accepted for vendor
    signature compatibility; the stockstats indicator keys encode their own
    periods, so they are not used here.
    """
    data = load_fmp_ohlcv(symbol, curr_date)
    return indicator_window_from_frame(data, indicator, curr_date, look_back_days)
