"""Vendor-routed OHLCV *frame* loader with rate-limit fallback.

The verified-snapshot path (``market_data_validator``) needs a cleaned
``DataFrame``, but the routing layer (``interface.route_to_vendor``) only
returns vendor-standard CSV *strings*. This loader applies the same
"configured primary vendor first, then fall back" policy at the frame level so
the snapshot survives a Yahoo Finance rate-limit cooldown by transparently
sourcing prices from FMP instead of degrading to a placeholder.

Only vendors that expose a cleaned OHLCV frame loader participate; the
configured ``core_stock_apis`` order is honored, with the remaining
frame-capable vendors appended as implicit fallbacks (mirroring
``route_to_vendor``).
"""

from __future__ import annotations

import logging

import pandas as pd

from .config import get_config
from .symbol_utils import NoMarketDataError
from .vendor_errors import VendorRateLimitError

logger = logging.getLogger(__name__)


def _yfinance_frame(symbol: str, curr_date: str) -> pd.DataFrame:
    # Imported lazily so this module stays import-cheap and avoids any import
    # ordering coupling with the vendor adapters.
    from .stockstats_utils import load_ohlcv

    return load_ohlcv(symbol, curr_date)


def _fmp_frame(symbol: str, curr_date: str) -> pd.DataFrame:
    from .fmp_stock import load_fmp_ohlcv

    return load_fmp_ohlcv(symbol, curr_date)


# vendor name -> cleaned-frame loader. Vendors without a frame loader (e.g.
# alpha_vantage, which only emits CSV here) are simply absent and skipped.
_FRAME_LOADERS = {
    "yfinance": _yfinance_frame,
    "fmp": _fmp_frame,
}


def _vendor_order() -> list[str]:
    """Frame-capable vendors in configured order, with the rest appended."""
    configured = get_config().get("data_vendors", {}).get("core_stock_apis", "yfinance")
    order = [v.strip() for v in str(configured).split(",") if v.strip()]
    for vendor in _FRAME_LOADERS:
        if vendor not in order:
            order.append(vendor)
    return order


def load_ohlcv_with_fallback(symbol: str, curr_date: str) -> pd.DataFrame:
    """Return a cleaned OHLCV frame, failing over across frame-capable vendors.

    Tries each configured vendor in turn. A rate limit or "no data" from one
    vendor moves on to the next; a genuine "no data from every vendor" surfaces
    as :class:`NoMarketDataError`, and "every vendor rate-limited" surfaces as
    :class:`VendorRateLimitError`, so callers can tell a delisted symbol apart
    from a transient outage.
    """
    last_no_data: NoMarketDataError | None = None
    last_rate_limit: VendorRateLimitError | None = None

    for vendor in _vendor_order():
        loader = _FRAME_LOADERS.get(vendor)
        if loader is None:
            continue
        try:
            return loader(symbol, curr_date)
        except VendorRateLimitError as exc:  # includes FMPRateLimitError
            last_rate_limit = exc
            logger.info(
                "OHLCV frame: %s rate-limited for %s; trying next vendor", vendor, symbol
            )
            continue
        except NoMarketDataError as exc:
            last_no_data = exc
            continue
        except Exception as exc:  # noqa: BLE001 — incidental (e.g. FMP key missing)
            logger.info(
                "OHLCV frame: %s unavailable for %s (%s); trying next vendor",
                vendor,
                symbol,
                exc,
            )
            continue

    # A genuine "no rows anywhere" takes precedence over a transient rate limit
    # so a delisted symbol still reads as no-data rather than "try again later".
    if last_no_data is not None:
        raise last_no_data
    if last_rate_limit is not None:
        raise last_rate_limit
    raise NoMarketDataError(symbol, symbol, "no OHLCV from any configured vendor")
