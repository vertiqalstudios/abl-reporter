"""One-call report pack: the Borrowing Base Certificate PDF plus every chart."""

from dataclasses import replace
from pathlib import Path

from .certificate import generate_certificate
from .fxdata import format_fx_note, load_fx_market
from .importer import load_invoices_from_csv
from .models import ReportSettings
from .report import generate_all_reports


def generate_report_pack(settings: ReportSettings | None = None) -> dict[str, Path]:
    """Loads invoices once and writes the certificate and all charts to `output_dir`.

    Rates are loaded once too, so the certificate and charts convert identically.
    Returns output paths keyed by name, with the PDF under "certificate".
    """
    cfg = settings if settings is not None else ReportSettings()
    invoices = tuple(load_invoices_from_csv(cfg.input_source))

    as_of = cfg.resolve_as_of()
    first_date = min((inv.invoice_date for inv in invoices), default=as_of)
    market = load_fx_market(cfg.fx_fetcher, first_date, as_of, cfg.fx_bank_spread)
    priced = replace(cfg, fx=cfg.fx if cfg.fx is not None else market.provider)

    certificate = generate_certificate(priced, invoices, fx_note=format_fx_note(market))
    charts = generate_all_reports(priced, invoices)
    return {"certificate": certificate, **charts}
