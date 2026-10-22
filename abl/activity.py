"""Receivables settled in the snapshot week: activity beside the open collateral."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .engine import SNAPSHOT_WINDOW_DAYS
from .fx import FXProvider, convert_to_cad
from .models import Invoice


@dataclass(frozen=True, slots=True)
class SettledLine:
    """An invoice paid in the week, valued at its settlement-date rate."""

    client_name: str
    invoice_id: str
    invoice_date: date
    paid_date: date
    currency: str
    face_amount: Decimal
    fx_rate: Decimal
    cad_amount: Decimal


def is_settled_in_week(inv: Invoice, as_of: date) -> bool:
    """True if paid in the window (as_of minus a week, as_of]."""
    if inv.paid_date is None:
        return False
    window_start = as_of - timedelta(days=SNAPSHOT_WINDOW_DAYS)
    return window_start < inv.paid_date <= as_of


def _settled_line(inv: Invoice, as_of: date, fx: FXProvider) -> SettledLine:
    """Builds the activity row, priced at the settlement date by `convert_to_cad`."""
    assert inv.paid_date is not None  # guaranteed by is_settled_in_week
    return SettledLine(
        client_name=inv.client_name,
        invoice_id=inv.invoice_id,
        invoice_date=inv.invoice_date,
        paid_date=inv.paid_date,
        currency=inv.currency,
        face_amount=inv.invoice_amount,
        fx_rate=fx(inv.currency, "CAD", inv.paid_date),
        cad_amount=convert_to_cad(inv, as_of, fx).invoice_amount,
    )


def settled_in_week(
    invoices: Sequence[Invoice], as_of: date, fx: FXProvider
) -> tuple[SettledLine, ...]:
    """Lists invoices paid in the week, by debtor (case-blind) then settlement date."""
    lines = [
        _settled_line(inv, as_of, fx)
        for inv in invoices
        if is_settled_in_week(inv, as_of)
    ]
    lines.sort(
        key=lambda ln: (
            ln.client_name.casefold(),
            ln.paid_date,
            ln.invoice_date,
            ln.invoice_id,
        )
    )
    return tuple(lines)
