from typing import Annotated
from datetime import datetime
from dateutil.relativedelta import relativedelta
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError
from .stockstats_utils import StockstatsUtils, yf_retry, load_ohlcv, filter_financials_by_date
from .symbol_utils import normalize_symbol, NoMarketDataError
from .vendor_errors import VendorRateLimitError
from . import vendor_cooldown
from .indicators_common import BEST_IND_PARAMS, indicator_window_from_frame


def _raise_if_yahoo_cooling() -> None:
    """Fast-fail a yfinance call when the Yahoo rate-limit breaker is open.

    Skips the doomed ~62s retry backoff so the routing layer fails over to the
    next vendor immediately (see ``vendor_cooldown``).
    """
    vendor_cooldown.raise_if_cooling("yfinance")

def get_YFin_data_online(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
):

    # Validate date formats early (raises ValueError on malformed input).
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    # Resolve broker/forex symbols to Yahoo's convention (XAUUSD+ -> GC=F).
    canonical = normalize_symbol(symbol)

    # Reuse the cached OHLCV frame that load_ohlcv maintains (shared with the
    # verified snapshot and indicator paths) instead of issuing a separate
    # yf.Ticker.history download. This collapses the market analyst's three
    # price fetches into a single network call per symbol — the main driver of
    # Yahoo rate-limiting — and keeps this tool consistent with the snapshot
    # (same source, so the two outputs can never disagree). A bad/delisted
    # symbol or an exhausted rate-limit surfaces here as NoMarketDataError,
    # which the routing layer turns into a single "no data" signal.
    data = load_ohlcv(symbol, end_date)

    window = data[
        (data["Date"] >= pd.to_datetime(start_date))
        & (data["Date"] <= pd.to_datetime(end_date))
    ].copy()
    if window.empty:
        raise NoMarketDataError(
            symbol, canonical, f"no rows between {start_date} and {end_date}"
        )

    # Round prices for cleaner display and emit the standard OHLCV columns,
    # Date-indexed, to match this tool's prior CSV shape.
    for col in ("Open", "High", "Low", "Close"):
        if col in window.columns:
            window[col] = window[col].round(2)
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in window.columns]
    out = window.set_index("Date")[cols]
    out.index = out.index.strftime("%Y-%m-%d")
    csv_string = out.to_csv()

    # Add header information; note the resolved symbol when it differs so the
    # agent (and user) can see which instrument was actually priced.
    label = canonical if canonical == symbol.upper() else f"{canonical} (from {symbol})"
    header = f"# Stock data for {label} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(window)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    return header + csv_string

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
    except VendorRateLimitError:
        raise  # Yahoo throttled — let the router fail over to the next vendor
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


def get_stockstats_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[
        str, "The current trading date you are trading on, YYYY-mm-dd"
    ],
) -> str:

    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    curr_date = curr_date_dt.strftime("%Y-%m-%d")

    try:
        indicator_value = StockstatsUtils.get_stock_stats(
            symbol,
            indicator,
            curr_date,
        )
    except NoMarketDataError:
        raise  # Unknown/delisted symbol — let the router emit the sentinel
    except VendorRateLimitError:
        raise  # Yahoo throttled — let the router fail over to the next vendor
    except Exception as e:
        print(
            f"Error getting stockstats indicator data for indicator {indicator} on {curr_date}: {e}"
        )
        return ""

    return str(indicator_value)


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date (not used for yfinance)"] = None
):
    """Get company fundamentals overview from yfinance."""
    canonical = normalize_symbol(ticker)
    _raise_if_yahoo_cooling()
    try:
        ticker_obj = yf.Ticker(canonical)
        info = yf_retry(lambda: ticker_obj.info)

        if not info:
            raise NoMarketDataError(ticker, canonical, "no fundamentals returned")

        fields = [
            ("Name", info.get("longName")),
            ("Sector", info.get("sector")),
            ("Industry", info.get("industry")),
            ("Market Cap", info.get("marketCap")),
            ("PE Ratio (TTM)", info.get("trailingPE")),
            ("Forward PE", info.get("forwardPE")),
            ("PEG Ratio", info.get("pegRatio")),
            ("Price to Book", info.get("priceToBook")),
            ("EPS (TTM)", info.get("trailingEps")),
            ("Forward EPS", info.get("forwardEps")),
            ("Dividend Yield", info.get("dividendYield")),
            ("Beta", info.get("beta")),
            ("52 Week High", info.get("fiftyTwoWeekHigh")),
            ("52 Week Low", info.get("fiftyTwoWeekLow")),
            ("50 Day Average", info.get("fiftyDayAverage")),
            ("200 Day Average", info.get("twoHundredDayAverage")),
            ("Revenue (TTM)", info.get("totalRevenue")),
            ("Gross Profit", info.get("grossProfits")),
            ("EBITDA", info.get("ebitda")),
            ("Net Income", info.get("netIncomeToCommon")),
            ("Profit Margin", info.get("profitMargins")),
            ("Operating Margin", info.get("operatingMargins")),
            ("Return on Equity", info.get("returnOnEquity")),
            ("Return on Assets", info.get("returnOnAssets")),
            ("Debt to Equity", info.get("debtToEquity")),
            ("Current Ratio", info.get("currentRatio")),
            ("Book Value", info.get("bookValue")),
            ("Free Cash Flow", info.get("freeCashflow")),
        ]

        lines = []
        for label, value in fields:
            if value is not None:
                lines.append(f"{label}: {value}")

        # yfinance returns a stub dict (e.g. {"trailingPegRatio": None}) for
        # unknown symbols, so `info` is truthy but every field is empty. Treat
        # "no usable fields" as no data rather than emitting a bare header the
        # agent might fabricate around.
        if not lines:
            raise NoMarketDataError(ticker, canonical, "no fundamental fields returned")

        header = f"# Company Fundamentals for {canonical}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + "\n".join(lines)

    except NoMarketDataError:
        raise
    except (VendorRateLimitError, YFRateLimitError) as exc:
        vendor_cooldown.record_rate_limit("yfinance")
        raise VendorRateLimitError(
            f"Yahoo Finance rate limited for {canonical!r}"
        ) from exc
    except Exception as e:
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
):
    """Get balance sheet data from yfinance."""
    canonical = normalize_symbol(ticker)
    _raise_if_yahoo_cooling()
    try:
        ticker_obj = yf.Ticker(canonical)

        if freq.lower() == "quarterly":
            data = yf_retry(lambda: ticker_obj.quarterly_balance_sheet)
        else:
            data = yf_retry(lambda: ticker_obj.balance_sheet)

        data = filter_financials_by_date(data, curr_date)

        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no balance sheet data")

        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        # Add header information
        header = f"# Balance Sheet data for {canonical} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except NoMarketDataError:
        raise
    except (VendorRateLimitError, YFRateLimitError) as exc:
        vendor_cooldown.record_rate_limit("yfinance")
        raise VendorRateLimitError(
            f"Yahoo Finance rate limited for {canonical!r}"
        ) from exc
    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"


