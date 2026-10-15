from datetime import date
from decimal import Decimal
import unittest

from abl.models import IneligibilityReason, Invoice
from abl.rules import (
    check_aged_overdue,
    check_contra,
    find_tainted_clients,
    is_invoice_open,
    calculate_net_availability,
)


class TestRules(unittest.TestCase):
    def test_is_invoice_open_lifecycle(self):
        as_of = date(2025, 3, 1)

        inv_future = Invoice("A", date(2025, 3, 5), date(2025, 4, 5), Decimal("100"), "CAD")
        self.assertFalse(is_invoice_open(inv_future, as_of))

        inv_paid = Invoice("A", date(2025, 1, 1), date(2025, 2, 1), Decimal("100"), "CAD", invoice_paid_date=date(2025, 2, 28))
        self.assertFalse(is_invoice_open(inv_paid, as_of))

        inv_paid_later = Invoice("A", date(2025, 1, 1), date(2025, 2, 1), Decimal("100"), "CAD", invoice_paid_date=date(2025, 3, 2))
        self.assertTrue(is_invoice_open(inv_paid_later, as_of))

        inv_unpaid = Invoice("A", date(2025, 1, 1), date(2025, 2, 1), Decimal("100"), "CAD")
        self.assertTrue(is_invoice_open(inv_unpaid, as_of))

    def test_check_contra_accounts(self):
        contra_set = frozenset({"self", "intercompany"})
        self.assertEqual(check_contra("Self", contra_set), IneligibilityReason.CONTRA_ACCOUNT)
        self.assertEqual(check_contra(" self ", contra_set), IneligibilityReason.CONTRA_ACCOUNT)
        self.assertIsNone(check_contra("ThirdParty Inc", contra_set))

    def test_check_aged_overdue_threshold(self):
        as_of = date(2025, 7, 1)
        self.assertIsNone(check_aged_overdue(date(2025, 1, 2), as_of, 180))
        self.assertEqual(check_aged_overdue(date(2025, 1, 1), as_of, 180), IneligibilityReason.AGED_OVERDUE)

    def test_find_tainted_clients_50pct_threshold(self):
        as_of = date(2025, 5, 1)
        invoices = [
            Invoice("Client A", date(2025, 1, 1), date(2025, 1, 20), Decimal("60000"), "CAD"),
            Invoice("Client A", date(2025, 4, 1), date(2025, 4, 25), Decimal("40000"), "CAD"),
            Invoice("Client B", date(2025, 1, 1), date(2025, 1, 20), Decimal("40000"), "CAD"),
            Invoice("Client B", date(2025, 4, 1), date(2025, 4, 25), Decimal("60000"), "CAD"),
        ]
        tainted = find_tainted_clients(invoices, as_of, 90, Decimal("0.50"), frozenset({"self"}))
        self.assertIn("Client A", tainted)
        self.assertNotIn("Client B", tainted)

    def test_calculate_net_availability(self):
        net = calculate_net_availability(Decimal("200000.00"), Decimal("0.85"), Decimal("0.05"))
        self.assertEqual(net, Decimal("160000.00"))
