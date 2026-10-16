from datetime import date
from decimal import Decimal
import pytest

from abl.models import IneligibilityReason, IneligibilitySummary, Invoice, InvoiceWithReason
from abl.rules import (
    calculate_concentration_cap,
    calculate_net_availability,
    check_aged_overdue,
    check_contra,
    check_cross_default,
    find_tainted_clients,
    is_invoice_open,
    summarize_ineligibles,
)


@pytest.mark.parametrize(
    ("invoice_date", "due_date", "paid_date", "as_of", "expected_open"),
    [
        # Future invoice
        (date(2025, 3, 5), date(2025, 4, 5), None, date(2025, 3, 1), False),
        # Paid before as_of
        (date(2025, 1, 1), date(2025, 2, 1), date(2025, 2, 28), date(2025, 3, 1), False),
        # Paid on as_of
        (date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1), date(2025, 3, 1), False),
        # Paid after as_of
        (date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 2), date(2025, 3, 1), True),
        # Unpaid
        (date(2025, 1, 1), date(2025, 2, 1), None, date(2025, 3, 1), True),
        # Invoiced exactly on as_of and unpaid
        (date(2025, 3, 1), date(2025, 4, 1), None, date(2025, 3, 1), True),
    ],
)
def test_is_invoice_open(
    invoice_date: date,
    due_date: date,
    paid_date: date | None,
    as_of: date,
    expected_open: bool,
) -> None:
    inv = Invoice(
        client_name="Test Client",
        invoice_date=invoice_date,
        due_date=due_date,
        invoice_amount=Decimal("100.00"),
        currency="CAD",
        paid_date=paid_date,
    )
    assert is_invoice_open(inv, as_of) is expected_open


@pytest.mark.parametrize(
    ("debtor_name", "contra_set", "expected_reason"),
    [
        ("Self", frozenset({"self", "affiliate"}), IneligibilityReason.CONTRA_ACCOUNT),
        (" self ", frozenset({"self", "affiliate"}), IneligibilityReason.CONTRA_ACCOUNT),
        ("AFFILIATE", frozenset({"self", "affiliate"}), IneligibilityReason.CONTRA_ACCOUNT),
        ("ThirdParty Inc", frozenset({"self", "affiliate"}), None),
        ("", frozenset({"self"}), None),
    ],
)
def test_check_contra(
    debtor_name: str,
    contra_set: frozenset[str],
    expected_reason: IneligibilityReason | None,
) -> None:
    assert check_contra(debtor_name, contra_set) is expected_reason


@pytest.mark.parametrize(
    ("due_date", "as_of", "max_days", "expected_reason"),
    [
        # Exactly 180 days -> not overdue beyond threshold
        (date(2025, 1, 2), date(2025, 7, 1), 180, None),
        # 181 days -> aged overdue
        (date(2025, 1, 1), date(2025, 7, 1), 180, IneligibilityReason.AGED_OVERDUE),
        # Current (due after as_of)
        (date(2025, 7, 10), date(2025, 7, 1), 180, None),
    ],
)
def test_check_aged_overdue(
    due_date: date,
    as_of: date,
    max_days: int,
    expected_reason: IneligibilityReason | None,
) -> None:
    assert check_aged_overdue(due_date, as_of, max_days) is expected_reason


def test_find_tainted_clients() -> None:
    as_of = date(2025, 5, 1)
    invoices = [
        Invoice("Client A", date(2025, 1, 1), date(2025, 1, 20), Decimal("60000.00"), "CAD"),
        Invoice("Client A", date(2025, 4, 1), date(2025, 4, 25), Decimal("40000.00"), "CAD"),
        Invoice("Client B", date(2025, 1, 1), date(2025, 1, 20), Decimal("40000.00"), "CAD"),
        Invoice("Client B", date(2025, 4, 1), date(2025, 4, 25), Decimal("60000.00"), "CAD"),
        Invoice("Self", date(2025, 1, 1), date(2025, 1, 20), Decimal("100000.00"), "CAD"),
    ]
    tainted = find_tainted_clients(
        invoices=invoices,
        as_of=as_of,
        taint_days=90,
        threshold_pct=Decimal("0.50"),
        contra_clients=frozenset({"self"}),
    )
    assert "Client A" in tainted
    assert "Client B" not in tainted
    assert "Self" not in tainted


