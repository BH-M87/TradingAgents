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
