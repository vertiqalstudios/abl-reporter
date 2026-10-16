from datetime import date
from decimal import Decimal
import pytest

from abl.engine import evaluate_snapshot
from abl.fx import make_fixed_fx_provider
from abl.models import IneligibilityReason, Invoice, LendingCriteria


def test_full_pipeline_reconciliation() -> None:
    fx = make_fixed_fx_provider(Decimal("1.44"))
    criteria = LendingCriteria()
    snapshot_date = date(2025, 2, 7)

    invoices = [
        Invoice("Alpha Corp", date(2025, 1, 1), date(2025, 1, 31), Decimal("50000"), "USD"),
        Invoice("Beta Ltd", date(2024, 5, 1), date(2024, 6, 1), Decimal("20000"), "CAD"),
        Invoice("Gamma Inc", date(2024, 8, 1), date(2024, 9, 1), Decimal("60000"), "CAD"),
        Invoice("Gamma Inc", date(2025, 1, 10), date(2025, 2, 10), Decimal("40000"), "CAD"),
        Invoice("Self", date(2025, 1, 1), date(2025, 1, 31), Decimal("15000"), "CAD"),
        Invoice("MegaCorp", date(2025, 1, 15), date(2025, 2, 15), Decimal("150000"), "CAD"),
    ]

    report = evaluate_snapshot(invoices, snapshot_date, criteria, fx)

    assert report.eligible_amount_cad == Decimal("133200.00")
    assert report.ineligible_amount_cad == Decimal("223800.00")
    assert report.net_available_borrow_cad == Decimal("106560.00")
    assert report.gross_amount_cad == Decimal("357000.00")
    assert report.ineligible_summary.total == Decimal("223800.00")

    excess_records = [
        r for r in report.ineligible_invoices
        if r.reason is IneligibilityReason.CONCENTRATION_EXCESS
    ]
    assert len(excess_records) == 2
