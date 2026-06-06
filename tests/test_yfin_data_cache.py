"""get_YFin_data_online must slice the shared load_ohlcv cache, not issue its
own yf.Ticker.history download.

Routing it through load_ohlcv collapses the market analyst's snapshot + OHLCV +
indicator fetches into one network call per symbol (the main cause of Yahoo
rate-limiting) and keeps the OHLCV tool consistent with the verified snapshot.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from tradingagents.dataflows import y_finance
from tradingagents.dataflows.symbol_utils import NoMarketDataError


def _frame():
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(
                ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-06"]
            ),
            "Open": [10.0, 11.0, 12.0, 13.0],
            "High": [10.5, 11.5, 12.5, 13.5],
            "Low": [9.5, 10.5, 11.5, 12.5],
            "Close": [10.123, 11.456, 12.0, 13.0],
            "Volume": [100, 200, 300, 400],
        }
    )


@pytest.mark.unit
def test_reuses_cache_and_issues_no_network_call():
    with patch.object(y_finance, "load_ohlcv", return_value=_frame()) as lo, \
         patch.object(y_finance.yf, "Ticker") as ticker:
        out = y_finance.get_YFin_data_online("MU", "2026-01-02", "2026-01-03")
    lo.assert_called_once()
    ticker.assert_not_called()  # no separate download
    assert "# Total records: 2" in out
    assert "2026-01-02" in out and "2026-01-03" in out
    assert "2026-01-01" not in out  # before start filtered out
    assert "2026-01-06" not in out  # after end filtered out
    assert "11.46" in out  # close rounded to 2dp


@pytest.mark.unit
def test_empty_window_raises_no_market_data():
    with patch.object(y_finance, "load_ohlcv", return_value=_frame()):
        with pytest.raises(NoMarketDataError):
            y_finance.get_YFin_data_online("MU", "2025-01-01", "2025-06-01")


@pytest.mark.unit
def test_propagates_no_market_data_from_cache_layer():
    with patch.object(
        y_finance, "load_ohlcv",
        side_effect=NoMarketDataError("FAKE", "FAKE", "no rows"),
    ):
        with pytest.raises(NoMarketDataError):
            y_finance.get_YFin_data_online("FAKE", "2026-01-01", "2026-01-03")


@pytest.mark.unit
def test_malformed_date_raises_before_fetch():
    with patch.object(y_finance, "load_ohlcv") as lo:
        with pytest.raises(ValueError):
            y_finance.get_YFin_data_online("MU", "01-01-2026", "2026-01-03")
    lo.assert_not_called()
