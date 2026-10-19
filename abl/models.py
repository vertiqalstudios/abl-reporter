from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

try:
    from enum import StrEnum
except ImportError:
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class IneligibilityReason(StrEnum):
    CONTRA_ACCOUNT = "CONTRA_ACCOUNT"
    AGED_OVERDUE = "AGED_OVERDUE"
    CROSS_DEFAULT_TAINT = "CROSS_DEFAULT_TAINT"
    CONCENTRATION_EXCESS = "CONCENTRATION_EXCESS"


@dataclass(frozen=True, slots=True)
class IneligibilitySummary:
    contra: Decimal = Decimal("0.00")
    aged_overdue: Decimal = Decimal("0.00")
    cross_default: Decimal = Decimal("0.00")
    concentration_excess: Decimal = Decimal("0.00")

    @property
    def total(self) -> Decimal:
        return (
            self.contra
            + self.aged_overdue
            + self.cross_default
            + self.concentration_excess
        )


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


@dataclass(frozen=True, slots=True)
class CollateralReport:
    week: datetime
    eligible_amount_cad: Decimal
    ineligible_amount_cad: Decimal
    net_available_borrow_cad: Decimal
    eligible_invoices: list[Invoice] = field(default_factory=list)
    ineligible_invoices: list[InvoiceWithReason] = field(default_factory=list)
    ineligible_summary: IneligibilitySummary = field(
        default_factory=IneligibilitySummary
    )

    @property
    def gross_amount_cad(self) -> Decimal:
        return self.eligible_amount_cad + self.ineligible_amount_cad

    @property
    def as_of_date(self) -> date:
        return self.week.date()


class ReportStatus(StrEnum):
    PRO_FORMA = "PRO_FORMA"
    DRAFT = "DRAFT"
    CERTIFIED = "CERTIFIED"


@dataclass(frozen=True, slots=True)
class DebtorConcentration:
    client_name: str
    balance_cad: Decimal
    share_pct: Decimal
    is_capped: bool


@dataclass(frozen=True, slots=True)
class CurrencyBreakdown:
    currency: str
    face_amount: Decimal
    fx_rate: Decimal
    cad_amount: Decimal
    share_pct: Decimal


@dataclass(frozen=True, slots=True)
class ReportPalette:
    primary: str = "#0F172A"
    muted: str = "#64748B"
    subtle: str = "#94A3B8"
    accent: str = "#0F766E"
    alert: str = "#991B1B"
    border: str = "#E2E8F0"
    bg_subtle: str = "#F8FAFC"
    debtor_colors: tuple[str, ...] = (
        "#1E293B",
        "#334155",
        "#475569",
        "#64748B",
        "#94A3B8",
        "#CBD5E1",
    )


@dataclass(frozen=True, slots=True)
class CertificateParams:
    as_of: date
    status: ReportStatus = ReportStatus.PRO_FORMA
    existing_borrow: Decimal = Decimal("0.00")
    facility_limit: Decimal = Decimal("2000000.00")
    currency: str = "CAD"
    disclaimer: str = (
        "Highly confidential, Material Non-Public Information. "
        "For Internal Use and Lender Use only"
    )
    generated_at: datetime = field(default_factory=datetime.now)
