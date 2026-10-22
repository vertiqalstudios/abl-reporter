import io
from datetime import date
from decimal import Decimal

import pytest

from abl.importer import load_invoices_from_csv, parse_invoice_row
from abl.models import Invoice


def test_parse_valid_row() -> None:
    row = {
        "Client Name": "Acme Corp",
        "Invoice Date": "2025-01-15",
        "Due Date": "2025-02-15",
        "Invoice Amount": "15000.50",
        "Currency": "cad",
        "Invoice Paid Date": "",
    }
    inv = parse_invoice_row(row, 2)
    assert inv == Invoice(
        client_name="Acme Corp",
        invoice_date=date(2025, 1, 15),
        due_date=date(2025, 2, 15),
        invoice_amount=Decimal("15000.50"),
        currency="CAD",
        paid_date=None,
        invoice_id="",
    )


def test_parse_paid_invoice_row() -> None:
    row = {
        "Client Name": "Beta LLC",
        "Invoice Date": "2025-01-01",
        "Due Date": "2025-01-31",
        "Invoice Amount": "$2,500.00",
        "Currency": "USD",
        "Invoice Paid Date": "2025-01-20",
    }
    inv = parse_invoice_row(row, 3)
    assert inv.invoice_amount == Decimal("2500.00")
    assert inv.currency == "USD"
    assert inv.paid_date == date(2025, 1, 20)


@pytest.mark.parametrize(
    ("row", "line_num", "error_match"),
    [
        (
            {
                "Client Name": "   ",
                "Invoice Date": "2025-01-15",
                "Due Date": "2025-02-15",
                "Invoice Amount": "100.00",
                "Currency": "CAD",
                "Invoice Paid Date": "",
            },
            2,
            "Line 2: 'Client Name' cannot be empty",
        ),
        (
            {
                "Client Name": "Acme",
                "Invoice Date": "2025-01-15",
                "Due Date": "2025-02-15",
                "Invoice Amount": "N/A",
                "Currency": "CAD",
                "Invoice Paid Date": "",
            },
            5,
            "Line 5: invalid monetary amount 'N/A'",
        ),
        (
            {
                "Client Name": "Acme",
                "Invoice Date": "not-a-date",
                "Due Date": "2025-02-15",
                "Invoice Amount": "100",
                "Currency": "CAD",
                "Invoice Paid Date": "",
            },
            4,
            "Line 4: invalid ISO date 'not-a-date' for 'Invoice Date'",
        ),
    ],
)
def test_parse_invoice_row_validation_errors(
    row: dict[str, str], line_num: int, error_match: str
) -> None:
    with pytest.raises(ValueError, match=error_match):
        parse_invoice_row(row, line_num)


def test_load_invoices_from_csv_stream() -> None:
    csv_content = (
        "Client Name,Invoice Date,Due Date,Invoice Amount,Currency,Invoice Paid Date\n"
        """Alpha Inc,2025-01-10,2025-02-10,5000.00,USD,
Beta Inc,2025-01-12,2025-02-12,12000.00,CAD,2025-02-01
"""
    )
    invoices = load_invoices_from_csv(io.StringIO(csv_content))
    assert len(invoices) == 2
    assert invoices[0].client_name == "Alpha Inc"
    assert invoices[1].client_name == "Beta Inc"


def test_missing_header_raises_key_error() -> None:
    csv_content = """Client Name,Invoice Date,Due Date
Alpha Inc,2025-01-10,2025-02-10
"""
    with pytest.raises(KeyError, match="Missing required CSV column"):
        load_invoices_from_csv(io.StringIO(csv_content))


@pytest.mark.parametrize(
    ("extra", "expected"),
    [({}, ""), ({"Invoice Key": " 100499 "}, "100499"), ({"Invoice Key": ""}, "")],
)
def test_invoice_key_is_optional(extra: dict[str, str], expected: str) -> None:
    row = {
        "Client Name": "Acme Corp",
        "Invoice Date": "2025-01-15",
        "Due Date": "2025-02-15",
        "Invoice Amount": "100",
        "Currency": "CAD",
        "Invoice Paid Date": "",
        **extra,
    }
    assert parse_invoice_row(row, 2).invoice_id == expected
