"""FMP vendor: client behavior, parsing, look-ahead filtering, routing fallback."""

import pytest

from tradingagents.dataflows.vendor_errors import VendorRateLimitError
from tradingagents.dataflows.alpha_vantage_common import AlphaVantageRateLimitError


@pytest.mark.unit
def test_alpha_vantage_rate_limit_is_vendor_rate_limit():
    assert issubclass(AlphaVantageRateLimitError, VendorRateLimitError)
    err = AlphaVantageRateLimitError("rate limited")
    assert isinstance(err, VendorRateLimitError)
