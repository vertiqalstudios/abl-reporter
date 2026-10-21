import json
import logging
from datetime import date, timedelta
from decimal import Decimal
from urllib.error import URLError

import pytest

from abl.fx import make_fixed_fx_provider
from abl.fxdata import (
    BOC_SOURCE,
    FALLBACK_SOURCE,
    FXDataError,
    FXMarket,
    RateSeries,
    SeriesFXProvider,
    fetch_usd_cad_series,
    format_fx_note,
    load_fx_market,
    parse_boc_observations,
    rate_on,
    realized_volatility,
)

FRIDAY = date(2026, 10, 23)


def _series(rates: list[str], end: date = FRIDAY) -> dict[date, Decimal]:
    """Builds one rate per calendar day ending at `end`."""
    days = [end - timedelta(days=len(rates) - 1 - i) for i in range(len(rates))]
    return {day: Decimal(rate) for day, rate in zip(days, rates)}


def _payload(rows: dict[str, str]) -> bytes:
    return json.dumps(
        {"observations": [{"d": d, "FXUSDCAD": {"v": v}} for d, v in rows.items()]}
    ).encode()


def test_parse_boc_observations() -> None:
    body = _payload({"2026-10-20": "1.3950", "2026-10-21": "1.4000"})
    assert parse_boc_observations(body) == {
        date(2026, 10, 20): Decimal("1.3950"),
        date(2026, 10, 21): Decimal("1.4000"),
    }


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"observations": [{"d": 1}]}'])
def test_parse_boc_observations_rejects_bad_payloads(body: bytes) -> None:
    with pytest.raises(FXDataError, match="Unexpected Bank of Canada response"):
        parse_boc_observations(body)


def test_fetch_wraps_network_errors_and_logs_the_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failing_get(url: str, timeout: float) -> bytes:
        raise URLError("no route")

    with caplog.at_level(logging.ERROR, logger="abl.fxdata"):
        with pytest.raises(FXDataError, match="Could not reach"):
            fetch_usd_cad_series(date(2026, 1, 1), FRIDAY, get=failing_get)
    assert "start_date=2026-01-01" in caplog.text


def test_fetch_requests_the_window_and_parses() -> None:
    seen: list[str] = []

    def get(url: str, timeout: float) -> bytes:
        seen.append(url)
        return _payload({"2026-10-21": "1.4000"})

    series = fetch_usd_cad_series(date(2026, 10, 1), FRIDAY, get=get)
    assert series == {date(2026, 10, 21): Decimal("1.4000")}
    assert "start_date=2026-10-01&end_date=2026-10-23" in seen[0]


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (date(2026, 10, 21), Decimal("1.40")),
        (date(2026, 10, 24), Decimal("1.40")),  # weekend -> last fixing
    ],
)
def test_rate_on_uses_latest_fixing(as_of: date, expected: Decimal) -> None:
    series: RateSeries = {
        date(2026, 10, 20): Decimal("1.39"),
        date(2026, 10, 21): Decimal("1.40"),
    }
    assert rate_on(series, as_of) == expected


@pytest.mark.parametrize("as_of", [date(2026, 10, 1), date(2026, 11, 15)])
def test_rate_on_rejects_missing_or_stale(as_of: date) -> None:
    series: RateSeries = {date(2026, 10, 20): Decimal("1.39")}
    with pytest.raises(FXDataError):
        rate_on(series, as_of)


def test_constant_rates_have_zero_volatility() -> None:
    vol = realized_volatility(_series(["1.40"] * 31), FRIDAY)
    assert vol.annualized == 0.0
    assert vol.returns == 30
    assert vol.window_end == FRIDAY


def test_alternating_rates_give_known_volatility() -> None:
    # Returns alternate +r/-r: sample stdev is r*sqrt(n/(n-1)), annualised x sqrt(252)
    rates = ["1.40", "1.4140"] * 15 + ["1.40"]
    vol = realized_volatility(_series(rates), FRIDAY)
    assert 0.15 < vol.annualized < 0.17


def test_thin_window_raises() -> None:
    with pytest.raises(FXDataError, match="daily returns"):
        realized_volatility(_series(["1.40"] * 5), FRIDAY)


