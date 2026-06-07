"""Tests for the per-vendor rate-limit circuit breaker (vendor_cooldown).

The breaker is what stops a Yahoo 429 from making every later call in the run
re-pay a ~62s backoff: once tripped it stays open for a cooldown window, then
re-probes the vendor.
"""

from unittest import mock

import pytest

from tradingagents.dataflows import vendor_cooldown


@pytest.fixture(autouse=True)
def _clean():
    vendor_cooldown.reset()
    yield
    vendor_cooldown.reset()


@pytest.mark.unit
def test_not_in_cooldown_by_default():
    assert vendor_cooldown.in_cooldown("yfinance") is False
    assert vendor_cooldown.seconds_remaining("yfinance") == 0.0


@pytest.mark.unit
def test_record_opens_breaker():
    vendor_cooldown.record_rate_limit("yfinance", cooldown_seconds=300)
    assert vendor_cooldown.in_cooldown("yfinance") is True
    assert 0 < vendor_cooldown.seconds_remaining("yfinance") <= 300


@pytest.mark.unit
def test_breaker_reopens_after_window():
    base = 1_000.0
    with mock.patch.object(vendor_cooldown.time, "monotonic") as clock:
        clock.return_value = base
        vendor_cooldown.record_rate_limit("yfinance", cooldown_seconds=600)
        assert vendor_cooldown.in_cooldown("yfinance") is True

        clock.return_value = base + 599
        assert vendor_cooldown.in_cooldown("yfinance") is True

        clock.return_value = base + 601  # window elapsed
        assert vendor_cooldown.in_cooldown("yfinance") is False
        # Expired entry is cleared, so it stays closed without another record.
        assert vendor_cooldown.in_cooldown("yfinance") is False


@pytest.mark.unit
def test_breaker_is_per_vendor():
    vendor_cooldown.record_rate_limit("yfinance", cooldown_seconds=300)
    assert vendor_cooldown.in_cooldown("yfinance") is True
    assert vendor_cooldown.in_cooldown("fmp") is False


@pytest.mark.unit
def test_raise_if_cooling():
    from tradingagents.dataflows.vendor_errors import VendorRateLimitError

    # Closed breaker: no-op.
    vendor_cooldown.raise_if_cooling("yfinance")

    vendor_cooldown.record_rate_limit("yfinance")
    with pytest.raises(VendorRateLimitError):
        vendor_cooldown.raise_if_cooling("yfinance")
    # A different vendor is unaffected.
    vendor_cooldown.raise_if_cooling("fmp")


@pytest.mark.unit
def test_reset_specific_and_all():
    vendor_cooldown.record_rate_limit("yfinance")
    vendor_cooldown.record_rate_limit("fmp")
    vendor_cooldown.reset("yfinance")
    assert vendor_cooldown.in_cooldown("yfinance") is False
    assert vendor_cooldown.in_cooldown("fmp") is True
    vendor_cooldown.reset()
    assert vendor_cooldown.in_cooldown("fmp") is False
