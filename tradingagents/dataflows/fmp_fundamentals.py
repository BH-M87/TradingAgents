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
