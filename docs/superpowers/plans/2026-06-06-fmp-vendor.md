# FMP Vendor Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Financial Modeling Prep (FMP) as a third, optional data vendor covering all five data categories (OHLCV, indicators, fundamentals, news, insider transactions) without changing any default.

**Architecture:** Mirror the existing `alpha_vantage_*` vendor split. FMP modules call a shared `fmp_common` HTTP client; the routing layer in `interface.py` gains an `fmp` entry per method. Free-tier plan-gating (premium/limit) raises a new `VendorRateLimitError`-derived error so the router transparently falls back to yfinance. Technical indicators are computed locally with stockstats on FMP-sourced OHLCV via a newly extracted `indicators_common` module shared with the yfinance path.

**Tech Stack:** Python, `requests`, `pandas`, `stockstats`, `pytest`/`unittest.mock`. Package: `tradingagents/dataflows`. Tests run with `uv run pytest`.

**Spec:** [docs/superpowers/specs/2026-06-06-fmp-vendor-design.md](../specs/2026-06-06-fmp-vendor-design.md)

**Implementation notes / deviations from spec:**
- `indicator_window_from_frame` drops the unused `symbol` parameter the spec sketched (it was only ever used to load OHLCV, which the caller now does). Signature: `indicator_window_from_frame(ohlcv_df, indicator, curr_date, look_back_days)`.
- The OHLCV→CSV serializer is kept local to `fmp_stock.py` (a focused ~12-line helper) rather than extracted and shared with `y_finance.get_YFin_data_online`. Rationale: avoids a second mutation of the well-tested yfinance hot path; the duplication is small and the header-label logic differs per vendor. The indicators extraction (Task 2) is the only refactor of `y_finance.py`.

---

## File Structure

| File | Responsibility |
|------|------|
| `tradingagents/dataflows/vendor_errors.py` (new) | `VendorRateLimitError` shared base class |
| `tradingagents/dataflows/indicators_common.py` (new) | Vendor-neutral indicator window logic (`BEST_IND_PARAMS`, bulk stockstats calc, window string) |
| `tradingagents/dataflows/fmp_common.py` (new) | FMP HTTP client, errors, API key, symbol normalization |
| `tradingagents/dataflows/fmp_stock.py` (new) | `get_stock`, `load_fmp_ohlcv`, local OHLCV CSV serializer |
| `tradingagents/dataflows/fmp_indicator.py` (new) | `get_indicator` (delegates to `indicators_common`) |
| `tradingagents/dataflows/fmp_fundamentals.py` (new) | `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement` |
| `tradingagents/dataflows/fmp_news.py` (new) | `get_news`, `get_global_news`, `get_insider_transactions` |
| `tradingagents/dataflows/fmp.py` (new) | Dispatcher re-exporting all FMP functions |
| `tradingagents/dataflows/alpha_vantage_common.py` (modify) | `AlphaVantageRateLimitError` inherits `VendorRateLimitError` |
| `tradingagents/dataflows/y_finance.py` (modify) | Rewire indicator window to `indicators_common` |
| `tradingagents/dataflows/interface.py` (modify) | Register `fmp` vendor; catch `VendorRateLimitError` |
| `tradingagents/default_config.py` (modify) | Comment-only: list `fmp` as an option |
| `tests/test_fmp_vendor.py` (new) | Unit + routing-integration tests (all HTTP mocked) |

---

## Task 1: Shared `VendorRateLimitError` base class + routing catch

**Files:**
- Create: `tradingagents/dataflows/vendor_errors.py`
- Modify: `tradingagents/dataflows/alpha_vantage_common.py:51-53`
- Modify: `tradingagents/dataflows/interface.py:25` (import) and `:162` (except clause)
- Test: `tests/test_fmp_vendor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_fmp_vendor.py` with:

```python
"""FMP vendor: client behavior, parsing, look-ahead filtering, routing fallback."""

import pytest

from tradingagents.dataflows.vendor_errors import VendorRateLimitError
from tradingagents.dataflows.alpha_vantage_common import AlphaVantageRateLimitError


@pytest.mark.unit
def test_alpha_vantage_rate_limit_is_vendor_rate_limit():
    assert issubclass(AlphaVantageRateLimitError, VendorRateLimitError)
    err = AlphaVantageRateLimitError("rate limited")
    assert isinstance(err, VendorRateLimitError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.dataflows.vendor_errors'`

