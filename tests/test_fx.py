from datetime import date
from decimal import Decimal
import unittest

from abl.fx import make_fixed_fx_provider, convert_to_cad
from abl.models import Invoice


class TestFX(unittest.TestCase):
    def test_fixed_fx_conversion_usd_to_cad(self):
        fx = make_fixed_fx_provider(Decimal("1.44"))
        inv = Invoice("Alpha Corp", date(2025, 1, 1), date(2025, 1, 31), Decimal("1000.00"), "USD")
        cad_inv = convert_to_cad(inv, date(2025, 1, 15), fx)
        self.assertEqual(cad_inv.currency, "CAD")
        self.assertEqual(cad_inv.invoice_amount, Decimal("1440.00"))

    def test_fixed_fx_identity_cad(self):
        fx = make_fixed_fx_provider(Decimal("1.44"))
        inv = Invoice("Beta Ltd", date(2025, 1, 1), date(2025, 1, 31), Decimal("500.00"), "CAD")
        cad_inv = convert_to_cad(inv, date(2025, 1, 15), fx)
        self.assertEqual(cad_inv.invoice_amount, Decimal("500.00"))

    def test_unsupported_currency_raises(self):
        fx = make_fixed_fx_provider()
        inv = Invoice("Gamma Inc", date(2025, 1, 1), date(2025, 1, 31), Decimal("100.00"), "EUR")
        with self.assertRaises(ValueError):
            convert_to_cad(inv, date(2025, 1, 15), fx)
