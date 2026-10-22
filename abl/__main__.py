"""Single entry point: `python -m abl` writes the certificate and all charts."""

import logging
from decimal import Decimal
from pathlib import Path

from .fx import FXConversionError
from .fxdata import fetch_usd_cad_series
from .models import (
    CertificateParams,
    CustomerPalette,
    LendingCriteria,
    ReportPalette,
    ReportSettings,
    ReportStatus,
)
from .pack import generate_report_pack

LOG_NAME = "abl.log"


def configure_logging(log_path: Path) -> None:
    """Logs warnings to stderr and full detail to `log_path` for troubleshooting."""
    root = logging.getLogger("abl")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    detail = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    detail.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(detail)
    root.addHandler(console)


def main(settings: ReportSettings | None = None) -> None:
    """Runs the report pack and prints where each output was written."""
    cfg = settings if settings is not None else build_default_settings()
    configure_logging(cfg.output_dir / LOG_NAME)
    try:
        outputs = generate_report_pack(cfg)
    except FileNotFoundError as err:
        raise SystemExit(str(err)) from err
    except FXConversionError as err:
        raise SystemExit(f"FXConversionError: {err}") from err
    for name, path in outputs.items():
        print(f"{name}: {path}")


def build_default_settings() -> ReportSettings:
    """Default report settings - these are easiest to tweak."""
    return ReportSettings(
        criteria=LendingCriteria(
            max_client_concentration_pct=Decimal("0.30"),
            max_overdue_days=90,
            taint_past_due_days=90,
            taint_threshold_pct=Decimal("0.50"),
            cross_default_enabled=True,
            advance_rate=Decimal("0.85"),  # % of Collateral Borrow-able against
            dilution_reserve_pct=Decimal("0.025"),  # Dilution cushion
            fx_reserve_pct=Decimal("0.025"),  # CADUSD volatility cushion, flat
            # Advance - Dilution - FX = Eff Advance Rate
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
        fx_fetcher=fetch_usd_cad_series,  # live USD/CAD prices collateral; None=pinned
        input_source=Path("invoices_real_2.csv"),
        output_dir=Path("output"),
        as_of=None,
        # Weeks of history charted (52 = 1 year); raise or lower to vary the span.
        # Earlier invoices still feed opening AR, DSO and taint.
        chart_weeks=52,
        top_n_customers=10,  # Customers ranked and tagged in the Top N charts
    )


if __name__ == "__main__":
    main()
