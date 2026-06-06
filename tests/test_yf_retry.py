"""Tests for yf_retry / load_ohlcv resilience to Yahoo Finance rate limiting.

yfinance signals a 429 two different ways and load_ohlcv must handle both:
  - it RAISES YFRateLimitError -> yf_retry's backoff retries, and
  - it SWALLOWS the 429 into an empty frame while recording the cause in
    yfinance.shared._ERRORS -> load_ohlcv must detect that and retry, rather
    than mistaking it for a delisted/invalid symbol (which must fail fast).
"""

import os
import unittest
from unittest import mock

import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError

from tradingagents.dataflows import stockstats_utils
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.symbol_utils import NoMarketDataError


def _good_frame():
    idx = pd.date_range("2026-01-01", periods=5, freq="D")
    return pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 100},
        index=idx,
    )


@pytest.mark.unit
class TestLoadOhlcvRateLimit(unittest.TestCase):
    def setUp(self):
        self._tmp = os.path.join(os.path.dirname(__file__), "_tmp_cache_retry")
        os.makedirs(self._tmp, exist_ok=True)
        set_config({"data_cache_dir": self._tmp})
        self._sleep = mock.patch.object(stockstats_utils.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)

    def tearDown(self):
        for f in os.listdir(self._tmp):
            os.remove(os.path.join(self._tmp, f))
        os.rmdir(self._tmp)

    def test_invalid_symbol_empty_frame_fails_fast(self):
        # An empty frame with no rate-limit marker is a delisted/invalid symbol:
        # raise immediately, never sleep/retry.
        with mock.patch.object(stockstats_utils, "_download_was_rate_limited", return_value=False), \
             mock.patch.object(stockstats_utils.yf, "download", return_value=pd.DataFrame()) as dl:
            with self.assertRaises(NoMarketDataError):
                stockstats_utils.load_ohlcv("FAKE", "2026-01-05")
        self.assertEqual(dl.call_count, 1)
        self.assertFalse(self._sleep.called)

    def test_swallowed_rate_limit_retries_then_succeeds(self):
        frames = [pd.DataFrame(), pd.DataFrame(), _good_frame()]
        with mock.patch.object(stockstats_utils, "_download_was_rate_limited", return_value=True), \
             mock.patch.object(stockstats_utils.yf, "download", side_effect=lambda *a, **k: frames.pop(0)) as dl:
            df = stockstats_utils.load_ohlcv("MU", "2026-01-05")
        self.assertEqual(dl.call_count, 3)
        self.assertTrue(self._sleep.called)
        self.assertFalse(df.empty)

    def test_swallowed_rate_limit_exhausts_raises_no_market_data(self):
        with mock.patch.object(stockstats_utils, "_download_was_rate_limited", return_value=True), \
             mock.patch.object(stockstats_utils.yf, "download", return_value=pd.DataFrame()):
            with self.assertRaises(NoMarketDataError):
                stockstats_utils.load_ohlcv("MU", "2026-01-05")
        # Retried up to the configured ceiling before giving up.
        self.assertTrue(self._sleep.call_count >= 3)

    def test_raised_rate_limit_retries_then_succeeds(self):
        calls = {"n": 0}

        def side_effect(*a, **k):
            calls["n"] += 1
            if calls["n"] < 3:
                raise YFRateLimitError()
            return _good_frame()

        with mock.patch.object(stockstats_utils.yf, "download", side_effect=side_effect) as dl:
            df = stockstats_utils.load_ohlcv("MU", "2026-01-05")
        self.assertEqual(dl.call_count, 3)
        self.assertFalse(df.empty)


@pytest.mark.unit
class TestDownloadWasRateLimited(unittest.TestCase):
    def _set_errors(self, mapping):
        from yfinance import shared
        patcher = mock.patch.object(shared, "_ERRORS", mapping)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_detects_rate_limit_marker(self):
        self._set_errors({"MU": "YFRateLimitError('Too Many Requests. Rate limited.')"})
        self.assertTrue(stockstats_utils._download_was_rate_limited("MU"))

    def test_delisted_marker_is_not_rate_limit(self):
        self._set_errors({"FAKE": "possibly delisted; no price data found"})
        self.assertFalse(stockstats_utils._download_was_rate_limited("FAKE"))

    def test_missing_symbol_is_not_rate_limit(self):
        self._set_errors({})
        self.assertFalse(stockstats_utils._download_was_rate_limited("MU"))


if __name__ == "__main__":
    unittest.main()