- [ ] **Step 3: Create `vendor_errors.py`**

```python
"""Shared vendor error types for the data routing layer.

Living in their own module (rather than a vendor-specific one) lets every
vendor's "rate limited / plan-gated, skip me" condition share a single base
class that the router catches uniformly, without cross-vendor imports.
"""


class VendorRateLimitError(Exception):
    """A vendor cannot serve this request right now for a non-fatal reason.

    Raised for rate limits, daily-quota exhaustion, or plan-gated (premium)
    endpoints. The routing layer treats this as "try the next vendor" and
    does NOT record it as the surfaced error, so a genuine primary-vendor
    failure still wins.
    """
```

- [ ] **Step 4: Make `AlphaVantageRateLimitError` inherit the base**

In `tradingagents/dataflows/alpha_vantage_common.py`, change the class (currently lines 51-53):

```python
from .vendor_errors import VendorRateLimitError


class AlphaVantageRateLimitError(VendorRateLimitError):
    """Exception raised when Alpha Vantage API rate limit is exceeded."""
    pass
```

Add the `from .vendor_errors import VendorRateLimitError` import near the top of the file with the other imports (after the existing `from io import StringIO` line).

- [ ] **Step 5: Update routing to catch the base class**

In `tradingagents/dataflows/interface.py`, update the import block (around line 25) to add:

```python
from .vendor_errors import VendorRateLimitError
```

Then change the except clause at line 162 from `except AlphaVantageRateLimitError:` to:

```python
        except VendorRateLimitError:
            continue  # Rate limits / plan-gated endpoints: try the next vendor
```

The existing `from .alpha_vantage_common import AlphaVantageRateLimitError` import on line 25 can stay or be removed; leave it to minimize churn (it is no longer referenced in the except but harmless).

- [ ] **Step 6: Run tests to verify pass + no regression**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py -v && uv run pytest -q 2>&1 | tail -4`
Expected: new test PASSES; full suite shows no new failures.

- [ ] **Step 7: Commit**

```bash
git add tradingagents/dataflows/vendor_errors.py tradingagents/dataflows/alpha_vantage_common.py tradingagents/dataflows/interface.py tests/test_fmp_vendor.py
git commit -m "refactor(dataflows): shared VendorRateLimitError base for fallback routing"
```

---

## Task 2: Extract `indicators_common` and rewire yfinance

**Files:**
- Create: `tradingagents/dataflows/indicators_common.py`
- Modify: `tradingagents/dataflows/y_finance.py:61-198` (`get_stock_stats_indicators_window`) and remove now-unused `_get_stock_stats_bulk:201-232`
- Test: `tests/test_fmp_vendor.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fmp_vendor.py`:

```python
import pandas as pd


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py::test_indicator_window_from_frame_formats_window -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.dataflows.indicators_common'`

- [ ] **Step 3: Create `indicators_common.py`**

