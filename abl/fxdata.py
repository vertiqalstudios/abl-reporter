"""Bank of Canada USD/CAD rates: fetch, FX provider and realised volatility.

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

from .fx import make_fixed_fx_provider
from .models import FXProvider

log = logging.getLogger(__name__)

BOC_SERIES = "FXUSDCAD"
BOC_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json"
BOC_SOURCE = "Bank of Canada FXUSDCAD (daily average)"
FALLBACK_SOURCE = "Pinned 1.4400, live unavailable"
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
class FXMarket:
    """The provider that prices collateral, plus volatility when it was measured."""

    provider: FXProvider
    volatility: RealizedVolatility | None = None


def _http_get(url: str, timeout: float) -> bytes:
    """Fetches a URL and returns the raw body."""
    request = Request(url, headers={"User-Agent": "abl-reporter"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _usable_rate(day: str, value: str) -> Decimal | None:
    """Returns the rate, or None (logged) for a blank or non-positive observation."""
    rate = Decimal(value) if value.strip() else None
    if rate is not None and rate > 0:
        return rate
    log.warning("Skipping unusable Bank of Canada rate on %s: %r", day, value)
    return None


def parse_boc_observations(body: bytes) -> dict[date, Decimal]:
    """Parses a Valet `observations` JSON payload into CAD per USD by date.

    Blank or non-positive observations are skipped, so one bad day does not
    discard the rest of the series.
    """
    try:
        rows = json.loads(body)["observations"]
        parsed = {
            date.fromisoformat(row["d"]): _usable_rate(row["d"], row[BOC_SERIES]["v"])
            for row in rows
        }
    except (ValueError, KeyError, TypeError, ArithmeticError) as err:
        snippet = body[:BODY_SNIPPET].decode(errors="replace")
        log.error("Unparseable Bank of Canada payload (%s): %s", err, snippet)
        raise FXDataError(f"Unexpected Bank of Canada response: {err!r}") from err
    return {day: rate for day, rate in parsed.items() if rate is not None}


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
    if any(rate <= 0 for rate in rates):
        raise FXDataError(f"Non-positive USD/CAD rate in {window_start}..{window_end}")
    returns = [math.log(b / a) for a, b in zip(rates, rates[1:])]
    if len(returns) < MIN_RETURNS:
        raise FXDataError(
            f"Only {len(returns)} daily returns in {window_start}..{window_end}; "
            f"need {MIN_RETURNS}"
        )
    annualized = statistics.stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)
    return RealizedVolatility(annualized, len(returns), window_start, window_end)


@dataclass(frozen=True, slots=True)
class SeriesFXProvider:
    """Mid-market FX from a USD/CAD series, using the latest fixing on or before a date.

    Calling it gives the mid rate, which values collateral. `conversion_rate` adds
    the bank spread and is for actual conversions only: the bank buys USD at mid
    less the spread and sells it at mid plus the spread.
    """

    series: RateSeries
    source: str = BOC_SOURCE
    bank_spread: Decimal = Decimal("0")

    def __call__(self, from_curr: str, to_curr: str, as_of: date) -> Decimal:
        pair = (from_curr.upper(), to_curr.upper())
        if pair[0] == pair[1]:
            return Decimal("1.00")
        mid = rate_on(self.series, as_of)
        match pair:
            case ("USD", "CAD"):
                return mid
            case ("CAD", "USD"):
                return _invert(mid)
        raise ValueError(f"Unsupported currency pair: {from_curr} to {to_curr}")

    def rate_date(self, as_of: date) -> date:
        """Returns the fixing date the rate for as_of comes from."""
        return _latest_on_or_before(self.series, as_of)

    def conversion_rate(self, from_curr: str, to_curr: str, as_of: date) -> Decimal:
        """Returns the rate the bank would give, spread included."""
        mid = self(from_curr, to_curr, as_of)
        match (from_curr.upper(), to_curr.upper()):
            case ("USD", "CAD"):
                return mid * (Decimal("1") - self.bank_spread)
            case ("CAD", "USD"):
                return _invert(_invert(mid) * (Decimal("1") + self.bank_spread))
        return mid


def _invert(rate: Decimal) -> Decimal:
    """Returns 1 / rate to eight places."""
    return (Decimal("1") / rate).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def _series_start(first_date: date, as_of: date) -> date:
    """Returns how far back to fetch: all invoice history, and a full vol window."""
    earliest = min(first_date, as_of - timedelta(days=2 * VOL_WINDOW_DAYS))
    return earliest - timedelta(days=MAX_STALE_DAYS)


def _volatility_or_none(series: RateSeries, as_of: date) -> RealizedVolatility | None:
    """Computes volatility, logging and returning None when the data is too thin."""
    try:
        return realized_volatility(series, as_of)
    except FXDataError as err:
        log.warning("Volatility unavailable: %s", err)
        return None


def _fetch_covering(
    fetch: Callable[[date, date], RateSeries], first_date: date, as_of: date
) -> RateSeries:
    """Fetches rates and checks both ends are covered, so pricing can't fail later."""
    series = fetch(_series_start(first_date, as_of), as_of)
    rate_on(series, first_date)
    rate_on(series, as_of)
    return series


def load_fx_market(
    fetch: Callable[[date, date], RateSeries] | None, first_date: date, as_of: date
) -> FXMarket:
    """Loads live rates, or the pinned rate when `fetch` is unset or fails.

    A failure is logged in full and never stops the report. The pinned fallback
    is labelled as such in its `source`, which the certificate prints.
    """
    if fetch is None:
        return FXMarket(make_fixed_fx_provider())
    try:
        series = _fetch_covering(fetch, first_date, as_of)
    except FXDataError as err:
        log.error("Live FX unavailable, using the pinned rate: %s", err)
        return FXMarket(make_fixed_fx_provider(source=FALLBACK_SOURCE))
    return FXMarket(SeriesFXProvider(series), _volatility_or_none(series, as_of))


def format_fx_note(market: FXMarket, as_of: date) -> str:
    """One line for the certificate: spot, its source and date, realised volatility."""
    provider = market.provider
    spot = provider("USD", "CAD", as_of)
    vol = market.volatility
    if vol is None:
        shown = "N/A"
    else:
        shown = f"{vol.annualized * 100:.1f}% annualised ({vol.returns} daily returns)"
    return (
        f"USD/CAD spot {spot:.4f} [{provider.source}, "
        f"rate date {provider.rate_date(as_of):%Y-%m-%d}]. "
        f"{VOL_WINDOW_DAYS}-day realised volatility: {shown}."
    )
