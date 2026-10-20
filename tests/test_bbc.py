import io
from datetime import date
from decimal import Decimal

import pytest

from abl.bbc import generate_bbc
from abl.fx import make_fixed_fx_provider
from abl.importer import load_invoices_from_csv
from abl.models import CollateralReport, Invoice, LendingCriteria


@pytest.fixture
def default_criteria() -> LendingCriteria:
    return LendingCriteria(
        advance_rate=Decimal("0.85"),
        dilution_reserve_pct=Decimal("0.05"),
        max_client_concentration_pct=Decimal("0.30"),
        max_overdue_days=180,
        taint_past_due_days=90,
        taint_threshold_pct=Decimal("0.50"),
        contra_clients=frozenset({"self"}),
    )


@pytest.fixture
def fixed_fx():
    return make_fixed_fx_provider(usd_cad_rate=Decimal("1.40"))


def test_empty_invoices_produces_zeroed_report(default_criteria, fixed_fx) -> None:
    report = generate_bbc([], date(2026, 1, 1), default_criteria, fixed_fx)
    assert report.eligible_amount_cad == Decimal("0.00")
    assert report.ineligible_amount_cad == Decimal("0.00")
    assert report.gross_amount_cad == Decimal("0.00")
    assert report.net_available_borrow_cad == Decimal("0.00")
    assert report.ineligible_summary.total == Decimal("0.00")
    assert len(report.ineligible_invoices) == 0


def test_ineligible_summary_categorization(default_criteria, fixed_fx) -> None:
    as_of = date(2026, 2, 1)
    invoices = [
        # Contra
        Invoice(
            "Self", date(2026, 1, 1), date(2026, 1, 15), Decimal("10000.00"), "CAD"
        ),
        # Aged overdue (>180 days past due)
        Invoice(
            "ClientOld", date(2025, 4, 1), date(2025, 5, 1), Decimal("20000.00"), "CAD"
        ),
        # Cross-default tainted debtor (60k/100k overdue >90d = 60% >= 50%)
        Invoice(
            "ClientTaint",
            date(2025, 9, 1),
            date(2025, 10, 1),
            Decimal("60000.00"),
            "CAD",
        ),
        Invoice(
            "ClientTaint",
            date(2026, 1, 5),
            date(2026, 2, 10),
            Decimal("40000.00"),
            "CAD",
        ),
        # Clean candidates: 70k + 30k = 100k gross eligible, 30% cap = 30k
        Invoice(
            "CleanA", date(2026, 1, 10), date(2026, 2, 10), Decimal("70000.00"), "CAD"
        ),
        Invoice(
            "CleanB", date(2026, 1, 15), date(2026, 2, 15), Decimal("30000.00"), "CAD"
        ),
    ]

    report = generate_bbc(invoices, as_of, default_criteria, fixed_fx)

    assert report.as_of_date == as_of
    assert report.ineligible_summary.contra == Decimal("10000.00")
    assert report.ineligible_summary.aged_overdue == Decimal("20000.00")
    assert report.ineligible_summary.cross_default == Decimal("100000.00")
    assert report.ineligible_summary.concentration_excess == Decimal("40000.00")

    expected_ineligible = Decimal("170000.00")
    assert report.ineligible_summary.total == expected_ineligible
    assert report.ineligible_amount_cad == expected_ineligible
    assert report.eligible_amount_cad == Decimal("60000.00")
    assert report.gross_amount_cad == Decimal("230000.00")
    assert report.net_available_borrow_cad == Decimal("48000.00")


SAMPLE_CSV_CONTENT = (
    "Client Name,Invoice Date,Due Date,Invoice Amount,Currency,Invoice Paid Date\n"
    """Beta Retail,2025-01-01,2025-01-31,124000.00,USD,2025-01-19
Gamma Tech,2025-01-07,2025-01-14,66000.00,CAD,2025-01-13
Self,2025-01-28,2025-02-04,124000.00,CAD,2025-02-02
Phi Aerospace,2026-10-16,2026-11-15,155000.00,CAD,
Omega Capital,2026-10-23,2026-12-22,232000.00,CAD,
Rho Pharma,2026-10-30,2026-12-29,149000.00,CAD,
Iota Automotive,2026-11-06,2027-01-05,88000.00,CAD,
Omega Capital,2026-11-13,2027-01-12,220000.00,CAD,
Rho Pharma,2026-11-20,2026-12-20,141000.00,CAD,
"""
)


@pytest.mark.parametrize(
    ("as_of", "expected_min_gross"),
    [
        (date(2025, 2, 1), Decimal("100000.00")),
        (date(2026, 11, 20), Decimal("900000.00")),
    ],
)
def test_in_memory_csv_pipeline(
    as_of: date, expected_min_gross: Decimal, default_criteria, fixed_fx
) -> None:
    invoices = load_invoices_from_csv(io.StringIO(SAMPLE_CSV_CONTENT))

    report = generate_bbc(invoices, as_of, default_criteria, fixed_fx)

    assert isinstance(report, CollateralReport)
    assert report.as_of_date == as_of
    assert (
        report.gross_amount_cad
        == report.eligible_amount_cad + report.ineligible_amount_cad
    )
    assert report.ineligible_summary.total == report.ineligible_amount_cad
    assert report.gross_amount_cad >= expected_min_gross