```python
"""Vendor-neutral technical-indicator computation over an OHLCV DataFrame.

The indicator math (stockstats) and the windowed-string output are identical
regardless of where the OHLCV data came from. Only the data source differs,
so callers load their own OHLCV frame and pass it in. This lets the yfinance
and FMP vendors share one implementation.
"""

from datetime import datetime

import pandas as pd
from dateutil.relativedelta import relativedelta
from stockstats import wrap

# Supported indicator keys mapped to their human-readable descriptions,
# appended to every window result so the agent gets usage guidance.
BEST_IND_PARAMS = {
    "close_50_sma": (
        "50 SMA: A medium-term trend indicator. "
        "Usage: Identify trend direction and serve as dynamic support/resistance. "
        "Tips: It lags price; combine with faster indicators for timely signals."
    ),
    "close_200_sma": (
        "200 SMA: A long-term trend benchmark. "
        "Usage: Confirm overall market trend and identify golden/death cross setups. "
        "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
    ),
    "close_10_ema": (
        "10 EMA: A responsive short-term average. "
        "Usage: Capture quick shifts in momentum and potential entry points. "
        "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
    ),
    "macd": (
        "MACD: Computes momentum via differences of EMAs. "
        "Usage: Look for crossovers and divergence as signals of trend changes. "
        "Tips: Confirm with other indicators in low-volatility or sideways markets."
    ),
    "macds": (
        "MACD Signal: An EMA smoothing of the MACD line. "
        "Usage: Use crossovers with the MACD line to trigger trades. "
        "Tips: Should be part of a broader strategy to avoid false positives."
    ),
    "macdh": (
        "MACD Histogram: Shows the gap between the MACD line and its signal. "
        "Usage: Visualize momentum strength and spot divergence early. "
        "Tips: Can be volatile; complement with additional filters in fast-moving markets."
    ),
    "rsi": (
        "RSI: Measures momentum to flag overbought/oversold conditions. "
        "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
        "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
    ),
    "boll": (
        "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
        "Usage: Acts as a dynamic benchmark for price movement. "
        "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
    ),
    "boll_ub": (
        "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
        "Usage: Signals potential overbought conditions and breakout zones. "
        "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
    ),
    "boll_lb": (
        "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
        "Usage: Indicates potential oversold conditions. "
        "Tips: Use additional analysis to avoid false reversal signals."
    ),
    "atr": (
        "ATR: Averages true range to measure volatility. "
        "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
        "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
    ),
    "vwma": (
        "VWMA: A moving average weighted by volume. "
        "Usage: Confirm trends by integrating price action with volume data. "
        "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
    ),
    "mfi": (
        "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
        "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
        "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
    ),
}


def _bulk_indicator(ohlcv_df: pd.DataFrame, indicator: str) -> dict:
    """Compute ``indicator`` for every row, returning {date_str: value_str}."""
    df = wrap(ohlcv_df.copy())
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    df[indicator]  # trigger stockstats to calculate the indicator column

    result = {}
    for _, row in df.iterrows():
        value = row[indicator]
        result[row["Date"]] = "N/A" if pd.isna(value) else str(value)
    return result


def indicator_window_from_frame(
    ohlcv_df: pd.DataFrame,
    indicator: str,
    curr_date: str,
    look_back_days: int,
) -> str:
    """Return a dated window of ``indicator`` values plus its description.

    ``ohlcv_df`` must have a datetime ``Date`` column and OHLCV columns.
    """
    if indicator not in BEST_IND_PARAMS:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(BEST_IND_PARAMS.keys())}"
        )

    end_date = curr_date
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_date_dt - relativedelta(days=look_back_days)

    indicator_data = _bulk_indicator(ohlcv_df, indicator)

    ind_string = ""
    current_dt = curr_date_dt
    while current_dt >= before:
        date_str = current_dt.strftime("%Y-%m-%d")
        value = indicator_data.get(
            date_str, "N/A: Not a trading day (weekend or holiday)"
        )
        ind_string += f"{date_str}: {value}\n"
        current_dt = current_dt - relativedelta(days=1)

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {end_date}:\n\n"
        + ind_string
        + "\n\n"
        + BEST_IND_PARAMS.get(indicator, "No description available.")
    )
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py::test_indicator_window_from_frame_formats_window -v`
Expected: PASS

- [ ] **Step 5: Rewire yfinance to use the shared module**

In `tradingagents/dataflows/y_finance.py`, add to the imports near the top (next to the existing `from .stockstats_utils import ...`):

```python
from .indicators_common import BEST_IND_PARAMS, indicator_window_from_frame
```

Replace the entire body of `get_stock_stats_indicators_window` (lines 61-198) with:

```python
def get_stock_stats_indicators_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    if indicator not in BEST_IND_PARAMS:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(BEST_IND_PARAMS.keys())}"
        )

    try:
        data = load_ohlcv(symbol, curr_date)
        return indicator_window_from_frame(data, indicator, curr_date, look_back_days)
    except NoMarketDataError:
        raise  # Unknown/delisted symbol — let the router emit the sentinel
    except Exception as e:
        print(f"Error getting bulk stockstats data: {e}")
        # Fallback to per-day computation if the bulk path fails.
        end_date = curr_date
        curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        before = curr_date_dt - relativedelta(days=look_back_days)
        ind_string = ""
        d = curr_date_dt
        while d >= before:
            indicator_value = get_stockstats_indicator(
                symbol, indicator, d.strftime("%Y-%m-%d")
            )
            ind_string += f"{d.strftime('%Y-%m-%d')}: {indicator_value}\n"
            d = d - relativedelta(days=1)
        return (
            f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {end_date}:\n\n"
            + ind_string
            + "\n\n"
            + BEST_IND_PARAMS.get(indicator, "No description available.")
        )
```

