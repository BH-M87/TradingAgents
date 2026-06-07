"""TradingAgentsGraph._fetch_returns must participate in the Yahoo breaker.

This best-effort outcome-resolution call (run at the very start of a run, before
the analysts) uses raw yf.Ticker().history(). It must skip Yahoo while the
breaker is open and trip the breaker on a 429 so the rest of the run fails over
fast instead of each call re-paying the retry backoff. It does not touch ``self``,
so we exercise it as an unbound method with a stub instance.
"""

from unittest import mock

import pytest
from yfinance.exceptions import YFRateLimitError

import tradingagents.graph.trading_graph as tg
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.dataflows import vendor_cooldown


@pytest.fixture(autouse=True)
def _clean():
    vendor_cooldown.reset()
    yield
    vendor_cooldown.reset()


@pytest.mark.unit
def test_fetch_returns_rate_limit_trips_breaker():
    with mock.patch.object(tg.yf, "Ticker", side_effect=YFRateLimitError()):
        out = TradingAgentsGraph._fetch_returns(object(), "MU", "2026-01-01")
    assert out == (None, None, None)
    assert vendor_cooldown.in_cooldown("yfinance") is True


@pytest.mark.unit
def test_fetch_returns_short_circuits_when_cooling():
    vendor_cooldown.record_rate_limit("yfinance")
    with mock.patch.object(tg.yf, "Ticker") as ticker:
        out = TradingAgentsGraph._fetch_returns(object(), "MU", "2026-01-01")
    assert out == (None, None, None)
    assert not ticker.called  # honored the breaker without touching the network