@pytest.mark.parametrize(
    ("client_name", "tainted_set", "expected_reason"),
    [
        ("Client A", {"Client A", "Client C"}, IneligibilityReason.CROSS_DEFAULT_TAINT),
        ("Client B", {"Client A", "Client C"}, None),
    ],
)
def test_check_cross_default(
    client_name: str,
    tainted_set: set[str],
    expected_reason: IneligibilityReason | None,
) -> None:
    assert check_cross_default(client_name, tainted_set) is expected_reason


@pytest.mark.parametrize(
    ("gross_eligible", "max_pct", "expected_cap"),
    [
        (Decimal("100000.00"), Decimal("0.30"), Decimal("30000.00")),
        (Decimal("123456.78"), Decimal("0.25"), Decimal("30864.20")),
        (Decimal("0.00"), Decimal("0.30"), Decimal("0.00")),
    ],
)
def test_calculate_concentration_cap(
    gross_eligible: Decimal, max_pct: Decimal, expected_cap: Decimal
) -> None:
    assert calculate_concentration_cap(gross_eligible, max_pct) == expected_cap


@pytest.mark.parametrize(
    ("eligible_cad", "advance_rate", "dilution_reserve", "expected_borrow"),
    [
        (Decimal("200000.00"), Decimal("0.85"), Decimal("0.05"), Decimal("160000.00")),
        (Decimal("100000.00"), Decimal("0.80"), Decimal("0.00"), Decimal("80000.00")),
        (Decimal("100000.00"), Decimal("0.05"), Decimal("0.10"), Decimal("0.00")),
    ],
)
def test_calculate_net_availability(
    eligible_cad: Decimal,
    advance_rate: Decimal,
    dilution_reserve: Decimal,
    expected_borrow: Decimal,
) -> None:
    assert calculate_net_availability(eligible_cad, advance_rate, dilution_reserve) == expected_borrow


def test_summarize_ineligibles() -> None:
    records = [
        InvoiceWithReason(
            client_name="Self",
            invoice_date=date(2025, 1, 1),
            due_date=date(2025, 1, 31),
            invoice_amount=Decimal("15000.00"),
            currency="CAD",
            ineligible_balance=Decimal("15000.00"),
            reason=IneligibilityReason.CONTRA_ACCOUNT,
        ),
        InvoiceWithReason(
            client_name="Old",
            invoice_date=date(2024, 1, 1),
            due_date=date(2024, 2, 1),
            invoice_amount=Decimal("25000.00"),
            currency="CAD",
            ineligible_balance=Decimal("25000.00"),
            reason=IneligibilityReason.AGED_OVERDUE,
        ),
        InvoiceWithReason(
            client_name="Tainted",
            invoice_date=date(2025, 1, 1),
            due_date=date(2025, 1, 31),
            invoice_amount=Decimal("40000.00"),
            currency="CAD",
            ineligible_balance=Decimal("40000.00"),
            reason=IneligibilityReason.CROSS_DEFAULT_TAINT,
        ),
        InvoiceWithReason(
            client_name="BigDebtor",
            invoice_date=date(2025, 1, 1),
            due_date=date(2025, 1, 31),
            invoice_amount=Decimal("50000.00"),
            currency="CAD",
            ineligible_balance=Decimal("20000.00"),
            reason=IneligibilityReason.CONCENTRATION_EXCESS,
        ),
    ]

    summary = summarize_ineligibles(records)
    assert summary.contra == Decimal("15000.00")
    assert summary.aged_overdue == Decimal("25000.00")
    assert summary.cross_default == Decimal("40000.00")
    assert summary.concentration_excess == Decimal("20000.00")
    assert summary.total == Decimal("100000.00")