Then delete the now-unused `_get_stock_stats_bulk` function (originally lines 201-232). Verify nothing else references it:

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && grep -rn "_get_stock_stats_bulk" tradingagents/ tests/`
Expected: no matches after deletion.

- [ ] **Step 6: Verify imports still resolve and full suite is green**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run python -c "import tradingagents.dataflows.y_finance" && uv run pytest -q 2>&1 | tail -4`
Expected: import OK; no new failures.

- [ ] **Step 7: Commit**

```bash
git add tradingagents/dataflows/indicators_common.py tradingagents/dataflows/y_finance.py tests/test_fmp_vendor.py
git commit -m "refactor(dataflows): extract vendor-neutral indicator window logic"
```

---

## Task 3: FMP HTTP client (`fmp_common.py`)

**Files:**
- Create: `tradingagents/dataflows/fmp_common.py`
- Test: `tests/test_fmp_vendor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fmp_vendor.py`:

```python
from unittest.mock import MagicMock, patch


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py -k fmp -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.dataflows.fmp_common'`

- [ ] **Step 3: Create `fmp_common.py`**

```python
"""Shared HTTP client and helpers for the Financial Modeling Prep vendor.

Targets FMP's "stable" API. The API key is read directly from the
``FMP_API_KEY`` environment variable (mirroring the Alpha Vantage vendor),
not from the dataflows config. Plan-gated or rate-limited responses raise
``FMPRateLimitError`` so the routing layer falls back to another vendor.
"""

import logging
import os

import requests

from .vendor_errors import VendorRateLimitError

logger = logging.getLogger(__name__)

_BASE_URL = "https://financialmodelingprep.com/stable"

# Substrings FMP uses in its "Error Message" body when an endpoint is not
# available on the caller's plan or the daily quota is exhausted.
_PLAN_GATE_MARKERS = (
    "limit reach",
    "premium",
    "exclusive endpoint",
    "special endpoint",
    "upgrade",
)


class FMPNotConfiguredError(ValueError):
    """Raised when FMP is selected but ``FMP_API_KEY`` is not set.

    Subclasses ValueError for parity with the Alpha Vantage vendor.
    """


class FMPRateLimitError(VendorRateLimitError):
    """Raised when FMP is rate-limited or the endpoint is plan-gated.

    The routing layer treats this as "skip this vendor" and falls back.
    """


def get_api_key() -> str:
    """Return the FMP API key or raise ``FMPNotConfiguredError``."""
    api_key = os.getenv("FMP_API_KEY")
    if not api_key:
        raise FMPNotConfiguredError("FMP_API_KEY environment variable is not set.")
    return api_key


def fmp_symbol(symbol: str) -> str:
    """Normalize a user/broker symbol to FMP's plain-ticker convention.

    Free-tier FMP is US equities only, so this just upper-cases and strips a
    trailing broker CFD ``+`` marker. It deliberately does NOT apply Yahoo's
    forex/crypto conventions (those are wrong for FMP).
    """
    if not isinstance(symbol, str):
        return symbol
    return symbol.strip().rstrip("+").upper()


def _make_api_request(endpoint: str, params: dict | None = None):
    """GET ``endpoint`` on the FMP stable API and return parsed JSON.

    Raises:
        FMPNotConfiguredError: no API key configured.
        FMPRateLimitError: HTTP 401/402/403/429 or a plan-gate error body.
        RuntimeError: non-JSON response or a non-plan-gate FMP error body.
    """
    api_params = dict(params or {})
    api_params["apikey"] = get_api_key()
    url = f"{_BASE_URL}/{endpoint.lstrip('/')}"

    response = requests.get(url, params=api_params, timeout=30)

    status = response.status_code
    if status in (401, 402, 403, 429):
        if status == 401:
            logger.warning("FMP returned HTTP 401 (invalid API key?) for %s", endpoint)
        raise FMPRateLimitError(
            f"FMP request to {endpoint!r} blocked (HTTP {status}); falling back."
        )
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"FMP returned a non-JSON response for {endpoint!r}") from exc

    if isinstance(payload, dict) and "Error Message" in payload:
        message = str(payload["Error Message"])
        if any(marker in message.lower() for marker in _PLAN_GATE_MARKERS):
            raise FMPRateLimitError(
                f"FMP endpoint {endpoint!r} unavailable on current plan: {message}"
            )
        raise RuntimeError(f"FMP error for {endpoint!r}: {message}")

    return payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py -k fmp -v`
