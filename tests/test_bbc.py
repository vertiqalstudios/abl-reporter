from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import unittest

from abl.bbc import generate_bbc
from abl.fx import make_fixed_fx_provider
from abl.importer import load_invoices_from_csv
from abl.models import (
    CollateralReport,
    IneligibilityReason,
    IneligibilitySummary,
    Invoice,
    LendingCriteria,
)
from abl.rules import summarize_ineligibles


class TestBBCReport(unittest.TestCase):
    def setUp(self):
        self.fx = make_fixed_fx_provider(usd_cad_rate=Decimal("1.40"))
        self.criteria = LendingCriteria(
            advance_rate=Decimal("0.85"),
            dilution_reserve_pct=Decimal("0.05"),
            max_client_concentration_pct=Decimal("0.30"),
            max_overdue_days=180,
            taint_past_due_days=90,
            taint_threshold_pct=Decimal("0.50"),
            contra_clients=frozenset({"self"}),
        )

    def test_empty_invoices(self):
        report = generate_bbc([], date(2026, 1, 1), self.criteria, self.fx)
        self.assertEqual(report.eligible_amount_cad, Decimal("0.00"))
        self.assertEqual(report.ineligible_amount_cad, Decimal("0.00"))
        self.assertEqual(report.gross_amount_cad, Decimal("0.00"))
        self.assertEqual(report.net_available_borrow_cad, Decimal("0.00"))
        self.assertEqual(report.ineligible_summary.total, Decimal("0.00"))
        self.assertEqual(len(report.ineligible_invoices), 0)

    def test_ineligible_summary_breakdown(self):
        as_of = date(2026, 2, 1)
        invoices = [
            # Contra: Self
            Invoice("Self", date(2026, 1, 1), date(2026, 1, 15), Decimal("10000.00"), "CAD"),
            # Aged overdue (>180 days past due 2025-05-01 vs 2026-02-01 = 276 days)
            Invoice("ClientOld", date(2025, 4, 1), date(2025, 5, 1), Decimal("20000.00"), "CAD"),
            # Cross-default tainted debtor: ClientTaint has 60k overdue >90 days, 40k current -> 60% > 50% threshold
            Invoice("ClientTaint", date(2025, 9, 1), date(2025, 10, 1), Decimal("60000.00"), "CAD"),
            Invoice("ClientTaint", date(2026, 1, 5), date(2026, 2, 10), Decimal("40000.00"), "CAD"),
            # Clean eligible debtors
            Invoice("CleanA", date(2026, 1, 10), date(2026, 2, 10), Decimal("70000.00"), "CAD"),
            Invoice("CleanB", date(2026, 1, 15), date(2026, 2, 15), Decimal("30000.00"), "CAD"),
        ]

        report = generate_bbc(invoices, as_of, self.criteria, self.fx)

        self.assertEqual(report.as_of_date, as_of)
        self.assertEqual(report.ineligible_summary.contra, Decimal("10000.00"))
        self.assertEqual(report.ineligible_summary.aged_overdue, Decimal("20000.00"))
        self.assertEqual(report.ineligible_summary.cross_default, Decimal("100000.00"))
        # Gross eligible candidates = 70k + 30k = 100k. Cap = 30k (30%).
        # CleanA has 70k -> cap 30k -> 40k excess.
        self.assertEqual(report.ineligible_summary.concentration_excess, Decimal("40000.00"))

        expected_ineligible = Decimal("10000.00") + Decimal("20000.00") + Decimal("100000.00") + Decimal("40000.00")
        self.assertEqual(report.ineligible_summary.total, expected_ineligible)
        self.assertEqual(report.ineligible_amount_cad, expected_ineligible)
        self.assertEqual(report.eligible_amount_cad, Decimal("60000.00"))  # 30k (CleanA capped) + 30k (CleanB)
        self.assertEqual(report.gross_amount_cad, Decimal("230000.00"))
        # Net borrow = 60,000 * 0.80 = 48,000
        self.assertEqual(report.net_available_borrow_cad, Decimal("48000.00"))

    def test_sample_csv_evaluation(self):
        csv_path = Path(__file__).resolve().parent.parent / "data" / "sample_invoices.csv"
        self.assertTrue(csv_path.exists())
        invoices = load_invoices_from_csv(csv_path)
        self.assertGreater(len(invoices), 50)

        as_of = date(2026, 11, 20)
        report = generate_bbc(invoices, as_of, self.criteria, self.fx)

        self.assertIsInstance(report, CollateralReport)
        self.assertEqual(report.as_of_date, as_of)
        self.assertEqual(report.gross_amount_cad, report.eligible_amount_cad + report.ineligible_amount_cad)
        self.assertEqual(report.ineligible_summary.total, report.ineligible_amount_cad)
        self.assertGreater(report.net_available_borrow_cad, Decimal("0.00"))
        self.assertLessEqual(report.net_available_borrow_cad, report.eligible_amount_cad)


if __name__ == "__main__":
    unittest.main()
