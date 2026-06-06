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
