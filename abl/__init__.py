"""ABL Collateral Monitoring Library.

Runtime Requirement: Python 3.14+ ONLY.
This package strictly targets Python 3.14 or higher and utilizes native PEP 695
type statements (`type X = ...`), `enum.StrEnum`, structural pattern matching,
and modern generic syntax. Pre-3.14 compatibility shims or version-fallback
workarounds must not be used or reintroduced.
"""

from .bbc import generate_bbc
from .certificate import (
    calculate_currency_breakdown,
    calculate_debtor_concentrations,
    generate_certificate,
    generate_certificate_pdf,
)
from .engine import evaluate_snapshot
from .fx import FXProvider, convert_to_cad, make_fixed_fx_provider
from .importer import load_invoices_from_csv, parse_invoice_row, parse_invoices
from .models import (
    CertificateParams,
    CollateralReport,
    CurrencyBreakdown,
    CustomerPalette,
    DebtorConcentration,
    IneligibilityReason,
    IneligibilitySummary,
    Invoice,
    InvoiceWithReason,
    LendingCriteria,
    ReportPalette,
    ReportSettings,
    ReportStatus,
    end_of_week,
)
from .pack import generate_report_pack
from .report import (
    generate_all_reports,
    generate_collateral_chart,
    generate_customer_ar_chart,
    generate_customer_stream_chart,
    generate_dso_chart,
    generate_top_n_chart,
)
from .rules import summarize_ineligibles
from .runner import run_collateral_report

__all__ = [
    "CertificateParams",
    "CollateralReport",
    "CurrencyBreakdown",
    "CustomerPalette",
    "DebtorConcentration",
    "IneligibilityReason",
    "IneligibilitySummary",
    "Invoice",
    "InvoiceWithReason",
    "LendingCriteria",
    "ReportPalette",
    "ReportSettings",
    "ReportStatus",
    "end_of_week",
    "FXProvider",
    "convert_to_cad",
    "make_fixed_fx_provider",
    "evaluate_snapshot",
    "summarize_ineligibles",
    "run_collateral_report",
    "load_invoices_from_csv",
    "parse_invoice_row",
    "parse_invoices",
    "generate_bbc",
    "calculate_currency_breakdown",
    "calculate_debtor_concentrations",
    "generate_certificate",
    "generate_certificate_pdf",
    "generate_all_reports",
    "generate_report_pack",
    "generate_collateral_chart",
    "generate_customer_ar_chart",
    "generate_customer_stream_chart",
    "generate_dso_chart",
    "generate_top_n_chart",
]