def get_cashflow(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
):
    """Get cash flow data from yfinance."""
    canonical = normalize_symbol(ticker)
    _raise_if_yahoo_cooling()
    try:
        ticker_obj = yf.Ticker(canonical)

        if freq.lower() == "quarterly":
            data = yf_retry(lambda: ticker_obj.quarterly_cashflow)
        else:
            data = yf_retry(lambda: ticker_obj.cashflow)

        data = filter_financials_by_date(data, curr_date)

        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no cash flow data")

        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        # Add header information
        header = f"# Cash Flow data for {canonical} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except NoMarketDataError:
        raise
    except (VendorRateLimitError, YFRateLimitError) as exc:
        vendor_cooldown.record_rate_limit("yfinance")
        raise VendorRateLimitError(
            f"Yahoo Finance rate limited for {canonical!r}"
        ) from exc
    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"


def get_income_statement(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "frequency of data: 'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "current date in YYYY-MM-DD format"] = None
):
    """Get income statement data from yfinance."""
    canonical = normalize_symbol(ticker)
    _raise_if_yahoo_cooling()
    try:
        ticker_obj = yf.Ticker(canonical)

        if freq.lower() == "quarterly":
            data = yf_retry(lambda: ticker_obj.quarterly_income_stmt)
        else:
            data = yf_retry(lambda: ticker_obj.income_stmt)

        data = filter_financials_by_date(data, curr_date)

        if data.empty:
            raise NoMarketDataError(ticker, canonical, "no income statement data")

        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        # Add header information
        header = f"# Income Statement data for {canonical} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        return header + csv_string

    except NoMarketDataError:
        raise
    except (VendorRateLimitError, YFRateLimitError) as exc:
        vendor_cooldown.record_rate_limit("yfinance")
        raise VendorRateLimitError(
            f"Yahoo Finance rate limited for {canonical!r}"
        ) from exc
    except Exception as e:
        return f"Error retrieving income statement for {ticker}: {str(e)}"


def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol of the company"]
):
    """Get insider transactions data from yfinance."""
    canonical = normalize_symbol(ticker)
    _raise_if_yahoo_cooling()
    try:
        ticker_obj = yf.Ticker(canonical)
        data = yf_retry(lambda: ticker_obj.insider_transactions)

        # Empty is normal here (many valid symbols have no insider filings),
        # so report it plainly rather than treating the symbol as invalid.
        if data is None or data.empty:
            return f"No insider transactions reported for symbol '{canonical}'"

        # Convert to CSV string for consistency with other functions
        csv_string = data.to_csv()

        # Add header information
        header = f"# Insider Transactions data for {canonical}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        return header + csv_string

    except (VendorRateLimitError, YFRateLimitError) as exc:
        vendor_cooldown.record_rate_limit("yfinance")
        raise VendorRateLimitError(
            f"Yahoo Finance rate limited for {canonical!r}"
        ) from exc
    except Exception as e:
        return f"Error retrieving insider transactions for {ticker}: {str(e)}"