"""FMP vendor: client behavior, parsing, look-ahead filtering, routing fallback."""

import json
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


@pytest.mark.unit
def test_fmp_get_indicator_uses_fmp_ohlcv(monkeypatch):
    from tradingagents.dataflows import fmp_indicator

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
    monkeypatch.setattr(fmp_indicator, "load_fmp_ohlcv", lambda symbol, curr_date: df)

    out = fmp_indicator.get_indicator("AAPL", "close_50_sma", "2024-02-29", 3)
    assert "## close_50_sma values from 2024-02-26 to 2024-02-29:" in out
    assert "50 SMA" in out


@pytest.mark.unit
def test_fmp_balance_sheet_filters_future_periods(monkeypatch):
    from tradingagents.dataflows import fmp_fundamentals

    reports = [
        {"date": "2024-12-31", "totalAssets": 300},
        {"date": "2024-03-31", "totalAssets": 200},
        {"date": "2023-12-31", "totalAssets": 100},
    ]
    monkeypatch.setattr(
        fmp_fundamentals, "_make_api_request", lambda endpoint, params=None: reports
    )

    out = fmp_fundamentals.get_balance_sheet("AAPL", freq="quarterly", curr_date="2024-06-30")
    parsed = json.loads(out)
    dates = [r["date"] for r in parsed]
    assert dates == ["2024-03-31", "2023-12-31"]  # 2024-12-31 dropped


@pytest.mark.unit
def test_fmp_fundamentals_combines_profile(monkeypatch):
    from tradingagents.dataflows import fmp_fundamentals

    def fake_request(endpoint, params=None):
        if endpoint == "profile":
            return [{"symbol": "AAPL", "companyName": "Apple Inc."}]
        if endpoint == "ratios":
            return [{"peRatio": 30}]
        if endpoint == "key-metrics":
            return [{"marketCap": 1000}]
        return []

    monkeypatch.setattr(fmp_fundamentals, "_make_api_request", fake_request)

    out = fmp_fundamentals.get_fundamentals("AAPL", curr_date="2024-06-30")
    parsed = json.loads(out)
    assert parsed["profile"]["companyName"] == "Apple Inc."
    assert parsed["ratios"]["peRatio"] == 30
    assert parsed["key_metrics"]["marketCap"] == 1000


@pytest.mark.unit
def test_fmp_get_news_returns_json(monkeypatch):
    from tradingagents.dataflows import fmp_news

    articles = [{"symbol": "AAPL", "publishedDate": "2024-06-01 10:00:00", "title": "X"}]
    monkeypatch.setattr(fmp_news, "_make_api_request", lambda endpoint, params=None: articles)

    out = fmp_news.get_news("AAPL", "2024-05-01", "2024-06-30")
    assert json.loads(out)[0]["title"] == "X"


@pytest.mark.unit
def test_fmp_global_news_filters_window(monkeypatch):
    from tradingagents.dataflows import fmp_news

    articles = [
        {"publishedDate": "2024-06-05 10:00:00", "title": "in"},
        {"publishedDate": "2024-05-01 10:00:00", "title": "out"},
    ]
    monkeypatch.setattr(fmp_news, "_make_api_request", lambda endpoint, params=None: articles)

    out = fmp_news.get_global_news("2024-06-06", look_back_days=7, limit=50)
    titles = [a["title"] for a in json.loads(out)]
    assert titles == ["in"]


@pytest.mark.unit
def test_fmp_insider_returns_json(monkeypatch):
    from tradingagents.dataflows import fmp_news

    txns = [{"symbol": "AAPL", "transactionType": "P-Purchase"}]
    monkeypatch.setattr(fmp_news, "_make_api_request", lambda endpoint, params=None: txns)

    out = fmp_news.get_insider_transactions("AAPL")
    assert json.loads(out)[0]["transactionType"] == "P-Purchase"
