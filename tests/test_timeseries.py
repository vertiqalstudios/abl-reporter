import inspect
import unittest
from datetime import date
from decimal import Decimal
from typing import Any

from abl.models import Invoice
from abl.timeseries import (
    TimeSeriesConfig,
    compute_weekly_dso,
    compute_weekly_snapshot,
    customer_ar_balances,
    get_top_n_customers,
)


class MockFX:
    def __init__(self, rates: dict[tuple[str, str], Decimal] | None = None) -> None:
        self.rates = rates or {}

    def get_rate(
        self, from_curr: str, to_curr: str, as_of: date | None = None
    ) -> Decimal:
        return self.rates.get((from_curr, to_curr), Decimal("1.0"))


def _create_invoice(
    invoice_number: str = "INV-001",
    client_name: str = "Client A",
    invoice_date: date = date(2026, 1, 1),
    due_date: date = date(2026, 1, 31),
    invoice_amount: Decimal = Decimal("100.00"),
    currency: str = "CAD",
    paid_date: date | None = None,
    **kwargs: Any,
) -> Invoice:
    amount = kwargs.pop("amount", invoice_amount)
    issue_date = kwargs.pop("issue_date", invoice_date)
    params = {
        "invoice_number": kwargs.pop("invoice_number", invoice_number),
        "client_name": client_name,
        "invoice_date": issue_date,
        "due_date": due_date,
        "invoice_amount": amount,
        "currency": currency,
        "paid_date": paid_date,
        **kwargs,
    }
    sig = inspect.signature(Invoice)
    filtered = {k: v for k, v in params.items() if k in sig.parameters}
    return Invoice(**filtered)  # type: ignore[call-arg]


class TestTimeSeriesModule(unittest.TestCase):
    def test_empty_invoices(self) -> None:
        snapshot = compute_weekly_snapshot(
            invoices=(),
            as_of_date=date(2026, 1, 15),
            currency="CAD",
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
            currency="CAD",
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
        )
        snapshot = compute_weekly_snapshot(
            invoices=(inv,),
            as_of_date=date(2026, 1, 15),
            currency="CAD",
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
            currency="CAD",
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 15),
        )
        inv2 = _create_invoice(
            amount=Decimal("300.00"),
            currency="CAD",
            issue_date=date(2026, 1, 11),
            due_date=date(2026, 1, 25),
        )
        snapshot = compute_weekly_snapshot(
            invoices=(inv1, inv2),
            as_of_date=date(2026, 1, 31),
            currency="CAD",
        )
        self.assertEqual(snapshot.total_ar, Decimal("400.00"))
        self.assertEqual(snapshot.dso_equal_weight, Decimal("25.00"))
        self.assertEqual(snapshot.dso_dollar_weight, Decimal("22.50"))
        self.assertEqual(snapshot.weighted_avg_dpd, Decimal("8.50"))
        self.assertEqual(snapshot.max_dpd, 16)
        self.assertEqual(snapshot.bucket_current, Decimal("0.00"))
        self.assertEqual(snapshot.bucket_1_30, Decimal("400.00"))

    def test_paid_invoice_exclusion(self) -> None:
        inv_open = _create_invoice(
            amount=Decimal("100.00"),
            currency="CAD",
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
            paid_date=None,
        )
        inv_paid = _create_invoice(
            amount=Decimal("200.00"),
            currency="CAD",
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
            paid_date=date(2026, 1, 20),
        )
        snapshot = compute_weekly_snapshot(
            invoices=(inv_open, inv_paid),
            as_of_date=date(2026, 1, 25),
            currency="CAD",
        )
        self.assertEqual(snapshot.total_ar, Decimal("100.00"))
        self.assertEqual(snapshot.lookback_sales, Decimal("300.00"))

    def test_compute_weekly_dso_series(self) -> None:
        inv = _create_invoice(
            amount=Decimal("150.00"),
            currency="CAD",
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
        )
        series = compute_weekly_dso(
            invoices=(inv,),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 15),
            currency="CAD",
            config=TimeSeriesConfig(step_days=7),
        )
        self.assertEqual(len(series), 3)
        self.assertEqual(series[0].as_of_date, date(2026, 1, 1))
        self.assertEqual(series[1].as_of_date, date(2026, 1, 8))
        self.assertEqual(series[2].as_of_date, date(2026, 1, 15))

    def test_start_date_after_end_date(self) -> None:
        series = compute_weekly_dso(
            invoices=(),
            start_date=date(2026, 2, 1),
            end_date=date(2026, 1, 1),
            currency="CAD",
        )
        self.assertEqual(series, ())

    def test_cad_identity_and_defaults(self) -> None:
        inv = _create_invoice(
            amount=Decimal("100.00"),
            currency="CAD",
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
        )
        snapshot = compute_weekly_snapshot(
            invoices=(inv,),
            as_of_date=date(2026, 1, 15),
        )
        self.assertEqual(snapshot.currency, "CAD")
        self.assertEqual(snapshot.total_ar, Decimal("100.00"))

    def test_missing_fx_provider_for_cad_conversion(self) -> None:
        inv = _create_invoice(
            amount=Decimal("100.00"),
            currency="USD",
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
        )
        with self.assertRaises(ValueError):
            compute_weekly_snapshot(
                invoices=(inv,),
                as_of_date=date(2026, 1, 15),
                currency="CAD",
                fx=None,
            )

    def test_unsupported_target_currency_conversion(self) -> None:
        inv = _create_invoice(
            amount=Decimal("100.00"),
            currency="USD",
            issue_date=date(2026, 1, 1),
            due_date=date(2026, 1, 31),
        )
        with self.assertRaises(ValueError):
            compute_weekly_snapshot(
                invoices=(inv,),
                as_of_date=date(2026, 1, 15),
                currency="EUR",
            )

    def test_get_top_n_customers(self) -> None:
        invoices = (
            _create_invoice(client_name="Client A", amount=Decimal("500.00")),
            _create_invoice(client_name="Client B", amount=Decimal("300.00")),
            _create_invoice(client_name="Client C", amount=Decimal("100.00")),
            _create_invoice(client_name="Client D", amount=Decimal("50.00")),
        )

        result = get_top_n_customers(
            invoices=invoices,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            top_n=2,
            currency="CAD",
        )

        # Verify Top 2 clients are selected correctly
        self.assertIn("Client A", result.top_n_names)
        self.assertIn("Client B", result.top_n_names)
        self.assertEqual(len(result.top_n_names), 2)

        # Verify exact balances are retained
        self.assertEqual(result.top_n_balances["Client A"], Decimal("500.00"))
        self.assertEqual(result.top_n_balances["Client B"], Decimal("300.00"))

        # Verify the sum of the remaining clients
        self.assertEqual(result.other_balance, Decimal("150.00"))

    def test_customer_ar_top_n_ranks_by_open_balance_not_volume(self) -> None:
        # Big invoices more but pays within a week; Slow stays open all month
        invoices = (
            _create_invoice(
                client_name="Big",
                invoice_date=date(2026, 1, 2),
                amount=Decimal("1000.00"),
                paid_date=date(2026, 1, 5),
            ),
            _create_invoice(
                client_name="Slow",
                invoice_date=date(2026, 1, 2),
                amount=Decimal("300.00"),
            ),
        )

        df = customer_ar_balances(
            invoices=invoices,
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 30),
            top_n=1,
        )

        top = set(df.loc[df["is_top_n"], "client_name"])
        self.assertEqual(top, {"Slow"})
        self.assertEqual(df["as_of_date"].nunique(), 5)


if __name__ == "__main__":
    unittest.main()
