"""yfinance news functions must fail over (not swallow) on rate limits.

Like the fundamentals path, these previously returned an error *string* on any
exception, so the routing layer never failed over to another news vendor. A
Yahoo 429 must raise VendorRateLimitError and trip the breaker; an unrelated
error must keep the graceful string.
"""

from unittest import mock

import pytest
from yfinance.exceptions import YFRateLimitError

from tradingagents.dataflows import yfinance_news, vendor_cooldown
from tradingagents.dataflows.vendor_errors import VendorRateLimitError


@pytest.fixture(autouse=True)
def _clean():
    vendor_cooldown.reset()
    yield
    vendor_cooldown.reset()


@pytest.mark.unit
def test_ticker_news_rate_limit_raises_and_trips_breaker():
    with mock.patch.object(yfinance_news.yf, "Ticker", return_value=mock.MagicMock()), \
         mock.patch.object(yfinance_news, "yf_retry", side_effect=YFRateLimitError()):
        with pytest.raises(VendorRateLimitError):
            yfinance_news.get_news_yfinance("MU", "2026-01-01", "2026-01-10")
    assert vendor_cooldown.in_cooldown("yfinance") is True


@pytest.mark.unit
def test_ticker_news_short_circuits_when_cooling():
    vendor_cooldown.record_rate_limit("yfinance")
    with mock.patch.object(yfinance_news.yf, "Ticker") as ticker:
        with pytest.raises(VendorRateLimitError):
            yfinance_news.get_news_yfinance("MU", "2026-01-01", "2026-01-10")
    assert not ticker.called


@pytest.mark.unit
def test_ticker_news_non_rate_limit_returns_string():
    with mock.patch.object(yfinance_news.yf, "Ticker", return_value=mock.MagicMock()), \
         mock.patch.object(yfinance_news, "yf_retry", side_effect=ValueError("boom")):
        out = yfinance_news.get_news_yfinance("MU", "2026-01-01", "2026-01-10")
    assert "Error fetching news" in out
    assert vendor_cooldown.in_cooldown("yfinance") is False


@pytest.mark.unit
def test_global_news_rate_limit_raises():
    with mock.patch.object(yfinance_news, "yf_retry", side_effect=YFRateLimitError()):
        with pytest.raises(VendorRateLimitError):
            yfinance_news.get_global_news_yfinance("2026-01-10")
    assert vendor_cooldown.in_cooldown("yfinance") is True


@pytest.mark.unit
def test_global_news_short_circuits_when_cooling():
    vendor_cooldown.record_rate_limit("yfinance")
    with mock.patch.object(yfinance_news, "yf_retry") as retry:
        with pytest.raises(VendorRateLimitError):
            yfinance_news.get_global_news_yfinance("2026-01-10")
    assert not retry.called
