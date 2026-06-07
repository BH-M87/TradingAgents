"""Tests that empty vendor results never become fabricated data.

Covers two systematic fixes:
  - load_ohlcv must not cache an empty download (cache poisoning), and must
    raise NoMarketDataError instead of returning an empty frame.
  - route_to_vendor must convert NoMarketDataError into a single explicit
    "NO_DATA_AVAILABLE" sentinel after all vendors are exhausted.
"""

import os
import unittest
from unittest import mock

import pandas as pd
import pytest

from tradingagents.dataflows import stockstats_utils, interface
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.symbol_utils import NoMarketDataError
from tradingagents.dataflows.vendor_errors import VendorRateLimitError


@pytest.mark.unit
class TestLoadOhlcvNoPoison(unittest.TestCase):
    def setUp(self):
        self._tmp = os.path.join(os.path.dirname(__file__), "_tmp_cache")
        os.makedirs(self._tmp, exist_ok=True)
        set_config({"data_cache_dir": self._tmp})

    def tearDown(self):
        for f in os.listdir(self._tmp):
            os.remove(os.path.join(self._tmp, f))
        os.rmdir(self._tmp)

    def test_empty_download_raises_and_does_not_cache(self):
        empty = pd.DataFrame()
        with mock.patch.object(stockstats_utils.yf, "download", return_value=empty):
            with self.assertRaises(NoMarketDataError):
                stockstats_utils.load_ohlcv("FAKE", "2026-01-01")
        # Nothing should have been written to the cache.
        self.assertEqual(os.listdir(self._tmp), [])

        # A second call must re-attempt the fetch (no poisoned cache served).
        with mock.patch.object(stockstats_utils.yf, "download", return_value=empty) as dl2:
            with self.assertRaises(NoMarketDataError):
                stockstats_utils.load_ohlcv("FAKE", "2026-01-01")
            self.assertTrue(dl2.called)


@pytest.mark.unit
class TestRouteToVendorSentinel(unittest.TestCase):
    def test_no_data_from_all_vendors_returns_sentinel(self):
        def raises_no_data(symbol, *a, **k):
            raise NoMarketDataError(symbol, "GC=F", "no rows")

        patched = {"yfinance": raises_no_data, "alpha_vantage": raises_no_data}
        with mock.patch.dict(
            interface.VENDOR_METHODS, {"get_stock_data": patched}, clear=False
        ):
            result = interface.route_to_vendor(
                "get_stock_data", "XAUUSD+", "2026-01-01", "2026-01-10"
            )
        self.assertIn("NO_DATA_AVAILABLE", result)
        self.assertIn("XAUUSD+", result)
        self.assertIn("GC=F", result)
        self.assertIn("Do not estimate", result)

    def test_unconfigured_fallback_does_not_mask_no_data(self):
        # When the primary vendor reports no data and the fallback is simply
        # unavailable (e.g. missing API key -> raises), the no-data sentinel
        # must win rather than the fallback's incidental error crashing out.
        def raises_no_data(symbol, *a, **k):
            raise NoMarketDataError(symbol, symbol, "no rows")

        def raises_unavailable(symbol, *a, **k):
            raise ValueError("ALPHA_VANTAGE_API_KEY environment variable is not set.")

        patched = {"yfinance": raises_no_data, "alpha_vantage": raises_unavailable}
        with mock.patch.dict(
            interface.VENDOR_METHODS, {"get_stock_data": patched}, clear=False
        ):
            result = interface.route_to_vendor(
                "get_stock_data", "FAKE", "2026-01-01", "2026-01-10"
            )
        self.assertIn("NO_DATA_AVAILABLE", result)

    def test_all_vendors_rate_limited_returns_temporary_sentinel(self):
        # Yahoo in cooldown AND FMP over quota: this is transient, not a bad
        # symbol, so the router must return a "try later" sentinel rather than
        # crashing the analyst node with RuntimeError.
        def raises_rate_limit(symbol, *a, **k):
            raise VendorRateLimitError("vendor throttled")

        patched = {
            "yfinance": raises_rate_limit,
            "fmp": raises_rate_limit,
            "alpha_vantage": raises_rate_limit,
        }
        with mock.patch.dict(
            interface.VENDOR_METHODS, {"get_stock_data": patched}, clear=False
        ):
            result = interface.route_to_vendor(
                "get_stock_data", "MU", "2026-01-01", "2026-01-10"
            )
        self.assertIn("TEMPORARILY_UNAVAILABLE", result)
        self.assertIn("Do not estimate", result)

    def test_rate_limit_beats_incidental_fallback_error(self):
        # Yahoo cooling (VendorRateLimitError) + a fallback vendor's incidental
        # non-rate-limit failure (e.g. FMP 5xx -> RuntimeError) must degrade to
        # the retryable sentinel, NOT crash the node by re-raising first_error.
        def cooling(symbol, *a, **k):
            raise VendorRateLimitError("Yahoo in cooldown")

        def transient_5xx(symbol, *a, **k):
            raise RuntimeError("FMP returned a non-JSON response")

        # Override all three so no real vendor impl runs; yfinance/alpha_vantage
        # are rate-limited, fmp hits an incidental error.
        patched = {"yfinance": cooling, "alpha_vantage": cooling, "fmp": transient_5xx}
        with mock.patch.dict(
            interface.VENDOR_METHODS, {"get_stock_data": patched}, clear=False
        ):
            result = interface.route_to_vendor(
                "get_stock_data", "MU", "2026-01-01", "2026-01-10"
            )
        self.assertIn("TEMPORARILY_UNAVAILABLE", result)


if __name__ == "__main__":
    unittest.main()
