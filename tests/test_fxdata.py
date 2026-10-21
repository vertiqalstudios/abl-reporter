import json
import logging
from datetime import date, timedelta
from decimal import Decimal
from urllib.error import URLError

import pytest

from abl.fxdata import (
    BOC_SOURCE,
    NOT_CONFIGURED_SOURCE,
    UNAVAILABLE_SOURCE,
    FXDataError,
    RateSeries,
    VolatilityReading,
    fetch_usd_cad_series,
    format_fx_note,
    load_volatility,
    make_series_fx_provider,
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


@pytest.mark.parametrize(
    ("spread", "usd_cad", "cad_usd"),
    [
        (Decimal("0"), Decimal("1.4000"), Decimal("0.71428571")),
        (Decimal("0.02"), Decimal("1.3720"), Decimal("0.70028011")),
    ],
)
def test_series_provider_applies_bank_spread(
    spread: Decimal, usd_cad: Decimal, cad_usd: Decimal
) -> None:
    fx = make_series_fx_provider({FRIDAY: Decimal("1.40")}, spread)
    assert fx("USD", "CAD", FRIDAY) == usd_cad
    assert fx("CAD", "USD", FRIDAY) == cad_usd
    assert fx("CAD", "CAD", FRIDAY) == Decimal("1.00")


def test_series_provider_rejects_unknown_pairs() -> None:
    fx = make_series_fx_provider({FRIDAY: Decimal("1.40")})
    with pytest.raises(ValueError, match="Unsupported currency pair"):
        fx("EUR", "CAD", FRIDAY)


def test_load_volatility_without_fetcher_is_not_configured() -> None:
    reading = load_volatility(None, FRIDAY)
    assert reading == VolatilityReading(NOT_CONFIGURED_SOURCE)


def test_load_volatility_live() -> None:
    series = _series(["1.40", "1.41"] * 20)
    reading = load_volatility(lambda start, end: series, FRIDAY)
    assert reading.source == BOC_SOURCE
    assert reading.volatility is not None


def test_load_volatility_requests_a_full_window() -> None:
    requested: list[tuple[date, date]] = []

    def fetch(start: date, end: date) -> RateSeries:
        requested.append((start, end))
        return _series(["1.40", "1.41"] * 20)

    load_volatility(fetch, FRIDAY)
    start, end = requested[0]
    assert (end - start).days >= 60
    assert end == FRIDAY


def test_load_volatility_logs_and_reports_na_on_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(start: date, end: date) -> RateSeries:
        raise FXDataError("boom")

    with caplog.at_level(logging.ERROR, logger="abl.fxdata"):
        reading = load_volatility(broken, FRIDAY)
    assert reading == VolatilityReading(UNAVAILABLE_SOURCE)
    assert "boom" in caplog.text


def test_load_volatility_keeps_source_when_data_is_thin() -> None:
    reading = load_volatility(lambda start, end: _series(["1.40"] * 3), FRIDAY)
    assert reading.source == BOC_SOURCE
    assert reading.volatility is None


def test_format_fx_note_reports_na_without_volatility() -> None:
    note = format_fx_note(VolatilityReading("Somewhere"), Decimal("1.44"))
    assert "volatility: N/A [Somewhere]" in note
    assert "pinned USD/CAD 1.4400" in note


def test_format_fx_note_reports_annualised_percentage() -> None:
    vol = realized_volatility(_series(["1.40", "1.4140"] * 15 + ["1.40"]), FRIDAY)
    note = format_fx_note(VolatilityReading(BOC_SOURCE, vol), Decimal("1.44"))
    assert "annualised (30 daily returns to 2026-10-23)" in note
