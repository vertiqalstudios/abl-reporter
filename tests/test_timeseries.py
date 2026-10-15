from dataclasses import fields, is_dataclass
from datetime import date
from decimal import Decimal
import unittest

from abl.models import Invoice
from abl.timeseries import (
    AgingBucket,
    TimeSeriesConfig,
    WeeklyDSOSnapshot,
    compute_weekly_dso,
    compute_weekly_snapshot,
)


def _create_invoice(
    amount: Decimal,
    currency: str = "USD",
    issue_date: date = date(2026, 1, 1),
    due_date: date = date(2026, 1, 31),
    paid_date: date | None = None,
) -> Invoice:
    if is_dataclass(Invoice):
        fnames = {f.name for f in fields(Invoice)}
        data: dict[str, object] = {
            "amount": amount,
            "currency": currency,
            "issue_date": issue_date,
            "due_date": due_date,
            "paid_date": paid_date,
        }
        if "invoice_id" in fnames:
            data["invoice_id"] = "INV-TEST"
        if "id" in fnames:
            data["id"] = "INV-TEST"
        if "customer_id" in fnames:
            data["customer_id"] = "CUST-TEST"
        filtered = {k: v for k, v in data.items() if k in fnames}
        return Invoice(**filtered)  # type: ignore[call-arg]
    return Invoice(  # type: ignore[call-arg]
        amount=amount,
        currency=currency,
        issue_date=issue_date,
        due_date=due_date,
        paid_date=paid_date,
    )


class TestTimeSeriesModule(unittest.TestCase):
    def test_empty_invoices(self) -> None:
        snapshot = compute_weekly_snapshot(
            invoices=(),
            as_of_date=date(2026, 3, 1),
            currency="USD",
        )
        self.assertEqual(snapshot.total_ar, Decimal("0.00"))
        self.assertEqual(snapshot.lookback_sales, Decimal("0.00"))
        self.assertIsNone(snapshot.dso_equal_weight)
        self.assertIsNone(snapshot.dso_dollar_weight)
        self.assertEqual(snapshot.weighted_avg_dpd, Decimal("0.00"))
        self.assertEqual(snapshot.max_dpd, 0)
        self.assertEqual(snapshot.bucket_current, Decimal("0.00"))

    def test_single_current_invoice(self) -> None:
        inv = _create_invoice(
            amount=Decimal("100.00"),
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
        )
        snapshot = compute_weekly_snapshot(
            invoices=(inv,),
            as_of_date=date(2026, 1, 15),
            currency="USD",
        )
        self.assertEqual(snapshot.total_ar, Decimal("100.00"))
        self.assertEqual(snapshot.lookback_sales, Decimal("100.00"))
        self.assertEqual(snapshot.dso_equal_weight, Decimal("14.00"))
        self.assertEqual(snapshot.dso_dollar_weight, Decimal("14.00"))
        self.assertEqual(snapshot.weighted_avg_dpd, Decimal("0.00"))
        self.assertEqual(snapshot.max_dpd, 0)
        self.assertEqual(snapshot.bucket_current, Decimal("100.00"))
        self.assertEqual(snapshot.bucket_1_30, Decimal("0.00"))

    def test_weighted_calculations(self) -> None:
        inv1 = _create_invoice(
            amount=Decimal("100.00"),
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 15),
        )
        inv2 = _create_invoice(
            amount=Decimal("300.00"),
            issue_date=date(2026, 1, 11),
            due_date=date(2026, 1, 25),
        )
        snapshot = compute_weekly_snapshot(
            invoices=(inv1, inv2),
            as_of_date=date(2026, 2, 1),
            currency="USD",
        )
        self.assertEqual(snapshot.total_ar, Decimal("400.00"))
        self.assertEqual(snapshot.dso_equal_weight, Decimal("26.00"))
        self.assertEqual(snapshot.dso_dollar_weight, Decimal("23.50"))
        self.assertEqual(snapshot.weighted_avg_dpd, Decimal("9.50"))
        self.assertEqual(snapshot.max_dpd, 17)
        self.assertEqual(snapshot.bucket_1_30, Decimal("400.00"))
        self.assertEqual(snapshot.bucket_current, Decimal("0.00"))

    def test_paid_invoice_exclusion(self) -> None:
        inv_paid = _create_invoice(
            amount=Decimal("200.00"),
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 15),
            paid_date=date(2026, 1, 20),
        )
        snapshot = compute_weekly_snapshot(
            invoices=(inv_paid,),
            as_of_date=date(2026, 1, 25),
            currency="USD",
        )
        self.assertEqual(snapshot.total_ar, Decimal("0.00"))
        self.assertEqual(snapshot.lookback_sales, Decimal("200.00"))
        self.assertIsNone(snapshot.dso_equal_weight)

    def test_compute_weekly_dso_series(self) -> None:
        inv = _create_invoice(
            amount=Decimal("150.00"),
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
        )
        series = compute_weekly_dso(
            invoices=(inv,),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 15),
            currency="USD",
            config=TimeSeriesConfig(step_days=7),
        )
        self.assertEqual(len(series), 3)
        self.assertEqual(series[0].as_of_date, date(2026, 1, 1))
        self.assertEqual(series[1].as_of_date, date(2026, 1, 8))
        self.assertEqual(series[2].as_of_date, date(2026, 1, 15))

    def test_compute_weekly_dso_empty_range(self) -> None:
        series = compute_weekly_dso(
            invoices=(),
            start_date=date(2026, 1, 15),
            end_date=date(2026, 1, 1),
            currency="USD",
        )
        self.assertEqual(series, ())


if __name__ == "__main__":
    unittest.main()
