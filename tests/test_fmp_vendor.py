"""FMP vendor: client behavior, parsing, look-ahead filtering, routing fallback."""

import pytest
import pandas as pd

from tradingagents.dataflows.vendor_errors import VendorRateLimitError
from tradingagents.dataflows.alpha_vantage_common import AlphaVantageRateLimitError


@pytest.mark.unit
def test_alpha_vantage_rate_limit_is_vendor_rate_limit():
    assert issubclass(AlphaVantageRateLimitError, VendorRateLimitError)
    err = AlphaVantageRateLimitError("rate limited")
    assert isinstance(err, VendorRateLimitError)


@pytest.mark.unit
def test_indicator_window_from_frame_formats_window():
    from tradingagents.dataflows.indicators_common import indicator_window_from_frame

    dates = pd.date_range("2024-01-01", periods=60, freq="D")
    df = pd.DataFrame(
        {
            "Date": dates,
            "Open": range(100, 160),
            "High": range(101, 161),
            "Low": range(99, 159),
            "Close": range(100, 160),
            "Volume": [1000] * 60,
        }
    )
    out = indicator_window_from_frame(df, "close_50_sma", "2024-02-29", look_back_days=3)

    assert "## close_50_sma values from 2024-02-26 to 2024-02-29:" in out
    assert "2024-02-29:" in out
    assert "2024-02-26:" in out
    assert "50 SMA" in out  # description appended