Expected: the four new tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/fmp_common.py tests/test_fmp_vendor.py
git commit -m "feat(dataflows): FMP HTTP client with plan-gate detection"
```

---

## Task 4: FMP OHLCV (`fmp_stock.py`)

**Files:**
- Create: `tradingagents/dataflows/fmp_stock.py`
- Test: `tests/test_fmp_vendor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fmp_vendor.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py -k get_stock -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.dataflows.fmp_stock'`

- [ ] **Step 3: Create `fmp_stock.py`**

```python
"""FMP OHLCV price data: raw CSV output and a cleaned DataFrame loader."""

from datetime import datetime

import pandas as pd

from .fmp_common import _make_api_request, fmp_symbol
from .stockstats_utils import _clean_dataframe
from .symbol_utils import NoMarketDataError

# FMP historical-price field names -> the OHLCV columns stockstats expects.
_COLUMN_MAP = {
    "date": "Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}


def load_fmp_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch ~5y of FMP daily OHLCV up to today, cleaned and look-ahead trimmed.

    Returns a DataFrame with a datetime ``Date`` column and OHLCV columns,
    rows on/before ``curr_date`` only. Raises ``NoMarketDataError`` when FMP
    returns nothing for the symbol.
    """
    canonical = fmp_symbol(symbol)

    today = pd.Timestamp.today()
    start_str = (today - pd.DateOffset(years=5)).strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")

    payload = _make_api_request(
        "historical-price-eod/full",
        {"symbol": canonical, "from": start_str, "to": end_str},
    )
    # Stable API returns a flat list; legacy shape is {"historical": [...]}.
    rows = payload.get("historical") if isinstance(payload, dict) else payload
    if not rows:
        raise NoMarketDataError(symbol, canonical, "FMP returned no price rows")

    df = pd.DataFrame(rows).rename(columns=_COLUMN_MAP)
    if "Close" not in df.columns:
        raise NoMarketDataError(symbol, canonical, "FMP price rows missing close")

    df = _clean_dataframe(df).sort_values("Date")
    df = df[df["Date"] <= pd.to_datetime(curr_date)]
    if df.empty:
        raise NoMarketDataError(
            symbol, canonical, f"no FMP rows on or before {curr_date}"
        )
    return df


def _to_ohlcv_csv(df: pd.DataFrame, header_label: str, start_date: str, end_date: str) -> str:
    """Serialize a cleaned OHLCV frame to the vendor-standard CSV string."""
    df = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in df.columns:
            df[col] = df[col].round(2)
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    out = df.set_index("Date")[cols]
    out.index = out.index.strftime("%Y-%m-%d")
    csv_string = out.to_csv()

    header = f"# Stock data for {header_label} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(df)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    return header + csv_string


def get_stock(symbol: str, start_date: str, end_date: str) -> str:
    """Return FMP daily OHLCV between ``start_date`` and ``end_date`` as CSV."""
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    canonical = fmp_symbol(symbol)
    data = load_fmp_ohlcv(symbol, end_date)

    window = data[
        (data["Date"] >= pd.to_datetime(start_date))
        & (data["Date"] <= pd.to_datetime(end_date))
    ].copy()
    if window.empty:
        raise NoMarketDataError(
            symbol, canonical, f"no rows between {start_date} and {end_date}"
        )

    label = canonical if canonical == symbol.strip().upper() else f"{canonical} (from {symbol})"
    return _to_ohlcv_csv(window, label, start_date, end_date)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py -k get_stock -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/fmp_stock.py tests/test_fmp_vendor.py
git commit -m "feat(dataflows): FMP OHLCV loader and CSV output"
```

---

## Task 5: FMP indicators (`fmp_indicator.py`)

**Files:**
- Create: `tradingagents/dataflows/fmp_indicator.py`
- Test: `tests/test_fmp_vendor.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fmp_vendor.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py::test_fmp_get_indicator_uses_fmp_ohlcv -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.dataflows.fmp_indicator'`

- [ ] **Step 3: Create `fmp_indicator.py`**

