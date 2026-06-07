"""Tests for the vendor-routed OHLCV frame loader (ohlcv_loader).

This is what gives the verified-snapshot path an FMP fallback: when Yahoo is
rate-limited, the snapshot must still get a real OHLCV frame from FMP instead
of degrading to a placeholder.
"""

from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows import ohlcv_loader, stockstats_utils, fmp_stock
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.symbol_utils import NoMarketDataError
from tradingagents.dataflows.vendor_errors import VendorRateLimitError


def _frame(tag: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Date": pd.date_range("2026-01-01", periods=3, freq="D"),
            "Open": tag, "High": tag, "Low": tag, "Close": tag, "Volume": 1,
        }
    )


@pytest.fixture(autouse=True)
def _default_vendor_order():
    # Default: yfinance primary, fmp fallback (mirrors default_config).
    set_config({"data_vendors": {"core_stock_apis": "yfinance"}})
    yield
    set_config({"data_vendors": {"core_stock_apis": "yfinance"}})


@pytest.mark.unit
def test_uses_yahoo_when_available():
    with mock.patch.object(stockstats_utils, "load_ohlcv", return_value=_frame(1.0)) as yf_, \
         mock.patch.object(fmp_stock, "load_fmp_ohlcv") as fmp_:
        out = ohlcv_loader.load_ohlcv_with_fallback("MU", "2026-01-03")
    assert out["Close"].iloc[0] == 1.0
    assert yf_.called
    assert not fmp_.called  # primary succeeded, no fallback needed


@pytest.mark.unit
def test_falls_over_to_fmp_on_rate_limit():
    with mock.patch.object(stockstats_utils, "load_ohlcv",
                           side_effect=VendorRateLimitError("Yahoo cooling")), \
         mock.patch.object(fmp_stock, "load_fmp_ohlcv", return_value=_frame(2.0)) as fmp_:
        out = ohlcv_loader.load_ohlcv_with_fallback("MU", "2026-01-03")
    assert out["Close"].iloc[0] == 2.0  # FMP frame
    assert fmp_.called


@pytest.mark.unit
def test_falls_over_to_fmp_on_no_data():
    with mock.patch.object(stockstats_utils, "load_ohlcv",
                           side_effect=NoMarketDataError("MU", "MU", "no rows")), \
         mock.patch.object(fmp_stock, "load_fmp_ohlcv", return_value=_frame(3.0)):
        out = ohlcv_loader.load_ohlcv_with_fallback("MU", "2026-01-03")
    assert out["Close"].iloc[0] == 3.0


@pytest.mark.unit
def test_no_data_everywhere_raises_no_market_data():
    with mock.patch.object(stockstats_utils, "load_ohlcv",
                           side_effect=NoMarketDataError("FAKE", "FAKE", "no rows")), \
         mock.patch.object(fmp_stock, "load_fmp_ohlcv",
                           side_effect=NoMarketDataError("FAKE", "FAKE", "no rows")):
        with pytest.raises(NoMarketDataError):
            ohlcv_loader.load_ohlcv_with_fallback("FAKE", "2026-01-03")


@pytest.mark.unit
def test_all_rate_limited_raises_rate_limit():
    # No vendor reported clean no-data; every vendor was throttled.
    with mock.patch.object(stockstats_utils, "load_ohlcv",
                           side_effect=VendorRateLimitError("Yahoo cooling")), \
         mock.patch.object(fmp_stock, "load_fmp_ohlcv",
                           side_effect=VendorRateLimitError("FMP over quota")):
        with pytest.raises(VendorRateLimitError):
            ohlcv_loader.load_ohlcv_with_fallback("MU", "2026-01-03")


@pytest.mark.unit
def test_respects_configured_fmp_primary_order():
    set_config({"data_vendors": {"core_stock_apis": "fmp"}})
    with mock.patch.object(stockstats_utils, "load_ohlcv", return_value=_frame(1.0)) as yf_, \
         mock.patch.object(fmp_stock, "load_fmp_ohlcv", return_value=_frame(9.0)) as fmp_:
        out = ohlcv_loader.load_ohlcv_with_fallback("MU", "2026-01-03")
    assert out["Close"].iloc[0] == 9.0  # FMP first per config
    assert fmp_.called
    assert not yf_.called
