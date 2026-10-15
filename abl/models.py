import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class IneligibilityReason(StrEnum):
    CONTRA_ACCOUNT = "CONTRA_ACCOUNT"
    AGED_OVERDUE = "AGED_OVERDUE"
    CROSS_DEFAULT_TAINT = "CROSS_DEFAULT_TAINT"
    CONCENTRATION_EXCESS = "CONCENTRATION_EXCESS"


@dataclass(frozen=True, slots=True)
class Invoice:
    client_name: str
    invoice_date: date
    due_date: date
    invoice_amount: Decimal
    currency: str
    paid_date: date | None = None
    invoice_id: str = ""


@dataclass(frozen=True, slots=True)
class InvoiceWithReason(Invoice):
    eligible_balance: Decimal = Decimal("0.00")
    ineligible_balance: Decimal = Decimal("0.00")
    reason: IneligibilityReason = IneligibilityReason.AGED_OVERDUE
    reason_detail: str = ""


@dataclass(frozen=True, slots=True)
class LendingCriteria:
    max_client_concentration_pct: Decimal = Decimal("0.30")
    max_overdue_days: int = 180
    taint_past_due_days: int = 90
    taint_threshold_pct: Decimal = Decimal("0.50")
    cross_default_enabled: bool = True
    advance_rate: Decimal = Decimal("0.85")
    dilution_reserve_pct: Decimal = Decimal("0.05")
    contra_clients: frozenset[str] = frozenset({"self"})

    @property
    def effective_advance_rate(self) -> Decimal:
        return max(Decimal("0.00"), self.advance_rate - self.dilution_reserve_pct)


@dataclass(slots=True)
class CollateralReport:
    week: datetime
    eligible_amount_cad: Decimal
    ineligible_amount_cad: Decimal
    net_available_borrow_cad: Decimal
    eligible_invoices: list[Invoice] = field(default_factory=list)
    ineligible_invoices: list[InvoiceWithReason] = field(default_factory=list)
