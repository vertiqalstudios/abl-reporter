from datetime import date, timedelta
from decimal import Decimal

import pytest

from abl.engine import _status_of, evaluate_snapshot
from abl.fx import make_fixed_fx_provider
from abl.models import (
    CollateralReport,
    IneligibilityReason,
    Invoice,
    InvoiceLine,
    LendingCriteria,
    LineStatus,
)

AS_OF = date(2026, 3, 13)
FX = make_fixed_fx_provider(Decimal("1.40"))
CRITERIA = LendingCriteria(
    dilution_reserve_pct=Decimal("0"), fx_reserve_pct=Decimal("0")
)


def _inv(
    client: str,
    amount: str,
    due: date = date(2026, 4, 30),
    issued: date = date(2026, 3, 1),
    currency: str = "CAD",
    invoice_id: str = "",
) -> Invoice:
    return Invoice(client, issued, due, Decimal(amount), currency, None, invoice_id)


def _scenario() -> list[Invoice]:
    return [
        _inv("self", "1000"),  # contra
        _inv("Old", "500", due=date(2025, 11, 1)),  # aged
        _inv("Slow", "600", due=date(2025, 12, 1)),  # aged, and taints the debtor
        _inv("Slow", "300"),  # tainted by the 67% overdue balance
        _inv("big", "10000", currency="USD", invoice_id="K1"),  # over the cap
        _inv("Small A", "2000"),
        _inv("Small B", "1000"),
    ]


def _report() -> CollateralReport:
    return evaluate_snapshot(_scenario(), AS_OF, CRITERIA, FX)


def _by_client(report: CollateralReport) -> dict[str, list[InvoiceLine]]:
    grouped: dict[str, list[InvoiceLine]] = {}
    for line in report.lines:
        grouped.setdefault(line.client_name, []).append(line)
    return grouped


def test_lines_reconcile_to_the_report_totals() -> None:
    report = _report()
    gross = sum(line.gross_cad for line in report.lines)
    eligible = sum(line.eligible_cad for line in report.lines)
    ineligible = sum(line.ineligible_cad for line in report.lines)
    assert gross == report.gross_amount_cad
    assert eligible == report.eligible_amount_cad
    assert ineligible == report.ineligible_amount_cad


def test_every_open_invoice_has_exactly_one_line() -> None:
    assert len(_report().lines) == len(_scenario())


@pytest.mark.parametrize(
    ("client", "status", "reason"),
    [
        ("self", LineStatus.INELIGIBLE, IneligibilityReason.CONTRA_ACCOUNT),
        ("Old", LineStatus.INELIGIBLE, IneligibilityReason.AGED_OVERDUE),
        ("big", LineStatus.HAIRCUT, IneligibilityReason.CONCENTRATION_EXCESS),
        ("Small A", LineStatus.ELIGIBLE, None),
    ],
)
def test_line_status_and_reason(
    client: str, status: LineStatus, reason: IneligibilityReason | None
) -> None:
    (line,) = _by_client(_report())[client]
    assert (line.status, line.reason) == (status, reason)


def test_tainted_debtor_shows_aged_and_taint_lines() -> None:
    aged, tainted = sorted(
        _by_client(_report())["Slow"], key=lambda line: line.due_date
    )
    assert aged.reason is IneligibilityReason.AGED_OVERDUE
    assert tainted.reason is IneligibilityReason.CROSS_DEFAULT_TAINT
    assert "67%" in tainted.detail
    assert "limit 50%" in tainted.detail


def test_haircut_line_shows_how_much_was_kept() -> None:
    (line,) = _by_client(_report())["big"]
    assert line.gross_cad == Decimal("14000.00")
    assert line.eligible_cad + line.ineligible_cad == line.gross_cad
    assert Decimal("0") < line.haircut_pct < Decimal("1")
    assert "cap" in line.detail
    assert "of each invoice kept" in line.detail


def test_line_keeps_the_booked_amount_currency_and_rate() -> None:
    (line,) = _by_client(_report())["big"]
    assert (line.currency, line.face_amount) == ("USD", Decimal("10000"))
    assert line.fx_rate == Decimal("1.40")
    assert line.invoice_id == "K1"


def test_lines_sort_by_debtor_case_blind_then_issue_date() -> None:
    invoices = [
        _inv("zenith", "100", issued=date(2026, 3, 5)),
        _inv("Alpha", "100", issued=date(2026, 3, 9)),
        _inv("alpha", "100", issued=date(2026, 3, 2)),
        _inv("9Letters", "100"),
    ]
    report = evaluate_snapshot(invoices, AS_OF, CRITERIA, FX)
    assert [(ln.client_name, ln.invoice_date.day) for ln in report.lines] == [
        ("9Letters", 1),
        ("alpha", 2),
        ("Alpha", 9),
        ("zenith", 5),
    ]


@pytest.mark.parametrize(
    ("issued", "expected"),
    [
        (AS_OF - timedelta(days=7), False),  # last week's snapshot day
        (AS_OF - timedelta(days=6), True),
        (AS_OF, True),
    ],
)
def test_is_new_marks_invoices_issued_this_week(issued: date, expected: bool) -> None:
    report = evaluate_snapshot([_inv("A", "100", issued=issued)], AS_OF, CRITERIA, FX)
    assert report.lines[0].is_new is expected


@pytest.mark.parametrize(
    ("eligible", "ineligible", "expected"),
    [
        ("100.00", "0.00", LineStatus.ELIGIBLE),
        ("0.00", "100.00", LineStatus.INELIGIBLE),
        ("99.99", "0.01", LineStatus.HAIRCUT),
    ],
)
def test_status_comes_from_the_amounts(
    eligible: str, ineligible: str, expected: LineStatus
) -> None:
    assert _status_of(Decimal(eligible), Decimal(ineligible)) is expected


def test_no_open_invoices_means_no_lines() -> None:
    report = evaluate_snapshot([], AS_OF, CRITERIA, FX)
    assert report.lines == ()


@pytest.mark.parametrize(
    "as_of", [date(2026, 2, 20), date(2026, 3, 1), date(2026, 3, 13), date(2026, 6, 1)]
)
def test_invoice_paid_before_issue_is_never_collateral(as_of: date) -> None:
    inv = Invoice(
        "Acme", date(2026, 3, 1), date(2026, 4, 30), Decimal("1000"), "CAD",
        paid_date=date(2026, 2, 25),
    )  # fmt: skip
    report = evaluate_snapshot([inv], as_of, CRITERIA, FX)
    assert report.eligible_amount_cad == Decimal("0.00")
    assert report.lines == ()
