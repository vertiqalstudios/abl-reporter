# System Flow: Inputs, Outputs and Run Sequence

How `abl-report` (`python -m abl`) turns an invoice extract into the Borrowing Base
Certificate and charts. Diagrams are Mermaid and render on GitHub. The certificate's
layout and maths are in [certificate_design.md](certificate_design.md).

## 1. Inputs and outputs

```mermaid
flowchart LR
    csv[/"Invoice CSV<br/>(input_source)"/]
    boc[/"Bank of Canada Valet API<br/>series FXUSDCAD (network)"/]
    cfg[/"ReportSettings<br/>criteria, as_of, palettes,<br/>chart_weeks, top_n_customers,<br/>certificate params"/]

    run["abl-report<br/>python -m abl"]

    pdf[("output/bbc_certificate.pdf<br/>page 1 certificate,<br/>page 2+ invoice detail")]
    pngs[("output/*.png<br/>collateral, dso, top_n,<br/>customer_ar, customer_ar_stream")]
    log[("output/abl.log<br/>DEBUG detail")]
    term["Terminal<br/>output paths on stdout,<br/>warnings on stderr"]

    csv --> run
    boc -. "optional, falls back<br/>to pinned 1.44" .-> run
    cfg --> run
    run --> pdf
    run --> pngs
    run --> log
    run --> term
```

| Input | Where it comes from | Notes |
| --- | --- | --- |
| Invoice CSV | `ReportSettings.input_source` (`Path`, string or open text stream). The CLI default is set in `build_default_settings()` in `abl/__main__.py`. | Required columns: `Client Name, Invoice Date, Due Date, Invoice Amount, Currency, Invoice Paid Date`. Optional: `Invoice Key`. Dates are ISO `YYYY-MM-DD`. |
| USD/CAD rates | Bank of Canada Valet API, only when `fx_fetcher` is set. | A fetch failure or a coverage gap falls back to the pinned rate, labelled on the certificate. |
| Settings | `ReportSettings` (CLI: `build_default_settings()`). | Covenants, reserves, as-of date, palettes, chart window, certificate status and loan figures. |

| Output | Written by | Notes |
| --- | --- | --- |
| `bbc_certificate.pdf` | `certificate.py`, `detail_page.py` | Page 1 is the certificate; page 2 onward is the per-invoice audit log and settled-this-week list. |
| 5 chart PNGs | `report.py` | `collateral_chart`, `dso_chart`, `top_n_chart`, `customer_ar_chart`, `customer_ar_stream_chart`. A chart with no data is skipped. |
| `abl.log` | `__main__.configure_logging` | Rewritten each run. Rate fetches, skipped rates, data warnings and errors. |
| stdout / stderr | `__main__.main` | One `name: path` line per output. WARNING and above also go to stderr. |

## 2. Run sequence

```mermaid
flowchart TD
    A["main(settings)<br/>__main__.py"] --> B["configure_logging<br/>output/abl.log"]
    B --> C["generate_report_pack<br/>pack.py"]
    C --> D["load_invoices_from_csv<br/>importer.py<br/>parse, floor paid-before-issued,<br/>alert due-before-issued,<br/>warn on duplicate keys"]
    D --> E["require_supported_currencies<br/>CAD and USD only,<br/>else FXConversionError"]
    E --> F["resolve as-of date<br/>settings.as_of or this week's Friday"]
    F --> G{"explicit settings.fx?"}
    G -- yes --> H["use it"]
    G -- no --> I["load_fx_market<br/>fxdata.py<br/>live BoC series, or pinned<br/>rate on any failure"]
    H --> J["format_fx_note<br/>spot, source, fixing date,<br/>30-day realised volatility"]
    I --> J
    J --> K["generate_certificate<br/>certificate.py"]
    J --> L["generate_all_reports<br/>report.py"]
    K --> M[("bbc_certificate.pdf")]
    L --> N[("5 chart PNGs")]
```

Both branches get the same invoices, as-of date and FX provider, so the certificate and
the charts always agree on prices.

### 2a. Certificate branch

```mermaid
flowchart TD
    K1["generate_bbc -> evaluate_snapshot<br/>engine.py"] --> K2["CollateralReport<br/>totals and one InvoiceLine<br/>per open invoice"]
    K3["calculate_debtor_concentrations<br/>calculate_currency_breakdown<br/>settled_in_week"] --> K4
    K2 --> K4["generate_certificate_pdf<br/>two passes: the first counts pages,<br/>the second prints Page n of N"]
    K2 --> K5["detail_page.py<br/>two-column invoice log"]
    K5 --> K4
```

### 2b. Chart branch

```mermaid
flowchart TD
    L0["chart window<br/>chart_weeks of Fridays,<br/>or since the first invoice"] --> L1["run_collateral_report<br/>weekly evaluate_snapshot"]
    L0 --> L2["compute_weekly_dso<br/>timeseries.py"]
    L0 --> L3["customer_ar_balances<br/>Top N by open balance"]
    L1 --> L4["collateral_chart"]
    L2 --> L5["dso_chart"]
    L3 --> L6["top_n_chart"]
    L3 --> L7["customer_ar_chart"]
    L3 --> L8["customer_ar_stream_chart"]
```

Invoices before the window still count: they supply opening AR, DSO history and the
taint ratios.

## 3. Collateral engine (`evaluate_snapshot`)

```mermaid
flowchart TD
    E1["all invoices"] --> E2["keep invoices open at as-of<br/>issued, and not yet paid"]
    E2 --> E3["price in CAD<br/>spot at as-of; settlement date if paid"]
    E3 --> E4{"negative amount?"}
    E4 -- "credit note" --> E9
    E4 -- invoice --> E5["screen, first failure wins:<br/>1 contra account<br/>2 aged past max_overdue_days<br/>3 cross-default taint"]
    E5 -- fails --> E6["ineligible at full value"]
    E5 -- passes --> E7["concentration cap<br/>debtor over its share of eligible<br/>is prorated; excess ineligible"]
    E7 --> E8["eligible collateral"]
    E8 --> E9["less credit notes at full face<br/>floored at zero"]
    E9 --> E10["x (advance rate - dilution - FX reserve)"]
    E10 --> E11["net available borrow"]
```

The certificate then subtracts `existing_borrow` (a static figure in `CertificateParams`;
there is no loan ledger yet) to give Net Excess Availability.

## 4. Module map

| Module | Role |
| --- | --- |
| `__main__.py` | CLI entry point, logging, default settings |
| `pack.py` | One call: load, validate, price, then certificate and charts |
| `importer.py` | CSV to `Invoice`, with data checks |
| `models.py` | Dataclasses and settings (`ReportSettings`, `LendingCriteria`, ...) |
| `rules.py` | Pure covenant predicates and the borrowing-base arithmetic |
| `engine.py` | Snapshot evaluation and per-invoice audit lines |
| `fx.py`, `fxdata.py` | FX providers, currency checks, Bank of Canada fetch and volatility |
| `activity.py` | Invoices settled in the snapshot week |
| `certificate.py`, `detail_page.py` | PDF page 1 and the detail pages |
| `timeseries.py`, `runner.py`, `report.py` | Weekly DSO, AR and collateral series, and the charts |
| `bbc.py` | Thin wrapper that evaluates one snapshot for the certificate |