```python
"""FMP technical indicators, computed locally with stockstats on FMP OHLCV.

FMP's native indicator endpoint has partial coverage (no Bollinger bands,
MACD sub-series, or VWMA) and costs one call per indicator. Instead we reuse
the same stockstats math the yfinance path uses, sourcing prices from FMP.
"""

from .fmp_stock import load_fmp_ohlcv
from .indicators_common import indicator_window_from_frame


def get_indicator(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int,
    interval: str = "daily",
    time_period: int = 14,
    series_type: str = "close",
) -> str:
    """Return a dated window of ``indicator`` values from FMP-sourced OHLCV.

    ``interval`` / ``time_period`` / ``series_type`` are accepted for vendor
    signature compatibility; the stockstats indicator keys encode their own
    periods, so they are not used here.
    """
    data = load_fmp_ohlcv(symbol, curr_date)
    return indicator_window_from_frame(data, indicator, curr_date, look_back_days)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py::test_fmp_get_indicator_uses_fmp_ohlcv -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/fmp_indicator.py tests/test_fmp_vendor.py
git commit -m "feat(dataflows): FMP technical indicators via local stockstats"
```

---

## Task 6: FMP fundamentals (`fmp_fundamentals.py`)

**Files:**
- Create: `tradingagents/dataflows/fmp_fundamentals.py`
- Test: `tests/test_fmp_vendor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fmp_vendor.py`:

```python
import json


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py -k fundamentals_combines -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.dataflows.fmp_fundamentals'`

- [ ] **Step 3: Create `fmp_fundamentals.py`**

```python
"""FMP company fundamentals and financial statements.

Statement endpoints return a list of fiscal periods, each with a ``date``.
Periods ending after ``curr_date`` are dropped to prevent look-ahead bias,
matching the Alpha Vantage vendor's behavior.
"""

import json

from .fmp_common import _make_api_request, fmp_symbol


def _filter_reports_by_date(reports, curr_date):
    """Drop statement periods whose ``date`` is after ``curr_date``."""
    if not curr_date or not isinstance(reports, list):
        return reports
    return [r for r in reports if str(r.get("date", "")) <= curr_date]


def _fmp_period(freq: str) -> str:
    """Map the project's ``freq`` to FMP's period value (annual|quarter)."""
    return "quarter" if str(freq).lower().startswith("q") else "annual"


def get_fundamentals(ticker: str, curr_date: str = None) -> str:
    """Return a combined company overview (profile + ratios + key metrics)."""
    symbol = fmp_symbol(ticker)
    profile = _make_api_request("profile", {"symbol": symbol})
    ratios = _make_api_request("ratios", {"symbol": symbol, "limit": 1})
    metrics = _make_api_request("key-metrics", {"symbol": symbol, "limit": 1})

    def _first(data):
        return data[0] if isinstance(data, list) and data else data

    overview = {
        "profile": _first(profile),
        "ratios": _first(ratios),
        "key_metrics": _first(metrics),
    }
    return json.dumps(overview, indent=2)


def get_balance_sheet(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """Return FMP balance sheet statements, look-ahead trimmed."""
    reports = _make_api_request(
        "balance-sheet-statement",
        {"symbol": fmp_symbol(ticker), "period": _fmp_period(freq), "limit": 8},
    )
    return json.dumps(_filter_reports_by_date(reports, curr_date), indent=2)


def get_cashflow(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """Return FMP cash flow statements, look-ahead trimmed."""
    reports = _make_api_request(
        "cash-flow-statement",
        {"symbol": fmp_symbol(ticker), "period": _fmp_period(freq), "limit": 8},
    )
    return json.dumps(_filter_reports_by_date(reports, curr_date), indent=2)


def get_income_statement(ticker: str, freq: str = "quarterly", curr_date: str = None) -> str:
    """Return FMP income statements, look-ahead trimmed."""
    reports = _make_api_request(
        "income-statement",
        {"symbol": fmp_symbol(ticker), "period": _fmp_period(freq), "limit": 8},
    )
    return json.dumps(_filter_reports_by_date(reports, curr_date), indent=2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py -k "fundamentals_combines or filters_future" -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/fmp_fundamentals.py tests/test_fmp_vendor.py
git commit -m "feat(dataflows): FMP fundamentals with look-ahead filtering"
```

---

## Task 7: FMP news & insider (`fmp_news.py`)

**Files:**
- Create: `tradingagents/dataflows/fmp_news.py`
- Test: `tests/test_fmp_vendor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fmp_vendor.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py -k "get_news or global_news or insider" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tradingagents.dataflows.fmp_news'`

- [ ] **Step 3: Create `fmp_news.py`**

