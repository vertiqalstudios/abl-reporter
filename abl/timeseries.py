from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum

from abl.fx import FXProvider, convert_to_cad
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
    return inv.amount


def _convert_currency(
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    fx: FXProvider | None,
) -> Decimal:
    if from_currency == to_currency:
        return amount
    if to_currency == "CAD":
        return convert_to_cad(amount, from_currency, fx)
    raise ValueError(f"Unsupported target currency: {to_currency}; expected CAD")


def _is_active_ar(inv: Invoice, as_of: date) -> bool:
    if inv.issue_date > as_of:
        return False
    if inv.paid_date is not None and inv.paid_date <= as_of:
        return False
    return True


def _is_lookback_sale(inv: Invoice, as_of: date, lookback_days: int) -> bool:
    start_window = as_of - timedelta(days=lookback_days)
    if inv.issue_date < start_window:
        return False
    if inv.issue_date > as_of:
        return False
    return True


def _calculate_dpd(inv: Invoice, as_of: date) -> int:
    delta_days = (as_of - inv.due_date).days
    if delta_days <= 0:
        return 0
    return delta_days


def _classify_aging_bucket(dpd: int) -> AgingBucket:
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
    raw_amount = _get_invoice_amount(inv)
    converted_amount = _convert_currency(raw_amount, inv.currency, currency, fx)
    age_days = (as_of - inv.issue_date).days
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
    lookback_invoices = [inv for inv in invoices if _is_lookback_sale(inv, as_of_date, config.lookback_days)]
    lookback_sales = sum(
        (_convert_currency(_get_invoice_amount(inv), inv.currency, currency, fx) for inv in lookback_invoices),
        start=Decimal("0"),
    )

    active_invoices = [inv for inv in invoices if _is_active_ar(inv, as_of_date)]
    all_active_amounts = [
        _convert_currency(_get_invoice_amount(inv), inv.currency, currency, fx)
        for inv in active_invoices
    ]
    total_ar = sum(all_active_amounts, start=Decimal("0"))

    positive_records = [
        _build_active_record(inv, as_of_date, currency, fx)
        for inv in active_invoices
        if _get_invoice_amount(inv) > Decimal("0")
    ]

    if not positive_records:
        return _empty_snapshot(as_of_date, currency, lookback_sales, total_ar)

    positive_balance_sum = sum((record.amount for record in positive_records), start=Decimal("0"))
    if positive_balance_sum <= Decimal("0"):
        return _empty_snapshot(as_of_date, currency, lookback_sales, total_ar)

    count = Decimal(len(positive_records))
    total_age = Decimal(sum(record.age_days for record in positive_records))
    dso_equal_weight = (total_age / count).quantize(_CENTS, rounding=ROUND_HALF_UP)

    weighted_age_sum = sum(
        (record.amount * Decimal(record.age_days) for record in positive_records),
        start=Decimal("0"),
    )
    dso_dollar_weight = (weighted_age_sum / positive_balance_sum).quantize(_CENTS, rounding=ROUND_HALF_UP)

    max_dpd = max(record.dpd for record in positive_records)
    weighted_dpd_sum = sum(
        (record.amount * Decimal(record.dpd) for record in positive_records),
        start=Decimal("0"),
    )
    weighted_avg_dpd = (weighted_dpd_sum / positive_balance_sum).quantize(_CENTS, rounding=ROUND_HALF_UP)

    bucket_current = sum(
        (record.amount for record in positive_records if record.bucket == AgingBucket.CURRENT),
        start=Decimal("0"),
    ).quantize(_CENTS, rounding=ROUND_HALF_UP)

    bucket_1_30 = sum(
        (record.amount for record in positive_records if record.bucket == AgingBucket.DAYS_1_30),
        start=Decimal("0"),
    ).quantize(_CENTS, rounding=ROUND_HALF_UP)

    bucket_31_60 = sum(
        (record.amount for record in positive_records if record.bucket == AgingBucket.DAYS_31_60),
        start=Decimal("0"),
    ).quantize(_CENTS, rounding=ROUND_HALF_UP)

    bucket_61_90 = sum(
        (record.amount for record in positive_records if record.bucket == AgingBucket.DAYS_61_90),
        start=Decimal("0"),
    ).quantize(_CENTS, rounding=ROUND_HALF_UP)

    bucket_90_plus = sum(
        (record.amount for record in positive_records if record.bucket == AgingBucket.DAYS_90_PLUS),
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
