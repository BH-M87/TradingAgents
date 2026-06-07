import time
import logging

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError
from stockstats import wrap
from typing import Annotated
import os
from .config import get_config
from .utils import safe_ticker_component
from .symbol_utils import normalize_symbol, NoMarketDataError
from .vendor_errors import VendorRateLimitError
from . import vendor_cooldown

logger = logging.getLogger(__name__)


def yf_retry(func, max_retries=5, base_delay=2.0):
    """Execute a yfinance call with exponential backoff on rate limits.

    yfinance raises YFRateLimitError on HTTP 429 responses but does not
    retry them internally. This wrapper adds retry logic specifically
    for rate limits. Other exceptions propagate immediately.

    The default of 5 retries backs off 2+4+8+16+32 ≈ 62s, enough to outlast
    a short Yahoo cooldown; the original 3 (≈14s) routinely expired mid-block.
    When the retries are exhausted the Yahoo rate-limit breaker is tripped so
    every *other* Yahoo call in this run fails over to the next vendor
    immediately instead of each re-paying this full backoff (see
    ``vendor_cooldown``).
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except YFRateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Yahoo Finance rate limited, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                vendor_cooldown.record_rate_limit("yfinance")
                raise


def _download_was_rate_limited(symbol: str) -> bool:
    """True if yfinance recorded a rate-limit error for ``symbol`` last download.

    yfinance often swallows a 429 into an empty frame instead of raising —
    while stashing the cause in ``yfinance.shared._ERRORS`` (the same dict it
    prints "1 Failed download … YFRateLimitError" from). Inspect it so a
    transient rate limit (worth retrying) is told apart from a genuinely
    empty/delisted symbol (which must fail fast, not burn the retry budget).
    """
    try:
        from yfinance import shared

        errors = getattr(shared, "_ERRORS", None) or {}
        msg = errors.get(symbol) or errors.get(symbol.upper()) or ""
    except Exception:
        return False
    text = str(msg).lower()
    return any(
        tok in text
        for tok in ("rate limit", "too many requests", "429", "yfratelimit")
    )


def _ensure_date_column(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize the date column to ``Date``.

    Some yfinance builds leave the index unnamed (so ``reset_index()`` yields
    ``index``) or use ``Datetime`` for intraday data. Rename the first
    date-like column so indicators don't silently drop when it isn't ``Date``.
    """
    if "Date" in data.columns:
        return data
    for candidate in ("index", "Datetime", "date"):
        if candidate in data.columns:
            return data.rename(columns={candidate: "Date"})
    return data


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a stock DataFrame for stockstats: parse dates, drop invalid rows, fill price gaps."""
    data = _ensure_date_column(data)
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


def load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch OHLCV data with caching, filtered to prevent look-ahead bias.

    Downloads 15 years of data up to today and caches per symbol. On
    subsequent calls the cache is reused. Rows after curr_date are
    filtered out so backtests never see future prices.
    """
    # Resolve broker/forex symbols (XAUUSD+ -> GC=F) to Yahoo's convention,
    # then reject values that would escape the cache directory when
    # interpolated into the cache filename (e.g. ``../../tmp/x``).
    canonical = normalize_symbol(symbol)
    safe_symbol = safe_ticker_component(canonical)

    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    # Cache uses a fixed window (15y to today) so one file per symbol
    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today_date.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-YFin-data-{start_str}-{end_str}.csv",
    )

    # A cached file may be empty if a prior fetch failed (unknown symbol,
    # transient rate limit). Treat an empty/columnless cache as a miss and
    # re-fetch rather than serving the poisoned file forever.
    data = None
    if os.path.exists(data_file):
        cached = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
        if not cached.empty and "Close" in cached.columns:
            data = cached

    if data is None:
        # Yahoo is in a rate-limit cooldown from an earlier 429 this run. Skip
        # the (doomed, ~62s) download entirely and signal the routing layer to
        # fail over to the next vendor immediately. Cached symbols never reach
        # here, so they still serve even while Yahoo is cooling down.
        if vendor_cooldown.in_cooldown("yfinance"):
            raise VendorRateLimitError(
                f"Yahoo Finance in rate-limit cooldown "
                f"({vendor_cooldown.seconds_remaining('yfinance'):.0f}s remaining)"
            )

        def _download():
            df = yf.download(
                canonical,
                start=start_str,
                end=end_str,
                multi_level_index=False,
                progress=False,
                auto_adjust=True,
            )
            # yfinance sometimes swallows a 429 into an empty frame instead of
            # raising; re-raise so yf_retry's backoff applies. A genuinely empty
            # result (delisted/invalid symbol) falls through to NoMarketDataError
            # below without consuming the retry budget.
            if (df is None or df.empty) and _download_was_rate_limited(canonical):
                raise YFRateLimitError()
            return df

        try:
            downloaded = yf_retry(_download)
        except YFRateLimitError as exc:
            # Retries exhausted against an active Yahoo cooldown. Surface as a
            # rate-limit (not "no data") so the routing layer fails over to the
            # next vendor and never mislabels a throttled symbol as
            # delisted/invalid. yf_retry already tripped the breaker so the
            # rest of this run skips Yahoo immediately.
            vendor_cooldown.record_rate_limit("yfinance")
            raise VendorRateLimitError(
                f"Yahoo Finance rate limited for {canonical!r}; retries exhausted"
            ) from exc
        downloaded = _ensure_date_column(downloaded.reset_index())
        # Only cache real data — never persist an empty frame.
        if downloaded.empty or "Close" not in downloaded.columns:
            raise NoMarketDataError(
                symbol, canonical, "Yahoo Finance returned no rows"
            )
        downloaded.to_csv(data_file, index=False, encoding="utf-8")
        data = downloaded

    data = _clean_dataframe(data)

    # Filter to curr_date to prevent look-ahead bias in backtesting
    data = data[data["Date"] <= curr_date_dt]

    return data


def filter_financials_by_date(data: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """Drop financial statement columns (fiscal period timestamps) after curr_date.

    yfinance financial statements use fiscal period end dates as columns.
    Columns after curr_date represent future data and are removed to
    prevent look-ahead bias.
    """
    if not curr_date or data.empty:
        return data
    cutoff = pd.Timestamp(curr_date)
    mask = pd.to_datetime(data.columns, errors="coerce") <= cutoff
    return data.loc[:, mask]


class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[
            str, "curr date for retrieving stock price data, YYYY-mm-dd"
        ],
    ):
        data = load_ohlcv(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        curr_date_str = pd.to_datetime(curr_date).strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats to calculate the indicator
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
