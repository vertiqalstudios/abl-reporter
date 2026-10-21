from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

import pandas as pd

from abl.fx import FXProvider, valuation_date
from abl.models import Invoice


class AgingBucket(StrEnum):
    CURRENT = "current"
    DAYS_1_30 = "1_30"
    DAYS_31_60 = "31_60"
    DAYS_61_90 = "61_90"
    DAYS_90_PLUS = "90_plus"


@dataclass(frozen=True, slots=True)
class TimeSeriesConfig:
    lookback_days: int = 90
    step_days: int = 7


@dataclass(frozen=True, slots=True)
class WeeklyDSOSnapshot:
    as_of_date: date
    currency: str
    total_ar: Decimal
    lookback_sales: Decimal
    dso_equal_weight: Decimal | None
    dso_dollar_weight: Decimal | None
    weighted_avg_dpd: Decimal
    max_dpd: int
    bucket_current: Decimal
    bucket_1_30: Decimal
    bucket_31_60: Decimal
    bucket_61_90: Decimal
    bucket_90_plus: Decimal


type WeeklyTimeSeries = tuple[WeeklyDSOSnapshot, ...]

_CENTS = Decimal("0.01")


def _get_invoice_amount(inv: Invoice) -> Decimal:
    return inv.invoice_amount


