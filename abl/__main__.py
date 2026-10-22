"""Single entry point: `python -m abl` writes the certificate and all charts."""

import logging
from decimal import Decimal
from pathlib import Path

from .fx import FXConversionError
from .fxdata import fetch_usd_cad_series
from .models import (
    CertificateParams,
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
    """Settings for `abl-report`: edit here to change what the command does."""
    # Only values that differ from the ReportSettings defaults are set below. To
    # change another, add it to the call. Every setting, its default and its effect:
    #
    # RUN
    #   input_source      invoices.csv  CSV to read: a Path, CSV text or open stream
    #   output_dir        output        folder for the PDF, charts and abl.log
    #   as_of             this Friday   date the certificate is struck and the last
    #                                   chart week; later invoices/payments ignored
    #   chart_weeks       52            weeks of chart history (52 = 1 year); older
    #                                   invoices still feed opening AR, DSO and taint
    #   top_n_customers   10            customers charted individually in the Top N
    #                                   charts; the rest group as Other
    #   fx_fetcher        None          fetch_usd_cad_series = live Bank of Canada
    #                                   rates; None = pinned 1 USD = 1.44 CAD
    #   fx                None          explicit FXProvider; wins over fx_fetcher
    #
    # criteria=LendingCriteria(...)
    #   advance_rate                  0.85   share of eligible collateral lent
    #   dilution_reserve_pct          0.025  cushion deducted from the advance rate
    #   fx_reserve_pct                0.025  USD/CAD cushion deducted likewise
    #                                        (advance - dilution - fx = effective rate)
    #   max_client_concentration_pct  0.30   one debtor's cap, as a share of eligible;
    #                                        the excess is ineligible
    #   max_overdue_days              90     invoices older past due are ineligible
    #   taint_past_due_days           90     age at which a balance counts toward taint
    #   taint_threshold_pct           0.50   past-due share that taints a debtor's
    #                                        other invoices
    #   cross_default_enabled         True   False switches the taint rule off
    #   contra_clients                {"self"}  affiliate debtors, always ineligible;
    #                                        case-insensitive, eg {"vertiqal"}
    #
    # certificate=CertificateParams(...)
    #   status            PRO_FORMA     PRO_FORMA, DRAFT or CERTIFIED: badge and
    #                                   footer wording; CERTIFIED adds the signature
    #   existing_borrow   0.00          loan outstanding, deducted from the borrowing
    #                                   base to give Net Excess Availability
    #   facility_limit    2,000,000     shown in the header and scales the waterfall
    #   currency          CAD           label only: amounts are always CAD
    #   disclaimer        (see models)  red legal text in the footer
    #
    # palette=ReportPalette(...)        certificate and chart colours
    # customer_palette=CustomerPalette(...)  ranked customer colours in the charts
    return ReportSettings(
        input_source=Path("invoices_real_2.csv"),
        fx_fetcher=fetch_usd_cad_series,
        certificate=CertificateParams(
            status=ReportStatus.PRO_FORMA,
            existing_borrow=Decimal("0.00"),
            facility_limit=Decimal("2000000.00"),
        ),
    )


if __name__ == "__main__":
    main()
