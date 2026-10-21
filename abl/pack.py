"""One-call report pack: the Borrowing Base Certificate PDF plus every chart."""

from dataclasses import replace
from pathlib import Path

from .certificate import generate_certificate
from .fx import make_fixed_fx_provider
from .fxdata import format_fx_note, load_volatility
from .importer import load_invoices_from_csv
from .models import ReportSettings
from .report import generate_all_reports


def generate_report_pack(settings: ReportSettings | None = None) -> dict[str, Path]:
    """Loads invoices once and writes the certificate and all charts to `output_dir`.

    Collateral converts at `fx` (pinned by default); live rates only feed volatility.
    Returns output paths keyed by name, with the PDF under "certificate".
    """
    cfg = settings if settings is not None else ReportSettings()
    invoices = tuple(load_invoices_from_csv(cfg.input_source))

    as_of = cfg.resolve_as_of()
    fx = cfg.fx if cfg.fx is not None else make_fixed_fx_provider()
    reading = load_volatility(cfg.fx_fetcher, as_of)
    note = format_fx_note(reading, fx("USD", "CAD", as_of))

    priced = replace(cfg, fx=fx)
    certificate = generate_certificate(priced, invoices, fx_note=note)
    charts = generate_all_reports(priced, invoices)
    return {"certificate": certificate, **charts}
