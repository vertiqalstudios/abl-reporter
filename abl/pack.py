"""One-call report pack: the Borrowing Base Certificate PDF plus every chart."""

from pathlib import Path

from .certificate import generate_certificate
from .importer import load_invoices_from_csv
from .models import ReportSettings
from .report import generate_all_reports


def generate_report_pack(settings: ReportSettings | None = None) -> dict[str, Path]:
    """Loads invoices once and writes the certificate and all charts to `output_dir`.

    Returns output paths keyed by name, with the PDF under "certificate".
    """
    cfg = settings if settings is not None else ReportSettings()
    invoices = tuple(load_invoices_from_csv(cfg.input_source))

    certificate = generate_certificate(cfg, invoices)
    charts = generate_all_reports(cfg, invoices)
    return {"certificate": certificate, **charts}
