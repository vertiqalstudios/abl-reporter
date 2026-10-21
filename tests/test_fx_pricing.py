from datetime import date
from decimal import Decimal

import pytest

from abl.certificate import calculate_currency_breakdown
from abl.engine import evaluate_snapshot
from abl.fx import convert_to_cad, valuation_date
from abl.fxdata import BOC_SOURCE, FXMarket, SeriesFXProvider, format_fx_note
from abl.models import Invoice, LendingCriteria
from abl.timeseries import compute_weekly_snapshot, customer_ar_balances

WEEK_1 = date(2026, 3, 6)
WEEK_2 = date(2026, 3, 13)
SETTLED = date(2026, 3, 10)

# A different USD/CAD rate on every date, so any wrong pricing date shows up.
RATES = SeriesFXProvider(
    {
        date(2026, 2, 27): Decimal("1.20"),
        WEEK_1: Decimal("1.30"),
        SETTLED: Decimal("1.35"),
        WEEK_2: Decimal("1.40"),
    }
)
NO_RESERVES = LendingCriteria(
    dilution_reserve_pct=Decimal("0"),
    fx_reserve_pct=Decimal("0"),
    max_client_concentration_pct=Decimal("1"),
)


def _usd(amount: str, paid: date | None = None, client: str = "Acme") -> Invoice:
    return Invoice(
        client, date(2026, 2, 27), date(2026, 4, 30), Decimal(amount), "USD", paid
    )


@pytest.mark.parametrize(
    ("paid", "expected"),
    [
        (None, WEEK_2),  # open: spot on the report date
        (date(2026, 3, 20), WEEK_2),  # settles after the report date: still open
        (SETTLED, SETTLED),  # settled by the report date: its settlement date
        (WEEK_2, WEEK_2),
    ],
)
def test_valuation_date(paid: date | None, expected: date) -> None:
    assert valuation_date(_usd("100", paid), WEEK_2) == expected


@pytest.mark.parametrize(
    ("paid", "expected"),
    [(None, Decimal("14000.00")), (SETTLED, Decimal("13500.00"))],
)
def test_convert_to_cad_prices_open_at_report_date_and_paid_at_settlement(
    paid: date | None, expected: Decimal
) -> None:
    cad = convert_to_cad(_usd("10000", paid), WEEK_2, RATES)
    assert cad.invoice_amount == expected


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [(WEEK_1, Decimal("13000.00")), (WEEK_2, Decimal("14000.00"))],
)
def test_engine_values_an_open_invoice_at_each_snapshots_own_spot(
    snapshot: date, expected: Decimal
) -> None:
    report = evaluate_snapshot([_usd("10000")], snapshot, NO_RESERVES, RATES)
    assert report.eligible_amount_cad == expected


def test_dso_lookback_sales_price_a_settled_invoice_at_settlement() -> None:
    invoices = (_usd("10000", paid=SETTLED), _usd("10000", client="Other"))
    snapshot = compute_weekly_snapshot(invoices, WEEK_2, fx=RATES)
    assert snapshot.lookback_sales == Decimal("27500.00")  # 13,500 + 14,000
    assert snapshot.total_ar == Decimal("14000.00")  # only the open one


def test_weekly_ar_balances_reprice_the_open_book_each_week() -> None:
    df = customer_ar_balances((_usd("10000"),), WEEK_1, WEEK_2, fx=RATES, top_n=1)
    assert list(df["balance"]) == [13000.0, 14000.0]


def test_certificate_rate_and_note_come_from_the_same_provider() -> None:
    breakdown = {
        b.currency: b
        for b in calculate_currency_breakdown((_usd("10000"),), WEEK_2, RATES)
    }
    note = format_fx_note(FXMarket(RATES), WEEK_2)
    assert breakdown["USD"].fx_rate == Decimal("1.40")
    assert f"USD/CAD spot {breakdown['USD'].fx_rate:.4f}" in note
    assert f"[{BOC_SOURCE}, rate date 2026-03-13]" in note
