"""Bank of Canada USD/CAD rates: fetch, realised volatility and a rate provider.

I/O is confined to `fetch_usd_cad_series`; everything else is pure. Detail goes to
the `abl.fxdata` logger so a failed or odd run can be diagnosed from the log.
"""

import json
import logging
import math
import statistics
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from urllib.error import URLError
from urllib.request import Request, urlopen

from .fx import FXProvider

log = logging.getLogger(__name__)

BOC_SERIES = "FXUSDCAD"
BOC_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json"
BOC_SOURCE = "Bank of Canada FXUSDCAD (daily average)"
NOT_CONFIGURED_SOURCE = "not configured"
UNAVAILABLE_SOURCE = "Bank of Canada unavailable"
TRADING_DAYS_PER_YEAR = 252
VOL_WINDOW_DAYS = 30
MIN_RETURNS = 10  # fewer daily returns than this is too thin to call a volatility
MAX_STALE_DAYS = 10  # newest rate older than this at as-of is treated as missing
BODY_SNIPPET = 500

type RateSeries = Mapping[date, Decimal]  # CAD per 1 USD, by business day
type Fetcher = Callable[[str, float], bytes]


class FXDataError(RuntimeError):
    """Raised when rates cannot be fetched, parsed or are too thin to use."""


@dataclass(frozen=True, slots=True)
class RealizedVolatility:
    """Annualised standard deviation of daily log returns over a trailing window."""

    annualized: float
    returns: int
    window_start: date
    window_end: date


@dataclass(frozen=True, slots=True)
class VolatilityReading:
    """Realised volatility, if it could be measured, and the source it came from."""

    source: str
    volatility: RealizedVolatility | None = None


