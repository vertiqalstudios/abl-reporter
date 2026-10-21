from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import TextIO


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
    fx_reserve_pct: Decimal = Decimal("0.025")
    contra_clients: frozenset[str] = frozenset({"self"})

    @property
    def effective_advance_rate(self) -> Decimal:
        reserves = self.dilution_reserve_pct + self.fx_reserve_pct
        return max(Decimal("0.00"), self.advance_rate - reserves)


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
    eligible_share_pct: Decimal = Decimal("0.0000")


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
    secondary: str = "#334155"
    muted: str = "#64748B"
    subtle: str = "#94A3B8"
    accent: str = "#2563EB"
    alert: str = "#DC2626"
    border: str = "#E2E8F0"
    bg_subtle: str = "#F8FAFC"
    font_family: str = "Helvetica Neue"
    debtor_colors: tuple[str, ...] = (
        "#1E293B",
        "#334155",
        "#475569",
        "#64748B",
        "#94A3B8",
        "#CBD5E1",
    )
    grid: str = "#CBD5E1"


@dataclass(frozen=True, slots=True)
class CustomerPalette:
    """Chart colours for the largest customers in rank order, plus "Other".

    Deliberately separate from `ReportPalette`: its debtor shades are one ramp,
    these are distinct hues kept clear of its accent and alert. The order passes
    colour-vision checks both as a plain stack and as the stream's centre-out
    stack, so reorder only after re-validating both. The last two hues (ranks 6
    and 7) are not yet colour-vision validated.
    """

    named: tuple[str, ...] = (
        "#eb6834",
        "#1baf7a",
        "#9085e9",
        "#eda100",
        "#e87ba4",
        "#35b7d4",
        "#8c9a2b",
    )
    other: str = "#DAD7D0"


@dataclass(frozen=True, slots=True)
class CertificateParams:
    status: ReportStatus = ReportStatus.PRO_FORMA
    existing_borrow: Decimal = Decimal("0.00")
    facility_limit: Decimal = Decimal("2000000.00")
    currency: str = "CAD"
    disclaimer: str = (
        "Highly confidential, Material Non-Public Information. "
        "For Internal Use and Lender Use only"
    )
    generated_at: datetime = field(default_factory=datetime.now)


type FXCallable = Callable[[str, str, date], Decimal]
type FXSeriesFetcher = Callable[[date, date], Mapping[date, Decimal]]

SNAPSHOT_WEEKDAY = 4  # Friday: weekly snapshots and the default as-of land here


def end_of_week(day: date) -> date:
    """Returns the Friday of the Monday-to-Sunday week containing `day`."""
    return day + timedelta(days=SNAPSHOT_WEEKDAY - day.weekday())


@dataclass(frozen=True, slots=True)
class ReportSettings:
    criteria: LendingCriteria = field(default_factory=LendingCriteria)
    palette: ReportPalette = field(default_factory=ReportPalette)
    customer_palette: CustomerPalette = field(default_factory=CustomerPalette)
    certificate: CertificateParams = field(default_factory=CertificateParams)
    # ~18 months of weekly snapshots; older invoices still feed DSO and taint
    chart_weeks: int = 78
    top_n_customers: int = 10
    fx: FXCallable | None = None
    # Live USD/CAD source for the volatility line only (see abl.fxdata); collateral
    # is converted by `fx` (default: the pinned rate), never by this feed
    fx_fetcher: FXSeriesFetcher | None = None
    input_source: Path | str | TextIO = Path("invoices.csv")
    output_dir: Path = Path("output")
    as_of: date | None = None

    def resolve_as_of(self, today: date | None = None) -> date:
        """Returns the as-of date shared by the certificate and every chart."""
        if self.as_of is not None:
            return self.as_of
        return end_of_week(today if today is not None else date.today())
