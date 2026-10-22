import io
import logging
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


HEADER = "Client Name,Invoice Date,Due Date,Invoice Amount,Currency,Invoice Paid Date"
KEYED_HEADER = HEADER + ",Invoice Key"


def _csv(*rows: str, header: str = HEADER) -> str:
    return "\n".join([header, *rows]) + "\n"


def test_paid_before_issue_is_floored_to_issue_date_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = _csv("Acme,2025-02-01,2025-03-01,100.00,CAD,2025-01-20")
    with caplog.at_level(logging.WARNING, logger="abl.importer"):
        (inv,) = load_invoices_from_csv(text)
    assert inv.paid_date == date(2025, 2, 1)
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "floored" in caplog.text and "line 2" in caplog.text


@pytest.mark.parametrize(
    ("paid", "expected"),
    [
        ("2025-02-01", date(2025, 2, 1)),
        ("2025-02-10", date(2025, 2, 10)),
        ("", None),
    ],
)
def test_paid_on_or_after_issue_is_untouched_and_silent(
    paid: str, expected: date | None, caplog: pytest.LogCaptureFixture
) -> None:
    text = _csv(f"Acme,2025-02-01,2025-03-01,100.00,CAD,{paid}")
    with caplog.at_level(logging.WARNING, logger="abl.importer"):
        (inv,) = load_invoices_from_csv(text)
    assert inv.paid_date == expected
    assert caplog.records == []


def test_due_before_issue_is_accepted_but_alerts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = _csv("Acme,2025-02-01,2025-01-15,100.00,CAD,,K9", header=KEYED_HEADER)
    with caplog.at_level(logging.WARNING, logger="abl.importer"):
        (inv,) = load_invoices_from_csv(text)
    assert inv.due_date == date(2025, 1, 15)
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert "invoice K9" in caplog.text


def test_duplicate_invoice_keys_warn_but_load(
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = _csv(
        "Acme,2025-02-01,2025-03-01,100.00,CAD,,K1",
        "Beta,2025-02-02,2025-03-02,200.00,CAD,,K1",
        "Gamma,2025-02-03,2025-03-03,300.00,CAD,,K2",
        header=KEYED_HEADER,
    )
    with caplog.at_level(logging.WARNING, logger="abl.importer"):
        invoices = load_invoices_from_csv(text)
    assert len(invoices) == 3
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "K1 appears 2 times" in caplog.text


def test_blank_invoice_keys_are_not_duplicates(
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = _csv(
        "Acme,2025-02-01,2025-03-01,100.00,CAD,",
        "Beta,2025-02-02,2025-03-02,200.00,CAD,",
    )
    with caplog.at_level(logging.WARNING, logger="abl.importer"):
        load_invoices_from_csv(text)
    assert caplog.records == []