def test_series_provider_gives_mid_market_and_its_fixing_date() -> None:
    fx = SeriesFXProvider(
        {date(2026, 10, 21): Decimal("1.40")}, bank_spread=Decimal("0.02")
    )
    assert fx("USD", "CAD", FRIDAY) == Decimal("1.40")
    assert fx("CAD", "USD", FRIDAY) == Decimal("0.71428571")
    assert fx("CAD", "CAD", FRIDAY) == Decimal("1.00")
    assert fx.rate_date(FRIDAY) == date(2026, 10, 21)
    assert fx.source == BOC_SOURCE


@pytest.mark.parametrize(
    ("spread", "usd_cad", "cad_usd"),
    [
        (Decimal("0"), Decimal("1.4000"), Decimal("0.71428571")),
        (Decimal("0.02"), Decimal("1.3720"), Decimal("0.70028011")),
    ],
)
def test_conversion_rate_applies_the_bank_spread(
    spread: Decimal, usd_cad: Decimal, cad_usd: Decimal
) -> None:
    fx = SeriesFXProvider({FRIDAY: Decimal("1.40")}, bank_spread=spread)
    assert fx.conversion_rate("USD", "CAD", FRIDAY) == usd_cad
    assert fx.conversion_rate("CAD", "USD", FRIDAY) == cad_usd


def test_series_provider_rejects_unknown_pairs() -> None:
    fx = SeriesFXProvider({FRIDAY: Decimal("1.40")})
    with pytest.raises(ValueError, match="Unsupported currency pair"):
        fx("EUR", "CAD", FRIDAY)


def test_load_fx_market_without_fetcher_is_pinned() -> None:
    market = load_fx_market(None, date(2026, 1, 1), FRIDAY)
    assert market.provider.source == "Pinned 1.4400"
    assert market.volatility is None
    assert market.provider("USD", "CAD", FRIDAY) == Decimal("1.44")


def test_load_fx_market_live_prices_and_measures_volatility() -> None:
    series = _series(["1.40", "1.41"] * 40)
    market = load_fx_market(lambda start, end: series, date(2026, 8, 20), FRIDAY)
    assert market.provider.source == BOC_SOURCE
    assert market.provider("USD", "CAD", FRIDAY) == series[FRIDAY]
    assert market.volatility is not None


def test_load_fx_market_fetches_from_the_first_invoice() -> None:
    requested: list[tuple[date, date]] = []

    def fetch(start: date, end: date) -> RateSeries:
        requested.append((start, end))
        return _series(["1.40", "1.41"] * 150)

    load_fx_market(fetch, date(2026, 1, 15), FRIDAY)
    start, end = requested[0]
    assert start < date(2026, 1, 15)
    assert end == FRIDAY


def test_load_fx_market_falls_back_and_logs_on_fetch_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(start: date, end: date) -> RateSeries:
        raise FXDataError("boom")

    with caplog.at_level(logging.ERROR, logger="abl.fxdata"):
        market = load_fx_market(broken, date(2026, 1, 1), FRIDAY)
    assert market.provider.source == FALLBACK_SOURCE
    assert market.provider("USD", "CAD", FRIDAY) == Decimal("1.44")
    assert market.volatility is None
    assert "boom" in caplog.text


def test_load_fx_market_falls_back_when_history_is_not_covered() -> None:
    recent = _series(["1.40"] * 40)  # nothing near the first invoice
    market = load_fx_market(lambda start, end: recent, date(2025, 6, 30), FRIDAY)
    assert market.provider.source == FALLBACK_SOURCE


def test_load_fx_market_keeps_live_rates_when_volatility_is_thin() -> None:
    series = _series(["1.40"] * 3)
    market = load_fx_market(lambda start, end: series, FRIDAY, FRIDAY)
    assert market.provider.source == BOC_SOURCE
    assert market.volatility is None


def test_format_fx_note_without_volatility() -> None:
    note = format_fx_note(FXMarket(make_fixed_fx_provider()), FRIDAY)
    assert "USD/CAD spot 1.4400 [Pinned 1.4400, rate date 2026-10-23]" in note
    assert "realised volatility: N/A" in note


def test_format_fx_note_names_spot_source_fixing_and_volatility() -> None:
    series = _series(["1.40", "1.4140"] * 15 + ["1.40"], end=date(2026, 10, 21))
    market = FXMarket(
        SeriesFXProvider(series), realized_volatility(series, date(2026, 10, 21))
    )
    note = format_fx_note(market, FRIDAY)
    assert "USD/CAD spot 1.4000" in note
    assert f"[{BOC_SOURCE}, rate date 2026-10-21]" in note  # not the future Friday
    assert "30-day realised volatility: 16.1% annualised (30 daily returns)" in note
