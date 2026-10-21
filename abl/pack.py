"""One-call report pack: the Borrowing Base Certificate PDF plus every chart."""

from dataclasses import replace
from pathlib import Path

from .certificate import generate_certificate
from .fxdata import FXMarket, format_fx_note, load_fx_market
from .importer import load_invoices_from_csv
from .models import ReportSettings
from .report import generate_all_reports


def generate_report_pack(settings: ReportSettings | None = None) -> dict[str, Path]:
    """Loads invoices once and writes the certificate and all charts to `output_dir`.

    Collateral is priced by `fx`, else by live rates from `fx_fetcher`, else pinned.
    Returns output paths keyed by name, with the PDF under "certificate".
    """
    cfg = settings if settings is not None else ReportSettings()
    invoices = tuple(load_invoices_from_csv(cfg.input_source))

    as_of = cfg.resolve_as_of()
    first_date = min((inv.invoice_date for inv in invoices), default=as_of)
    market = (
        FXMarket(cfg.fx)
        if cfg.fx is not None
        else load_fx_market(cfg.fx_fetcher, first_date, as_of)
    )

    priced = replace(cfg, fx=market.provider)
    note = format_fx_note(market, as_of)
    certificate = generate_certificate(priced, invoices, fx_note=note)
    charts = generate_all_reports(priced, invoices)
    return {"certificate": certificate, **charts}
