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
