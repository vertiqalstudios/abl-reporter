"""CSV importer for ABL collateral invoices."""

from collections.abc import Iterable
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import io
from pathlib import Path
from typing import TextIO

from .models import Invoice

REQUIRED_HEADERS: tuple[str, ...] = (
    "Client Name",
    "Invoice Date",
    "Due Date",
    "Invoice Amount",
    "Currency",
    "Invoice Paid Date",
)


def _parse_amount(raw: str, line_num: int) -> Decimal:
    """Parses sanitized monetary strings into Decimal, stripping formatting."""
    cleaned = raw.strip().replace("$", "").replace(",", "")
    if not cleaned:
        raise ValueError(f"Line {line_num}: 'Invoice Amount' cannot be empty")
    try:
        return Decimal(cleaned)
    except InvalidOperation as err:
        raise ValueError(f"Line {line_num}: invalid monetary amount '{raw}'") from err


def _parse_date(raw: str, field_name: str, line_num: int) -> date:
    """Parses an ISO format date string (YYYY-MM-DD), rejecting empty or invalid values."""
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError(f"Line {line_num}: '{field_name}' cannot be empty")
    try:
        return date.fromisoformat(cleaned)
    except ValueError as err:
        raise ValueError(
            f"Line {line_num}: invalid ISO date '{raw}' for '{field_name}'"
        ) from err


def _parse_optional_date(raw: str, field_name: str, line_num: int) -> date | None:
    """Parses an optional ISO format date string, returning None when blank."""
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError as err:
        raise ValueError(
            f"Line {line_num}: invalid ISO date '{raw}' for '{field_name}'"
        ) from err



def parse_invoice_row(row: dict[str, str], line_num: int) -> Invoice:
    """Parses and validates a single CSV row into an Invoice object."""
    client_name = row["Client Name"].strip()
    if not client_name:
        raise ValueError(f"Line {line_num}: 'Client Name' cannot be empty")

    currency = row["Currency"].strip().upper()
    if not currency:
        raise ValueError(f"Line {line_num}: 'Currency' cannot be empty")

    invoice_date = _parse_date(row["Invoice Date"], "Invoice Date", line_num)
    due_date = _parse_date(row["Due Date"], "Due Date", line_num)
    invoice_amount = _parse_amount(row["Invoice Amount"], line_num)
    paid_date = _parse_optional_date(
        row["Invoice Paid Date"], "Invoice Paid Date", line_num
    )

    return Invoice(
        client_name=client_name,
        invoice_date=invoice_date,
        due_date=due_date,
        invoice_amount=invoice_amount,
        currency=currency,
        paid_date=paid_date,
        invoice_id="",
    )


def parse_invoices(rows: Iterable[dict[str, str]]) -> list[Invoice]:
    """Pure pipeline mapping an iterable of CSV row dicts to Invoice objects."""
    invoices: list[Invoice] = []
    for line_num, row in enumerate(rows, start=2):
        if not any(v.strip() for v in row.values()):
            continue
        invoices.append(parse_invoice_row(row, line_num))
    return invoices


def _read_from_stream(stream: TextIO) -> list[Invoice]:
    """Reads a CSV text stream, validates required headers, and yields invoices."""
    reader = csv.DictReader(stream)
    if reader.fieldnames is None:
        return []

    missing = [header for header in REQUIRED_HEADERS if header not in reader.fieldnames]
    if missing:
        raise KeyError(f"Missing required CSV column(s): {missing}")

    return parse_invoices(reader)



def load_invoices_from_csv(
    source: Path | str | TextIO = Path("invoices.csv"),
) -> list[Invoice]:
    """Loads and validates invoices from a CSV file path, buffer, or string."""
    if hasattr(source, "read"):
        return _read_from_stream(source)

    if isinstance(source, str) and ("\n" in source or "\r" in source):
        return _read_from_stream(io.StringIO(source))

    path = Path(source)
    if path.is_file():
        with path.open(mode="r", encoding="utf-8-sig") as file_obj:
            return _read_from_stream(file_obj)

    if isinstance(source, str) and "," in source:
        return _read_from_stream(io.StringIO(source))

    raise FileNotFoundError(f"CSV file not found: {source}")