def _http_get(url: str, timeout: float) -> bytes:
    """Fetches a URL and returns the raw body."""
    request = Request(url, headers={"User-Agent": "abl-reporter"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_boc_observations(body: bytes) -> dict[date, Decimal]:
    """Parses a Valet `observations` JSON payload into CAD per USD by date."""
    try:
        rows = json.loads(body)["observations"]
        return {
            date.fromisoformat(row["d"]): Decimal(row[BOC_SERIES]["v"]) for row in rows
        }
    except (ValueError, KeyError, TypeError, ArithmeticError) as err:
        snippet = body[:BODY_SNIPPET].decode(errors="replace")
        log.error("Unparseable Bank of Canada payload (%s): %s", err, snippet)
        raise FXDataError(f"Unexpected Bank of Canada response: {err!r}") from err


def fetch_usd_cad_series(
    start: date, end: date, get: Fetcher = _http_get, timeout: float = 10.0
) -> dict[date, Decimal]:
    """Downloads daily USD/CAD from the Bank of Canada Valet API."""
    url = f"{BOC_URL.format(series=BOC_SERIES)}?start_date={start}&end_date={end}"
    log.info("Fetching USD/CAD rates %s to %s", start, end)
    log.debug("GET %s (timeout %.0fs)", url, timeout)
    began = time.monotonic()
    try:
        body = get(url, timeout)
    except (URLError, TimeoutError, OSError) as err:
        log.error("Bank of Canada request failed: %r | url=%s", err, url)
        raise FXDataError(f"Could not reach Bank of Canada: {err!r}") from err
    series = parse_boc_observations(body)
    log.info(
        "Fetched %d rates in %.2fs (%d bytes)",
        len(series),
        time.monotonic() - began,
        len(body),
    )
    return series


def _latest_on_or_before(series: RateSeries, as_of: date) -> date:
    """Returns the newest rate date at or before as_of, or raises if none/stale."""
    dates = [day for day in series if day <= as_of]
    if not dates:
        raise FXDataError(f"No USD/CAD rate on or before {as_of}")
    latest = max(dates)
    if (as_of - latest).days > MAX_STALE_DAYS:
        raise FXDataError(f"Newest USD/CAD rate is {latest}, too old for {as_of}")
    return latest


def rate_on(series: RateSeries, as_of: date) -> Decimal:
    """Returns CAD per USD on as_of, using the prior business day on non-fixings."""
    return series[_latest_on_or_before(series, as_of)]


def realized_volatility(
    series: RateSeries, as_of: date, window_days: int = VOL_WINDOW_DAYS
) -> RealizedVolatility:
    """Annualised volatility of daily log returns over the window ending at as_of."""
    window_end = _latest_on_or_before(series, as_of)
    window_start = window_end - timedelta(days=window_days)
    days = sorted(day for day in series if window_start <= day <= window_end)
    rates = [float(series[day]) for day in days]
    returns = [math.log(b / a) for a, b in zip(rates, rates[1:])]
    if len(returns) < MIN_RETURNS:
        raise FXDataError(
            f"Only {len(returns)} daily returns in {window_start}..{window_end}; "
            f"need {MIN_RETURNS}"
        )
    annualized = statistics.stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)
    return RealizedVolatility(annualized, len(returns), window_start, window_end)


def make_series_fx_provider(
    series: RateSeries, bank_spread: Decimal = Decimal("0")
) -> FXProvider:
    """Builds an FX provider from a USD/CAD series, with an optional bank spread.

    The bank buys USD at mid less the spread and sells it at mid plus the spread, so
    USD to CAD gets fewer dollars and CAD to USD costs more. Zero is mid-market.
    """

    def get_rate(from_curr: str, to_curr: str, as_of: date) -> Decimal:
        pair = (from_curr.upper(), to_curr.upper())
        if pair[0] == pair[1]:
            return Decimal("1.00")
        mid = rate_on(series, as_of)
        match pair:
            case ("USD", "CAD"):
                return mid * (Decimal("1") - bank_spread)
            case ("CAD", "USD"):
                return (Decimal("1") / (mid * (Decimal("1") + bank_spread))).quantize(
                    Decimal("0.00000001"), rounding=ROUND_HALF_UP
                )
        raise ValueError(f"Unsupported currency pair: {from_curr} to {to_curr}")

    return get_rate


def _series_start(as_of: date) -> date:
    """Returns how far back to fetch so a full volatility window is covered."""
    return as_of - timedelta(days=2 * VOL_WINDOW_DAYS + MAX_STALE_DAYS)


def _volatility_or_none(series: RateSeries, as_of: date) -> RealizedVolatility | None:
    """Computes volatility, logging and returning None when the data is too thin."""
    try:
        return realized_volatility(series, as_of)
    except FXDataError as err:
        log.warning("Volatility unavailable: %s", err)
        return None


def load_volatility(
    fetch: Callable[[date, date], RateSeries] | None, as_of: date
) -> VolatilityReading:
    """Measures USD/CAD volatility; never raises, so a data outage can't stop a report.

    A fetch failure is logged in full and shows as N/A on the certificate.
    """
    if fetch is None:
        return VolatilityReading(NOT_CONFIGURED_SOURCE)
    try:
        series = fetch(_series_start(as_of), as_of)
    except FXDataError as err:
        log.error("Volatility unavailable, live rates not fetched: %s", err)
        return VolatilityReading(UNAVAILABLE_SOURCE)
    return VolatilityReading(BOC_SOURCE, _volatility_or_none(series, as_of))


def format_fx_note(reading: VolatilityReading, pinned_rate: Decimal) -> str:
    """One line for the certificate: volatility, its source and the pinned rate."""
    vol = reading.volatility
    if vol is None:
        shown = "N/A"
    else:
        shown = (
            f"{vol.annualized * 100:.1f}% annualised "
            f"({vol.returns} daily returns to {vol.window_end:%Y-%m-%d})"
        )
    return (
        f"USD/CAD {VOL_WINDOW_DAYS}-day realised volatility: {shown} "
        f"[{reading.source}]. Collateral converted at pinned USD/CAD {pinned_rate:.4f}."
    )
