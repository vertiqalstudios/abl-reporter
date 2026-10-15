from datetime import date
from decimal import Decimal
import io
import unittest

from abl.importer import load_invoices_from_csv, parse_invoice_row, parse_invoices
from abl.models import Invoice


class TestImporter(unittest.TestCase):
    def test_parse_valid_row(self):
        row = {
            "Client Name": "Acme Corp",
            "Invoice Date": "2025-01-15",
            "Due Date": "2025-02-15",
            "Invoice Amount": "15000.50",
            "Currency": "cad",
            "Invoice Paid Date": "",
        }
        inv = parse_invoice_row(row, 2)
        self.assertEqual(
            inv,
            Invoice(
                client_name="Acme Corp",
                invoice_date=date(2025, 1, 15),
                due_date=date(2025, 2, 15),
                invoice_amount=Decimal("15000.50"),
                currency="CAD",
                invoice_paid_date=None,
                invoice_id="",
            ),
        )

    def test_parse_paid_invoice_row(self):
        row = {
            "Client Name": "Beta LLC",
            "Invoice Date": "2025-01-01",
            "Due Date": "2025-01-31",
            "Invoice Amount": "$2,500.00",
            "Currency": "USD",
            "Invoice Paid Date": "2025-01-20",
        }
        inv = parse_invoice_row(row, 3)
        self.assertEqual(inv.invoice_amount, Decimal("2500.00"))
        self.assertEqual(inv.currency, "USD")
        self.assertEqual(inv.invoice_paid_date, date(2025, 1, 20))

    def test_missing_client_name_raises(self):
        row = {
            "Client Name": "   ",
            "Invoice Date": "2025-01-15",
            "Due Date": "2025-02-15",
            "Invoice Amount": "100.00",
            "Currency": "CAD",
            "Invoice Paid Date": "",
        }
        with self.assertRaises(ValueError) as ctx:
            parse_invoice_row(row, 2)
        self.assertIn("Line 2: 'Client Name' cannot be empty", str(ctx.exception))

    def test_invalid_amount_raises(self):
        row = {
            "Client Name": "Acme",
            "Invoice Date": "2025-01-15",
            "Due Date": "2025-02-15",
            "Invoice Amount": "N/A",
            "Currency": "CAD",
            "Invoice Paid Date": "",
        }
        with self.assertRaises(ValueError) as ctx:
            parse_invoice_row(row, 5)
        self.assertIn("Line 5: invalid monetary amount 'N/A'", str(ctx.exception))

    def test_invalid_date_raises(self):
        row = {
            "Client Name": "Acme",
            "Invoice Date": "not-a-date",
            "Due Date": "2025-02-15",
            "Invoice Amount": "100",
            "Currency": "CAD",
            "Invoice Paid Date": "",
        }
        with self.assertRaises(ValueError) as ctx:
            parse_invoice_row(row, 4)
        self.assertIn("Line 4: invalid ISO date 'not-a-date' for 'Invoice Date'", str(ctx.exception))

    def test_load_invoices_from_csv_stream(self):
        csv_content = """Client Name,Invoice Date,Due Date,Invoice Amount,Currency,Invoice Paid Date
Alpha Inc,2025-01-10,2025-02-10,5000.00,USD,
Beta Inc,2025-01-12,2025-02-12,12000.00,CAD,2025-02-01
"""
        invoices = load_invoices_from_csv(io.StringIO(csv_content))
        self.assertEqual(len(invoices), 2)
        self.assertEqual(invoices[0].client_name, "Alpha Inc")
        self.assertEqual(invoices[1].client_name, "Beta Inc")

    def test_missing_header_raises_key_error(self):
        csv_content = """Client Name,Invoice Date,Due Date
Alpha Inc,2025-01-10,2025-02-10
"""
        with self.assertRaises(KeyError) as ctx:
            load_invoices_from_csv(io.StringIO(csv_content))
        self.assertIn("Missing required CSV column(s)", str(ctx.exception))
