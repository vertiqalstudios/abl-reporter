"""Single entry point: `python -m abl` writes the certificate and all charts."""

from decimal import Decimal
from pathlib import Path

from .models import (
    CertificateParams,
    CustomerPalette,
    LendingCriteria,
    ReportPalette,
    ReportSettings,
    ReportStatus,
)
from .pack import generate_report_pack


def main(settings: ReportSettings | None = None) -> None:
    """Runs the report pack and prints where each output was written."""
    try:
        outputs = generate_report_pack(settings)
    except FileNotFoundError as err:
        raise SystemExit(str(err)) from err
    for name, path in outputs.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    # Default report settings - these are easiest to tweak
    default_settings = ReportSettings(
        criteria=LendingCriteria(
            max_client_concentration_pct=Decimal("0.30"),
            max_overdue_days=180,
            taint_past_due_days=90,
            taint_threshold_pct=Decimal("0.50"),
            cross_default_enabled=True,
            advance_rate=Decimal("0.85"),  # % of Collateral Borrow-able against
            dilution_reserve_pct=Decimal(
                "0.05"
            ),  # Advance Rate - Dilution Rate = Eff Advance Rate
            contra_clients=frozenset(
                {"self"}
            ),  # Names of "Self" debtors - eg Vertiqal, Gamelancer
        ),
        palette=ReportPalette(
            primary="#0F172A",
            secondary="#334155",
            muted="#64748B",
            subtle="#94A3B8",
            accent="#2563EB",
            alert="#DC2626",
            border="#E2E8F0",
            bg_subtle="#F8FAFC",
            font_family="Helvetica Neue",
            debtor_colors=(
                "#1E293B",
                "#334155",
                "#475569",
                "#64748B",
                "#94A3B8",
                "#CBD5E1",
            ),
            grid="#CBD5E1",
        ),
        customer_palette=CustomerPalette(
            named=(
                "#eb6834",
                "#1baf7a",
                "#9085e9",
                "#eda100",
                "#e87ba4",
                "#35b7d4",
                "#8c9a2b",
            ),
            other="#DAD7D0",
        ),
        certificate=CertificateParams(
            status=ReportStatus.PRO_FORMA,
            existing_borrow=Decimal("0.00"),  # outstanding loan balance
            facility_limit=Decimal("2000000.00"),
            currency="CAD",
            disclaimer=(
                "Highly confidential, Material Non-Public Information. "
                "For Internal Use and Lender Use only"
            ),
        ),
        fx=None,
        input_source=Path("invoices_real_2.csv"),
        output_dir=Path("output"),
        as_of=None,
        # Weeks of history charted (52 = 1 year); raise or lower to vary the span.
        # Earlier invoices still feed opening AR, DSO and taint.
        chart_weeks=52,
        top_n_customers=10,  # Customers ranked and tagged in the Top N charts
    )
    main(default_settings)
