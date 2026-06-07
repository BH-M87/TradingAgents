"""yfinance fundamentals/statements must fail over (not swallow) on rate limits.

Previously these functions caught every exception and returned an error
*string*, so the routing layer saw a "successful" return and never tried FMP.
A Yahoo 429 must instead raise VendorRateLimitError so route_to_vendor fails
over, and must trip the breaker so the rest of the run skips Yahoo.
"""

from unittest import mock

import pytest
from yfinance.exceptions import YFRateLimitError

from tradingagents.dataflows import y_finance, vendor_cooldown
from tradingagents.dataflows.vendor_errors import VendorRateLimitError


@pytest.fixture(autouse=True)
def _clean():
    vendor_cooldown.reset()
    yield
    vendor_cooldown.reset()


@pytest.mark.unit
def test_fundamentals_rate_limit_raises_and_trips_breaker():
    with mock.patch.object(y_finance.yf, "Ticker", return_value=mock.MagicMock()), \
         mock.patch.object(y_finance, "yf_retry", side_effect=YFRateLimitError()):
        with pytest.raises(VendorRateLimitError):
            y_finance.get_fundamentals("MU")
    assert vendor_cooldown.in_cooldown("yfinance") is True


@pytest.mark.unit
def test_fundamentals_short_circuits_when_cooling():
    vendor_cooldown.record_rate_limit("yfinance")
    with mock.patch.object(y_finance.yf, "Ticker") as ticker:
        with pytest.raises(VendorRateLimitError):
            y_finance.get_fundamentals("NVDA")
    assert not ticker.called  # never even constructed the Ticker


@pytest.mark.unit
def test_balance_sheet_rate_limit_raises():
    with mock.patch.object(y_finance.yf, "Ticker", return_value=mock.MagicMock()), \
         mock.patch.object(y_finance, "yf_retry", side_effect=YFRateLimitError()):
        with pytest.raises(VendorRateLimitError):
            y_finance.get_balance_sheet("MU")


@pytest.mark.unit
def test_non_rate_limit_error_still_returns_string():
    # A genuinely incidental error must keep the old graceful string behavior,
    # not be misreported as a rate limit.
    with mock.patch.object(y_finance.yf, "Ticker", return_value=mock.MagicMock()), \
         mock.patch.object(y_finance, "yf_retry", side_effect=ValueError("boom")):
        out = y_finance.get_fundamentals("MU")
    assert "Error retrieving fundamentals" in out
    assert vendor_cooldown.in_cooldown("yfinance") is False