```python
"""FMP ticker news, global market news, and insider transactions.

On the free plan these endpoints are commonly plan-gated; ``fmp_common``
raises ``FMPRateLimitError`` in that case and the router falls back.
"""

import json
from datetime import datetime, timedelta

from .config import get_config
from .fmp_common import _make_api_request, fmp_symbol


def get_news(ticker: str, start_date: str, end_date: str) -> str:
    """Return ticker-specific news articles as a JSON string."""
    limit = get_config().get("news_article_limit", 20)
    articles = _make_api_request(
        "news/stock",
        {
            "symbols": fmp_symbol(ticker),
            "from": start_date,
            "to": end_date,
            "limit": limit,
        },
    )
    return json.dumps(articles, indent=2)


def get_global_news(curr_date: str, look_back_days: int = 7, limit: int = 50) -> str:
    """Return general market news within the look-back window as JSON.

    FMP's general-news endpoint is not date-filterable, so the window is
    applied client-side on the ``publishedDate`` field.
    """
    articles = _make_api_request("news/general-latest", {"page": 0, "limit": limit})
    start_date = (
        datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days)
    ).strftime("%Y-%m-%d")

    if isinstance(articles, list):
        windowed = [
            a
            for a in articles
            if start_date <= str(a.get("publishedDate", ""))[:10] <= curr_date
        ]
        return json.dumps(windowed, indent=2)
    return json.dumps(articles, indent=2)


def get_insider_transactions(symbol: str) -> str:
    """Return insider transactions for ``symbol`` as a JSON string."""
    txns = _make_api_request(
        "insider-trading/search",
        {"symbol": fmp_symbol(symbol), "page": 0, "limit": 100},
    )
    return json.dumps(txns, indent=2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py -k "get_news or global_news or insider" -v`
Expected: all three tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/dataflows/fmp_news.py tests/test_fmp_vendor.py
git commit -m "feat(dataflows): FMP news and insider transactions"
```

---

## Task 8: Register FMP vendor (`fmp.py`, `interface.py`, `default_config.py`)

**Files:**
- Create: `tradingagents/dataflows/fmp.py`
- Modify: `tradingagents/dataflows/interface.py` (imports, `VENDOR_LIST`, `VENDOR_METHODS`)
- Modify: `tradingagents/default_config.py:101-106` (comments only)
- Test: `tests/test_fmp_vendor.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fmp_vendor.py`:

```python
@pytest.mark.unit
def test_fmp_registered_for_all_methods():
    from tradingagents.dataflows.interface import VENDOR_LIST, VENDOR_METHODS

    assert "fmp" in VENDOR_LIST
    for method, vendors in VENDOR_METHODS.items():
        assert "fmp" in vendors, f"{method} missing fmp implementation"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py::test_fmp_registered_for_all_methods -v`
Expected: FAIL — `assert 'fmp' in ['yfinance', 'alpha_vantage']`

- [ ] **Step 3: Create `fmp.py` dispatcher**

```python
"""Financial Modeling Prep vendor entry points (mirrors alpha_vantage.py)."""

from .fmp_stock import get_stock
from .fmp_indicator import get_indicator
from .fmp_fundamentals import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
)
from .fmp_news import get_news, get_global_news, get_insider_transactions
```

- [ ] **Step 4: Register the vendor in `interface.py`**

Add an import block after the existing `from .alpha_vantage_common import ...` line (around line 25):

```python
from .fmp import (
    get_stock as get_fmp_stock,
    get_indicator as get_fmp_indicator,
    get_fundamentals as get_fmp_fundamentals,
    get_balance_sheet as get_fmp_balance_sheet,
    get_cashflow as get_fmp_cashflow,
    get_income_statement as get_fmp_income_statement,
    get_news as get_fmp_news,
    get_global_news as get_fmp_global_news,
    get_insider_transactions as get_fmp_insider_transactions,
)
```

Update `VENDOR_LIST` (lines 64-67) to:

```python
VENDOR_LIST = [
    "yfinance",
    "alpha_vantage",
    "fmp",
]
```

Add an `"fmp"` entry to every method in `VENDOR_METHODS` (lines 70-111). The full updated dict:

```python
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
        "fmp": get_fmp_stock,
    },
    # technical_indicators
    "get_indicators": {
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
        "fmp": get_fmp_indicator,
    },
    # fundamental_data
    "get_fundamentals": {
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
        "fmp": get_fmp_fundamentals,
    },
    "get_balance_sheet": {
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
        "fmp": get_fmp_balance_sheet,
    },
    "get_cashflow": {
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
        "fmp": get_fmp_cashflow,
    },
    "get_income_statement": {
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
        "fmp": get_fmp_income_statement,
    },
    # news_data
    "get_news": {
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
        "fmp": get_fmp_news,
    },
    "get_global_news": {
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
        "fmp": get_fmp_global_news,
    },
    "get_insider_transactions": {
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
        "fmp": get_fmp_insider_transactions,
    },
}
```

- [ ] **Step 5: Update `default_config.py` comments (no value change)**

In `tradingagents/default_config.py`, update the `data_vendors` block comments (lines 102-105) to list `fmp` as an option:

```python
    "data_vendors": {
        "core_stock_apis": "yfinance",       # Options: alpha_vantage, yfinance, fmp
        "technical_indicators": "yfinance",  # Options: alpha_vantage, yfinance, fmp
        "fundamental_data": "yfinance",      # Options: alpha_vantage, yfinance, fmp
        "news_data": "yfinance",             # Options: alpha_vantage, yfinance, fmp
    },
