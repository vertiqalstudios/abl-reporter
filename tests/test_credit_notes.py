from datetime import date
from decimal import Decimal

from abl.engine import evaluate_snapshot
from abl.fx import make_fixed_fx_provider
from abl.models import (
    CollateralReport,
    Invoice,
    InvoiceLine,
    LendingCriteria,
    LineStatus,
)

AS_OF = date(2026, 3, 13)
FX = make_fixed_fx_provider(Decimal("1.40"))
CRITERIA = LendingCriteria(
    dilution_reserve_pct=Decimal("0"), fx_reserve_pct=Decimal("0")
)  # effective advance rate 85%


def _inv(client: str, amount: str, currency: str = "CAD") -> Invoice:
    return Invoice(
        client, date(2026, 3, 1), date(2026, 4, 30), Decimal(amount), currency
    )


def _book() -> list[Invoice]:
    """Four debtors at 1000 each: under the concentration cap, all eligible."""
    return [_inv(name, "1000") for name in ("A", "B", "C", "D")]


def _evaluate(extra: list[Invoice]) -> CollateralReport:
    return evaluate_snapshot([*_book(), *extra], AS_OF, CRITERIA, FX)


def _credit_lines(report: CollateralReport) -> list[InvoiceLine]:
    return [ln for ln in report.lines if ln.status is LineStatus.CREDIT]


def test_credit_note_reduces_eligible_collateral_and_borrowing() -> None:
    report = _evaluate([_inv("A", "-500")])
    assert report.eligible_amount_cad == Decimal("3500.00")
    assert report.net_available_borrow_cad == Decimal("2975.00")


def test_credit_note_is_deducted_at_full_face_in_cad() -> None:
    report = _evaluate([_inv("A", "-100", currency="USD")])
    assert report.eligible_amount_cad == Decimal("3860.00")


def test_credit_note_gets_an_audit_line_that_says_so() -> None:
    (line,) = _credit_lines(_evaluate([_inv("A", "-500")]))
    assert line.eligible_cad == Decimal("-500.00")
    assert line.ineligible_cad == Decimal("0.00")
    assert "Credit note" in line.detail


def test_credit_note_does_not_change_other_invoices_treatment() -> None:
    plain = {ln.client_name: ln for ln in _evaluate([]).lines}
    with_credit = _evaluate([_inv("A", "-500")])
    kept = [ln for ln in with_credit.lines if ln.status is not LineStatus.CREDIT]
    assert [(ln.client_name, ln.eligible_cad) for ln in kept] == [
        (name, plain[name].eligible_cad) for name in ("A", "B", "C", "D")
    ]


def test_credit_note_from_a_contra_debtor_still_reduces_capacity() -> None:
    report = _evaluate([_inv("self", "-200")])
    assert report.eligible_amount_cad == Decimal("3800.00")


def test_credits_larger_than_collateral_floor_at_zero() -> None:
    report = _evaluate([_inv("A", "-9000")])
    assert report.eligible_amount_cad == Decimal("0.00")
    assert report.net_available_borrow_cad == Decimal("0.00")


def test_a_lone_credit_note_never_makes_capacity_negative() -> None:
    report = evaluate_snapshot([_inv("A", "-500")], AS_OF, CRITERIA, FX)
    assert report.eligible_amount_cad == Decimal("0.00")
    assert report.net_available_borrow_cad == Decimal("0.00")
    assert len(report.lines) == 1


def test_settled_credit_note_is_not_deducted() -> None:
    settled = Invoice(
        "A", date(2026, 3, 1), date(2026, 4, 30), Decimal("-500"), "CAD",
        paid_date=date(2026, 3, 5),
    )  # fmt: skip
    assert _evaluate([settled]).eligible_amount_cad == Decimal("4000.00")