def _convert_currency(
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    fx: FXProvider | None,
    as_of: date | None = None,
) -> Decimal:
    """Converts a monetary amount to the target currency using the FX provider."""
    if from_currency == to_currency:
        return amount
    if fx is None:
        raise ValueError(
            f"FXProvider required for conversion from {from_currency} to {to_currency}"
        )
    if to_currency == "CAD":
        eval_date = as_of or date.today()
        rate = fx(from_currency, "CAD", eval_date)
        return (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    raise ValueError(f"Unsupported target currency: {to_currency}; expected CAD")


def _convert_invoice(
    inv: Invoice, currency: str, fx: FXProvider | None, as_of: date
) -> Decimal:
    """Converts an invoice amount at its valuation-date rate (see `valuation_date`)."""
    return _convert_currency(
        _get_invoice_amount(inv),
        inv.currency,
        currency,
        fx,
        valuation_date(inv, as_of),
    )


def _is_active_ar(inv: Invoice, as_of: date) -> bool:
    """Determines if an invoice is issued on or before as_of and uncollected."""
    if inv.invoice_date > as_of:
        return False
    if inv.paid_date is not None and inv.paid_date <= as_of:
        return False
    return True


def _is_lookback_sale(inv: Invoice, as_of: date, lookback_days: int) -> bool:
    """Determines if an invoice falls within the rolling lookback sales window."""
    start_window = as_of - timedelta(days=lookback_days)
    if inv.invoice_date < start_window:
        return False
    if inv.invoice_date > as_of:
        return False
    return True


def _calculate_dpd(inv: Invoice, as_of: date) -> int:
    """Computes days past due (DPD) relative to due date, floored at zero."""
    delta_days = (as_of - inv.due_date).days
    if delta_days <= 0:
        return 0
    return delta_days


def _classify_aging_bucket(dpd: int) -> AgingBucket:
    """Maps days past due (DPD) to discrete aging categorization buckets."""
    if dpd == 0:
        return AgingBucket.CURRENT
    if dpd <= 30:
        return AgingBucket.DAYS_1_30
    if dpd <= 60:
        return AgingBucket.DAYS_31_60
    if dpd <= 90:
        return AgingBucket.DAYS_61_90
    return AgingBucket.DAYS_90_PLUS


@dataclass(frozen=True, slots=True)
class _ActiveInvoiceRecord:
    amount: Decimal
    age_days: int
    dpd: int
    bucket: AgingBucket


def _build_active_record(
    inv: Invoice,
    as_of: date,
    currency: str,
    fx: FXProvider | None,
) -> _ActiveInvoiceRecord:
    """Builds a normalized active AR record with converted amount and aging."""
    converted_amount = _convert_invoice(inv, currency, fx, as_of)
    age_days = (as_of - inv.invoice_date).days
    dpd = _calculate_dpd(inv, as_of)
    bucket = _classify_aging_bucket(dpd)
    return _ActiveInvoiceRecord(
        amount=converted_amount,
        age_days=age_days,
        dpd=dpd,
        bucket=bucket,
    )


def _empty_snapshot(
    as_of_date: date,
    currency: str,
    lookback_sales: Decimal,
    total_ar: Decimal,
) -> WeeklyDSOSnapshot:
    """Builds a zeroed-out snapshot preserving sales lookback and total AR."""
    return WeeklyDSOSnapshot(
        as_of_date=as_of_date,
        currency=currency,
        total_ar=total_ar.quantize(_CENTS, rounding=ROUND_HALF_UP),
        lookback_sales=lookback_sales.quantize(_CENTS, rounding=ROUND_HALF_UP),
        dso_equal_weight=None,
        dso_dollar_weight=None,
        weighted_avg_dpd=Decimal("0.00"),
        max_dpd=0,
        bucket_current=Decimal("0.00"),
        bucket_1_30=Decimal("0.00"),
        bucket_31_60=Decimal("0.00"),
        bucket_61_90=Decimal("0.00"),
        bucket_90_plus=Decimal("0.00"),
    )


def compute_weekly_snapshot(
    invoices: tuple[Invoice, ...],
    as_of_date: date,
    currency: str = "CAD",
    fx: FXProvider | None = None,
    config: TimeSeriesConfig = TimeSeriesConfig(),
) -> WeeklyDSOSnapshot:
    """Computes DSO and aging metrics for active AR at a snapshot date."""
    lookback_invoices = [
        inv
        for inv in invoices
        if _is_lookback_sale(inv, as_of_date, config.lookback_days)
    ]
    lookback_sales = sum(
        (_convert_invoice(inv, currency, fx, as_of_date) for inv in lookback_invoices),
        start=Decimal("0"),
    )

    active_invoices = [inv for inv in invoices if _is_active_ar(inv, as_of_date)]
    all_active_amounts = [
        _convert_invoice(inv, currency, fx, as_of_date) for inv in active_invoices
    ]
    total_ar = sum(all_active_amounts, start=Decimal("0"))

    positive_records = [
        _build_active_record(inv, as_of_date, currency, fx)
        for inv in active_invoices
        if _get_invoice_amount(inv) > Decimal("0")
    ]

    if not positive_records:
        return _empty_snapshot(as_of_date, currency, lookback_sales, total_ar)

    positive_balance_sum = sum(
        (record.amount for record in positive_records), start=Decimal("0")
    )
    if positive_balance_sum <= Decimal("0"):
        return _empty_snapshot(as_of_date, currency, lookback_sales, total_ar)

    count = Decimal(len(positive_records))
    total_age = Decimal(sum(record.age_days for record in positive_records))
    dso_equal_weight = (total_age / count).quantize(_CENTS, rounding=ROUND_HALF_UP)

    weighted_age_sum = sum(
        (record.amount * Decimal(record.age_days) for record in positive_records),
        start=Decimal("0"),
    )
    dso_dollar_weight = (weighted_age_sum / positive_balance_sum).quantize(
        _CENTS, rounding=ROUND_HALF_UP
    )

    max_dpd = max(record.dpd for record in positive_records)
    weighted_dpd_sum = sum(
        (record.amount * Decimal(record.dpd) for record in positive_records),
        start=Decimal("0"),
    )
    weighted_avg_dpd = (weighted_dpd_sum / positive_balance_sum).quantize(
        _CENTS, rounding=ROUND_HALF_UP
    )

    bucket_current = sum(
        (
            record.amount
            for record in positive_records
            if record.bucket == AgingBucket.CURRENT
        ),
        start=Decimal("0"),
    ).quantize(_CENTS, rounding=ROUND_HALF_UP)

    bucket_1_30 = sum(
        (
            record.amount
            for record in positive_records
            if record.bucket == AgingBucket.DAYS_1_30
        ),
        start=Decimal("0"),
    ).quantize(_CENTS, rounding=ROUND_HALF_UP)

    bucket_31_60 = sum(
        (
            record.amount
            for record in positive_records
            if record.bucket == AgingBucket.DAYS_31_60
        ),
        start=Decimal("0"),
    ).quantize(_CENTS, rounding=ROUND_HALF_UP)

    bucket_61_90 = sum(
        (
            record.amount
            for record in positive_records
            if record.bucket == AgingBucket.DAYS_61_90
        ),
        start=Decimal("0"),
    ).quantize(_CENTS, rounding=ROUND_HALF_UP)

    bucket_90_plus = sum(
        (
            record.amount
            for record in positive_records
            if record.bucket == AgingBucket.DAYS_90_PLUS
        ),
        start=Decimal("0"),
    ).quantize(_CENTS, rounding=ROUND_HALF_UP)

    return WeeklyDSOSnapshot(
        as_of_date=as_of_date,
        currency=currency,
        total_ar=total_ar.quantize(_CENTS, rounding=ROUND_HALF_UP),
        lookback_sales=lookback_sales.quantize(_CENTS, rounding=ROUND_HALF_UP),
        dso_equal_weight=dso_equal_weight,
        dso_dollar_weight=dso_dollar_weight,
        weighted_avg_dpd=weighted_avg_dpd,
        max_dpd=max_dpd,
        bucket_current=bucket_current,
        bucket_1_30=bucket_1_30,
        bucket_31_60=bucket_31_60,
        bucket_61_90=bucket_61_90,
        bucket_90_plus=bucket_90_plus,
    )


def compute_weekly_dso(
    invoices: tuple[Invoice, ...],
    start_date: date,
    end_date: date,
    currency: str = "CAD",
    fx: FXProvider | None = None,
    config: TimeSeriesConfig = TimeSeriesConfig(),
) -> WeeklyTimeSeries:
    """Generates a weekly series of DSO snapshots across the date range."""
    if start_date > end_date:

        return ()

    step = timedelta(days=config.step_days)
    snapshots: list[WeeklyDSOSnapshot] = []
    current_date = start_date

    while current_date <= end_date:
        snapshot = compute_weekly_snapshot(
            invoices=invoices,
            as_of_date=current_date,
            currency=currency,
            fx=fx,
            config=config,
        )
        snapshots.append(snapshot)
        current_date += step

    return tuple(snapshots)


@dataclass(slots=True)
class ClientVolume:
    client_name: str
    volume: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class TopNCustomersResult:
    top_n_balances: dict[str, Decimal]
    other_balance: Decimal

    @property
    def top_n_names(self) -> set[str]:
        return set(self.top_n_balances.keys())


def get_top_n_customers(
    invoices: tuple[Invoice, ...],
    start_date: date,
    end_date: date,
    top_n: int,
    currency: str = "CAD",
    fx: FXProvider | None = None,
) -> TopNCustomersResult:
    """Calculates the Top N customers by total invoiced volume during the period."""
    client_volumes: dict[str, ClientVolume] = {}

    for inv in invoices:
        if start_date <= inv.invoice_date <= end_date:
            amount = _convert_invoice(inv, currency, fx, end_date)
            if inv.client_name not in client_volumes:
                client_volumes[inv.client_name] = ClientVolume(
                    client_name=inv.client_name
                )
            client_volumes[inv.client_name].volume += amount

    sorted_clients = sorted(
        client_volumes.values(), key=lambda cv: cv.volume, reverse=True
    )
    top_clients_list = sorted_clients[:top_n]
    other_clients_list = sorted_clients[top_n:]

    top_n_balances = {cv.client_name: cv.volume for cv in top_clients_list}
    other_balance = sum((cv.volume for cv in other_clients_list), Decimal("0"))

    return TopNCustomersResult(
        top_n_balances=top_n_balances, other_balance=other_balance
    )


type ARRecord = dict[str, date | str | float | bool]


def _week_balances(
    invoices: Sequence[Invoice], as_of: date, currency: str, fx: FXProvider | None
) -> dict[str, ClientVolume]:
    """Sums each client's active AR at as_of, converted to currency."""
    week_balances: dict[str, ClientVolume] = {}
    for inv in invoices:
        if not _is_active_ar(inv, as_of):
            continue
        amount = _convert_invoice(inv, currency, fx, as_of)
        week_balances.setdefault(
            inv.client_name, ClientVolume(client_name=inv.client_name)
        ).volume += amount
    return week_balances


def _week_records(
    as_of: date, week_balances: dict[str, ClientVolume], top_n_clients: set[str]
) -> list[ARRecord]:
    """Flattens one week's balances into rows, or one null row when AR is empty."""
    if not week_balances:
        return [
            {
                "as_of_date": as_of,
                "client_name": "NONE",
                "balance": 0.0,
                "is_top_n": False,
            }
        ]
    return [
        {
            "as_of_date": as_of,
            "client_name": client_name,
            "balance": float(cv.volume),
            "is_top_n": client_name in top_n_clients,
        }
        for client_name, cv in week_balances.items()
    ]


def customer_ar_balances(
    invoices: tuple[Invoice, ...],
    start_date: date,
    end_date: date,
    currency: str = "CAD",
    fx: FXProvider | None = None,
    top_n: int = 4,
    config: TimeSeriesConfig = TimeSeriesConfig(),
) -> pd.DataFrame:
    """Computes weekly client AR balances, tagging clients within the Top N.

    The Top N rank by open balance summed across the weekly snapshots, the same
    measure that orders customers in the charts.
    """
    step = timedelta(days=config.step_days)
    weeks = [start_date + step * i for i in range((end_date - start_date) // step + 1)]
    balances = [_week_balances(invoices, week, currency, fx) for week in weeks]
    top_n_clients = _top_by_open_balance(balances, top_n)

    records = [
        record
        for week, week_balances in zip(weeks, balances)
        for record in _week_records(week, week_balances, top_n_clients)
    ]
    return pd.DataFrame(records)


def _top_by_open_balance(
    weekly_balances: Sequence[dict[str, ClientVolume]], top_n: int
) -> set[str]:
    """Names the top_n clients by open AR summed across all weekly snapshots."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for week in weekly_balances:
        for name, cv in week.items():
            totals[name] += cv.volume
    return set(sorted(totals, key=totals.__getitem__, reverse=True)[:top_n])
