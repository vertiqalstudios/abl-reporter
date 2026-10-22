# ABL Collateral Monitoring Engine

Asset-Backed Lending (ABL) collateral evaluation and borrowing base engine.

## Requirements
Written for Python 3.14. May be backwards compatible, but honestly - doesn't need to be.

## License
Copyright (c) 2026 Vertiqal Studios Corporation & Columbia Westminster Pte Ltd. All Rights Reserved.

## Generating the report
Install once, then run from the directory that holds your invoice CSV:

```bash
uv venv --python 3.14
uv pip install -e .
uv run abl-report        # or: uv run python -m abl
```

This reads `invoices.csv` and writes the Borrowing Base Certificate PDF
(`bbc_certificate.pdf`) and all five charts to `output/`, printing each path. The CSV
needs the columns `Client Name, Invoice Date, Due Date, Invoice Amount, Currency,
Invoice Paid Date`. There are no command-line flags; a missing CSV exits with a "CSV file not found" message.

### As-of date
One as-of date drives the certificate and every chart. It defaults to the **Friday of
the current week** and is set with `ReportSettings(as_of=...)`. Anything after it is
ignored: invoices dated later are left out, and a payment dated later still counts as
unpaid. Charts run weekly (Fridays) over the `chart_weeks` (default 52, 1 year)
before the as-of date, or from the first invoice if the history is shorter. Invoices
before the window still count, so DSO and taint rules see the full history.

### Custom settings
Configure by building a `ReportSettings` object in your own script:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path

from abl import CertificateParams, ReportSettings, ReportStatus, generate_report_pack

settings = ReportSettings(
    input_source=Path("data/invoices.csv"),
    output_dir=Path("reports/2026-10-23"),
    as_of=date(2026, 10, 23),
    certificate=CertificateParams(
        status=ReportStatus.CERTIFIED,
        existing_borrow=Decimal("250000.00"),
        facility_limit=Decimal("2000000.00"),
    ),
)
outputs = generate_report_pack(settings)  # {"certificate": ..., "collateral": ..., ...}
```

`ReportSettings` also takes `top_n_customers` (default 10) and `criteria` (`LendingCriteria`: advance rate, concentration
cap, taint rules), `palette`, `customer_palette` (`CustomerPalette`: chart colours for
the largest customers by rank, separate from the certificate's `ReportPalette`) and `fx`
(an `FXProvider`; defaults to a pinned 1 USD = 1.44 CAD). To print
the paths as the command does, pass the same object to `abl.__main__.main(settings)`.

### FX pricing and volatility
Receivables are priced at **spot on the report date**: each weekly snapshot (and the
certificate) values open USD invoices at that date's rate. An invoice already paid by then
is priced at its **settlement date** rate instead (`abl.fx.valuation_date`). The bank
spread never touches valuation; the 2.5% FX reserve is the buffer for that.

`python -m abl` and `abl-report` set `fx_fetcher=fetch_usd_cad_series`, which pulls daily
USD/CAD from the Bank of Canada Valet API (`FXUSDCAD`, no key, a daily average rather than
a closing rate). The certificate prints the spot rate, its source and fixing date, and the
30-day annualised realised volatility under the currency table. A date with no fixing uses
the latest earlier one.

An `FXProvider` carries a `source` and a `rate_date(as_of)`. `SeriesFXProvider` also has
`conversion_rate(...)`, which applies `bank_spread` for actual conversions only.
Without `fx_fetcher` (the default for a bare `ReportSettings()`) the pinned
`make_fixed_fx_provider()` rate (1 USD = 1.44 CAD) is used; so is an explicit `fx`.

If the fetch fails or doesn't cover the invoice history, the run continues on the pinned
rate, labelled "Pinned 1.4400, live unavailable" on the certificate with volatility `N/A`.
`output/abl.log` holds the detail (URL, timing, error, response snippet); warnings also go
to stderr.

### Invoice detail pages
Page 2 onward of the certificate is an audit log of the gross collateral: every invoice
open at the as-of date, in two columns grouped by debtor (A-Z, then issue date). Each row
shows the invoice id, issue and due dates (days past due), face amount as booked, gross
and eligible CAD, and a status of Eligible, Haircut n% or Ineligible. Ineligible and
haircut rows say why beneath (the aged limit, the debtor's overdue ratio against the
taint threshold, or its share of eligible against the concentration cap). An invoice shows
the first covenant it fails (contra, aged, then taint). A header strip reconciles gross,
ineligible and eligible to page 1; invoices issued this week are starred, and invoices
settled this week are listed at their settlement-date rate.

The CSV's optional `Invoice Key` column becomes the invoice id shown here.

### Data checks

The importer and engine handle awkward rows as follows (details in `output/abl.log`):

- **Credit notes** (negative amounts) come off eligible collateral at full face and
  never raise borrowing capacity; the borrowing base is floored at zero.
- **Paid before issued:** the paid date is floored to the issue date (warning). A paid
  invoice is never collateral.
- **Due before issued:** accepted as given, logged as an error.
- **Duplicate `Invoice Key`:** warning only; both rows load.
- **Currencies** other than CAD and USD stop the run with an `FXConversionError` naming
  the invoice.

## Running Tests
```bash
uv run pytest
```

## Authorship
v0.02+ - Maintained by @fadesibert
v0.01 - Created by Gemini / Architect @fadesibert
