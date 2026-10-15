from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .models import IneligibilityReason, Invoice


def is_invoice_open(inv: Invoice, as_of: date) -> bool:
    """Checks whether an invoice is uncollected and outstanding at as_of date."""
    if inv.invoice_date > as_of:
        return False
    if inv.paid_date is not None and inv.paid_date <= as_of:
        return False
    return True


def check_contra(client_name: str, contra_clients: frozenset[str]) -> IneligibilityReason | None:
    """Identifies debtor accounts affiliated with self or intercompany accounts."""
    if client_name.strip().lower() in contra_clients:
        return IneligibilityReason.CONTRA_ACCOUNT
    return None


def check_aged_overdue(due_date: date, as_of: date, max_days: int) -> IneligibilityReason | None:
    """Flags stale invoices exceeding the absolute overdue threshold."""
    if (as_of - due_date).days > max_days:
        return IneligibilityReason.AGED_OVERDUE
    return None


def find_tainted_clients(
    invoices: Sequence[Invoice],
    as_of: date,
    taint_days: int,
    threshold_pct: Decimal,
    contra_clients: frozenset[str],
) -> set[str]:
    """Identifies debtors where delinquency ratio meets or exceeds threshold."""
    client_totals: dict[str, Decimal] = defaultdict(Decimal)
    client_overdue: dict[str, Decimal] = defaultdict(Decimal)

    for inv in invoices:
        if check_contra(inv.client_name, contra_clients) is not None:
            continue
        client_totals[inv.client_name] += inv.invoice_amount
        days_overdue = (as_of - inv.due_date).days
        if days_overdue > taint_days:
            client_overdue[inv.client_name] += inv.invoice_amount

    tainted: set[str] = set()
    for client, total in client_totals.items():
        if total <= Decimal("0.00"):
            continue
        if (client_overdue[client] / total) >= threshold_pct:
            tainted.add(client)
    return tainted


def check_cross_default(client_name: str, tainted_clients: set[str]) -> IneligibilityReason | None:
    """Checks if a client belongs to the tainted debtor registry."""
    if client_name in tainted_clients:
        return IneligibilityReason.CROSS_DEFAULT_TAINT
    return None


def calculate_concentration_cap(gross_eligible: Decimal, max_pct: Decimal) -> Decimal:
    """Calculates single-debtor dollar cap baseline."""
    return (gross_eligible * max_pct).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_net_availability(
    eligible_cad: Decimal,
    advance_rate: Decimal,
    dilution_reserve: Decimal,
) -> Decimal:
    """Applies advance rate net of dilution reserve to eligible collateral."""
    effective_rate = max(Decimal("0.00"), advance_rate - dilution_reserve)
    return (eligible_cad * effective_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
