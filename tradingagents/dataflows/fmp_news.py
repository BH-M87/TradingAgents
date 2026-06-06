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
