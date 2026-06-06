"""Tests that outbound HTTPS fetchers verify TLS against certifi's CA bundle.

The python.org macOS framework build ships without a populated system CA
store, so urllib's default context raises CERTIFICATE_VERIFY_FAILED on every
HTTPS request (issue surfaced via StockTwits/Reddit fetches). The fetchers must
pass an explicit certifi-backed SSL context to urlopen.
"""

from __future__ import annotations

import ssl
from unittest.mock import patch

import pytest

from tradingagents.dataflows import reddit, stocktwits
from tradingagents.dataflows.utils import make_verified_ssl_context


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


@pytest.mark.unit
def test_make_verified_ssl_context_verifies():
    ctx = make_verified_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


@pytest.mark.unit
def test_stocktwits_passes_verified_context():
    captured = {}

    def fake_urlopen(req, *a, **k):
        captured.update(k)
        return _Resp(b'{"messages": []}')

    with patch.object(stocktwits, "urlopen", side_effect=fake_urlopen):
        stocktwits.fetch_stocktwits_messages("AAPL")
    assert isinstance(captured.get("context"), ssl.SSLContext)


@pytest.mark.unit
def test_reddit_json_passes_verified_context():
    captured = {}

    def fake_urlopen(req, *a, **k):
        captured.update(k)
        return _Resp(b'{"data": {"children": []}}')

    with patch.object(reddit, "urlopen", side_effect=fake_urlopen):
        reddit._fetch_subreddit("AAPL", "stocks", 5, 5.0)
    assert isinstance(captured.get("context"), ssl.SSLContext)


@pytest.mark.unit
def test_reddit_rss_passes_verified_context():
    captured = {}

    def fake_urlopen(req, *a, **k):
        captured.update(k)
        return _Resp(b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')

    with patch.object(reddit, "urlopen", side_effect=fake_urlopen):
        reddit._fetch_subreddit_rss("AAPL", "stocks", 5, 5.0)
    assert isinstance(captured.get("context"), ssl.SSLContext)
