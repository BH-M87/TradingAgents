"""FMP vendor: client behavior, parsing, look-ahead filtering, routing fallback."""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

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


def _mock_response(status_code=200, json_payload=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_payload
    resp.raise_for_status.return_value = None
    return resp


@pytest.mark.unit
def test_fmp_no_api_key_raises_not_configured(monkeypatch):
    from tradingagents.dataflows.fmp_common import _make_api_request, FMPNotConfiguredError

    monkeypatch.delenv("FMP_API_KEY", raising=False)
    with pytest.raises(FMPNotConfiguredError):
        _make_api_request("profile", {"symbol": "AAPL"})


@pytest.mark.unit
def test_fmp_http_429_raises_rate_limit(monkeypatch):
    from tradingagents.dataflows.fmp_common import _make_api_request, FMPRateLimitError

    monkeypatch.setenv("FMP_API_KEY", "test-key")
    with patch("tradingagents.dataflows.fmp_common.requests.get", return_value=_mock_response(429)):
        with pytest.raises(FMPRateLimitError):
            _make_api_request("profile", {"symbol": "AAPL"})


@pytest.mark.unit
def test_fmp_premium_error_message_raises_rate_limit(monkeypatch):
    from tradingagents.dataflows.fmp_common import _make_api_request, FMPRateLimitError

    monkeypatch.setenv("FMP_API_KEY", "test-key")
    payload = {"Error Message": "Limit Reach . Please upgrade your plan."}
    with patch("tradingagents.dataflows.fmp_common.requests.get", return_value=_mock_response(200, payload)):
        with pytest.raises(FMPRateLimitError):
            _make_api_request("news/stock", {"symbols": "AAPL"})


@pytest.mark.unit
def test_fmp_symbol_normalizes():
    from tradingagents.dataflows.fmp_common import fmp_symbol

    assert fmp_symbol("aapl+") == "AAPL"
    assert fmp_symbol("  msft ") == "MSFT"


@pytest.mark.unit
def test_fmp_get_stock_builds_csv(monkeypatch):
    from tradingagents.dataflows import fmp_stock

    rows = [
        {"date": "2024-01-03", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 1000},
        {"date": "2024-01-02", "open": 10.2, "high": 11.2, "low": 9.7, "close": 10.7, "volume": 1100},
    ]
    monkeypatch.setattr(fmp_stock, "_make_api_request", lambda endpoint, params=None: rows)

    out = fmp_stock.get_stock("AAPL", "2024-01-01", "2024-01-31")
    assert "# Stock data for AAPL" in out
    assert "2024-01-02" in out
    assert "2024-01-03" in out
    assert "Open,High,Low,Close,Volume" in out


@pytest.mark.unit
def test_fmp_get_stock_empty_raises_no_data(monkeypatch):
    from tradingagents.dataflows import fmp_stock
    from tradingagents.dataflows.symbol_utils import NoMarketDataError

    monkeypatch.setattr(fmp_stock, "_make_api_request", lambda endpoint, params=None: [])
    with pytest.raises(NoMarketDataError):
        fmp_stock.get_stock("NOPE", "2024-01-01", "2024-01-31")