```

- [ ] **Step 6: Run test + import check**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run python -c "import tradingagents.dataflows.interface" && uv run pytest tests/test_fmp_vendor.py::test_fmp_registered_for_all_methods -v`
Expected: import OK; test PASSES.

- [ ] **Step 7: Commit**

```bash
git add tradingagents/dataflows/fmp.py tradingagents/dataflows/interface.py tradingagents/default_config.py tests/test_fmp_vendor.py
git commit -m "feat(dataflows): register fmp as a selectable data vendor"
```

---

## Task 9: Routing fallback integration test + full regression

**Files:**
- Test: `tests/test_fmp_vendor.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fmp_vendor.py`:

```python
import copy

import tradingagents.default_config as default_config
from tradingagents.dataflows.config import set_config


@pytest.mark.unit
def test_router_falls_back_from_fmp_to_yfinance(monkeypatch):
    from tradingagents.dataflows import interface
    from tradingagents.dataflows.fmp_common import FMPRateLimitError

    # Select fmp as the primary vendor for OHLCV.
    cfg = copy.deepcopy(default_config.DEFAULT_CONFIG)
    cfg["data_vendors"]["core_stock_apis"] = "fmp"
    set_config(cfg)

    def fmp_blocked(*args, **kwargs):
        raise FMPRateLimitError("plan-gated")

    def yfin_ok(*args, **kwargs):
        return "YFIN_DATA"

    monkeypatch.setitem(interface.VENDOR_METHODS["get_stock_data"], "fmp", fmp_blocked)
    monkeypatch.setitem(interface.VENDOR_METHODS["get_stock_data"], "yfinance", yfin_ok)

    result = interface.route_to_vendor("get_stock_data", "AAPL", "2024-01-01", "2024-01-31")
    assert result == "YFIN_DATA"

    # Restore default config for test isolation.
    set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest tests/test_fmp_vendor.py::test_router_falls_back_from_fmp_to_yfinance -v`
Expected: PASS (the routing layer's `except VendorRateLimitError` from Task 1 makes this work).

- [ ] **Step 3: Full suite + lint**

Run: `cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork && uv run pytest -q 2>&1 | tail -6 && ruff check tradingagents/dataflows/`
Expected: all tests pass (no new failures vs. baseline); ruff reports no new issues in the new files.

- [ ] **Step 4: Commit**

```bash
git add tests/test_fmp_vendor.py
git commit -m "test(dataflows): FMP->yfinance routing fallback integration test"
```

---

## Manual verification (optional, requires a real free FMP key)

With `FMP_API_KEY` exported, confirm the live free-tier behavior end-to-end:

```bash
cd /Users/liuxuheng/OwnDevWorkspaces/TradingAgentsFork
FMP_API_KEY=<your-key> uv run python -c "
from tradingagents.dataflows.config import set_config
import tradingagents.default_config as dc, copy
cfg = copy.deepcopy(dc.DEFAULT_CONFIG)
cfg['data_vendors']['core_stock_apis'] = 'fmp'
cfg['data_vendors']['fundamental_data'] = 'fmp'
set_config(cfg)
from tradingagents.dataflows.interface import route_to_vendor
print(route_to_vendor('get_stock_data', 'AAPL', '2024-01-02', '2024-01-10')[:400])
print(route_to_vendor('get_fundamentals', 'AAPL', '2024-06-30')[:400])
"
```

Expected: OHLCV CSV from FMP for AAPL; a JSON fundamentals overview. News/insider may fall back to yfinance on free tier (visible if you also switch `news_data` to `fmp` and compare).
