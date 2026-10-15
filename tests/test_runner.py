from datetime import date
from decimal import Decimal
import unittest
import pandas as pd

from abl.runner import run_collateral_report
from abl.models import Invoice


class TestRunner(unittest.TestCase):
    def test_runner_dataframe_generation(self):
        invoices = [
            Invoice(f"Client {c}", date(2025, 1, 1), date(2025, 1, 31), Decimal("2500"), "CAD")
            for c in ("A", "B", "C", "D")
        ]
        reports, df = run_collateral_report(
            invoices=invoices,
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 28),
        )

        self.assertEqual(len(reports), 4)
        self.assertEqual(len(df), 4)
        self.assertEqual(list(df.columns), [
            "week",
            "eligible_collateral",
            "ineligible_collateral",
            "net_available_borrow",
        ])
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["week"]))
        self.assertEqual(df["eligible_collateral"].iloc[0], 10000.0)
        self.assertEqual(df["ineligible_collateral"].iloc[0], 0.0)
        self.assertEqual(df["net_available_borrow"].iloc[0], 8000.0)
