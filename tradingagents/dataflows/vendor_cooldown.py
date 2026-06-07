"""Process-wide rate-limit circuit breaker for data vendors.

When a vendor (currently Yahoo Finance) returns HTTP 429, the source IP is in
a cooldown that routinely outlasts our per-call retry budget. Without a breaker
EVERY subsequent vendor call in the same run re-pays the full retry backoff
(~tens of seconds each) before failing over — across three analysts and the
~seven data methods they each touch, that compounds into minutes of dead
waiting (the "Yahoo Finance timed out" symptom users report).

The breaker records the rate limit once. While it is open, vendor adapters
short-circuit immediately with :class:`VendorRateLimitError` so the routing
layer fails over to the next vendor (e.g. FMP) without waiting. The breaker
re-opens (re-probes the vendor) after the cooldown window so a transient
throttle does not disable the vendor for the life of a long-running process.

State is module-global and lock-guarded so it is shared correctly when
``analyst_concurrency_limit`` runs analysts on multiple threads.
"""

from __future__ import annotations

import threading
import time

# How long to skip a vendor after a rate limit, in seconds. Yahoo cooldowns
# commonly last several minutes; re-probe after this window rather than
# abandoning the vendor for the whole process.
DEFAULT_COOLDOWN_SECONDS = 600.0

_lock = threading.Lock()
# vendor name -> monotonic deadline after which the vendor may be retried.
_deadlines: dict[str, float] = {}


def record_rate_limit(
    vendor: str = "yfinance", cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS
) -> None:
    """Open the breaker for ``vendor`` for ``cooldown_seconds`` from now."""
    with _lock:
        _deadlines[vendor] = time.monotonic() + cooldown_seconds


def in_cooldown(vendor: str = "yfinance") -> bool:
    """True while ``vendor`` is within its rate-limit cooldown window.

    Expired entries are cleared on read so the next call probes the vendor
    again instead of staying blocked forever.
    """
    with _lock:
        deadline = _deadlines.get(vendor)
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            del _deadlines[vendor]
            return False
        return True


def seconds_remaining(vendor: str = "yfinance") -> float:
    """Seconds left in ``vendor``'s cooldown, or 0.0 if not in cooldown."""
    with _lock:
        deadline = _deadlines.get(vendor)
        if deadline is None:
            return 0.0
        return max(0.0, deadline - time.monotonic())


def raise_if_cooling(vendor: str = "yfinance") -> None:
    """Raise :class:`VendorRateLimitError` immediately if ``vendor`` is cooling.

    Lets a vendor adapter skip a doomed retry backoff at the top of a call so
    the routing layer fails over to the next vendor without waiting. Imported
    lazily to keep this module dependency-free for the simple state helpers.
    """
    if in_cooldown(vendor):
        from .vendor_errors import VendorRateLimitError

        raise VendorRateLimitError(
            f"{vendor} in rate-limit cooldown "
            f"({seconds_remaining(vendor):.0f}s remaining)"
        )


def reset(vendor: str | None = None) -> None:
    """Clear cooldown state for ``vendor`` (or all vendors when ``None``).

    Primarily for tests, which must not let a tripped breaker leak across
    cases, but also usable to force an immediate Yahoo re-probe.
    """
    with _lock:
        if vendor is None:
            _deadlines.clear()
        else:
            _deadlines.pop(vendor, None)
