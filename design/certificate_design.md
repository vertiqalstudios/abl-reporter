# Borrowing Base Certificate (BBC) Report Design Document

## 1. Executive Summary & Objective

The Borrowing Base Certificate (BBC) is a critical compliance instrument within Asset-Backed Lending (ABL). It calculates and certifies the pledged collateral base and corresponding credit availability as of a specified valuation date.

This document formalizes the architectural specifications, financial calculation rules, visual design language, currency breakdown logic, and typography standards for the landscape PDF certificate generator implemented in `abl/certificate.py`. Page 1 is the certificate itself; page 2 onward is a per-invoice audit log (`abl/detail_page.py`, section 4.1). For how the certificate fits into a full run (inputs, outputs, sequence) see [system_flow.md](system_flow.md).

---

## 2. Core Financial Mathematics & Valuation Logic

### 2.1 Collateral Availability Flow
The Borrowing Base calculation adheres to standard commercial credit facility agreements:

$$\text{Gross AR Balance} = \sum \text{Open Invoices Converted to CAD}$$

An invoice is open at the as-of date if it was issued on or before it and is unpaid (or paid after it). Credit notes (negative amounts) are not collateral: they come off Net Eligible Collateral at full face (section 2.4).

$$\text{Total Ineligible Collateral} = \text{Contra} + \text{Aged Overdue} + \text{Cross-Default Taint} + \text{Debtor Concentration Excess}$$

$$\text{Net Eligible Collateral} = \max\left(0,\ \text{Gross AR Balance} - \text{Total Ineligible Collateral}\right)$$

$$\text{Effective Advance Rate} = \text{Contractual Advance Rate} - \text{Dilution Reserve Rate} - \text{FX Reserve Rate}$$

Defaults: 85.0% - 2.5% - 2.5% = 80.0%. The FX reserve is a flat cushion for USD/CAD movement, applied to all collateral.

$$\text{Gross Borrowing Base} = \text{Net Eligible Collateral} \times \text{Effective Advance Rate}$$

$$\text{Net Excess Availability} = \max(0, \text{Gross Borrowing Base} - \text{Existing Loan Drawn})$$

### 2.2 Original Currency Breakdown (USD / CAD)
ABL portfolios frequently issue invoices in both Canadian Dollars (CAD) and United States Dollars (USD). To provide total audit transparency:
- Each open invoice retains its original transaction currency. Only `CAD` and `USD` are supported; any other currency stops the run with an `FXConversionError` naming the invoice.
- For each currency, the generator aggregates:
  - **Original Face Amount**: Total unadjusted face value in original currency.
  - **FX Rate**: The rate applied to convert to base CAD (e.g., $1.4000\text{ CAD/USD}$ for USD; $1.0000$ for CAD).
  - **CAD Equivalent Balance**: Converted valuation contributing to total collateral.
  - **Portfolio Share**: Percentage contribution of that currency to total gross collateral.

### 2.3 Debtor Concentration Caps & Rollup
- Maximum debtor concentration percentage (default 30.0% of Gross Eligible Collateral). A debtor above the cap keeps the same fraction of each invoice and the rest is Concentration Excess.
- To keep page 1 on a single page without table clipping or page-breaks:
  - The Top 5 debtors by open balance are listed individually with their balance, portfolio percentage, and compliance/breach status.
  - All debtors beyond the Top 5 are consolidated into a summary row: `Other Debtors (N)` with aggregate balance and share percentage.

### 2.4 Screening Order and Credit Notes
Each open invoice is screened in a fixed order and reports only the first covenant it fails:
1. **Contra / affiliate**: debtor name matches `contra_clients` (case-insensitive).
2. **Aged past due**: more than `max_overdue_days` (default 90) past the due date.
3. **Cross-default taint**: the debtor has at least `taint_threshold_pct` (default 50%) of its open balance more than `taint_past_due_days` (default 90) past due. A small new invoice from a tainted debtor is excluded; an invoice that keeps the debtor under the threshold is not.

Invoices that pass are then subject to the concentration cap. Credit notes bypass screening and the cap, so they cannot change another invoice's treatment, and they always reduce capacity (including those against contra debtors).

### 2.5 FX Pricing
- Open invoices are priced at the **spot rate on the as-of date**; an invoice already paid by then is priced at its **settlement date** rate.
- Rates are the Bank of Canada `FXUSDCAD` daily average, fetched live. A date with no fixing uses the latest earlier one (up to 10 days old). If the fetch fails or does not cover the history, the run uses the pinned 1.44 and the certificate says "Pinned 1.4400, live unavailable".
- Page 1 prints the spot rate, its source and fixing date, and the 30-day annualised realised volatility (sample standard deviation of daily log returns x sqrt(252)), or `N/A` when there is too little data.
- The bank spread never affects valuation. It applies only in `SeriesFXProvider.conversion_rate`, which nothing in the report uses yet (reserved for loan payoff modelling).

---

## 3. Visual Identity, Grid & Typography

### 3.1 Design Philosophy: Tufte Financial Minimalism
- **Zero Decorative Chrome**: Eliminate non-informative gradients, heavy fills, and visual clutter. All borders, dividers, and tints serve structural grouping.
- **High Data Density with Generous Breathing Room**: Margins fixed at 36pt (0.5 in). Content organized into distinct horizontal and vertical rhythm zones.
- **Tabular Alignment**: All currency figures and percentages are right-aligned with fixed tabular numeral presentation.

### 3.2 Typography Standards
- **Font Family**: **Helvetica Neue** (`Helvetica Neue`, `Helvetica-Bold`, `Helvetica-Oblique`), with automatic fallback to standard PostScript `Helvetica` if system font registration is unmapped.
- **Hierarchy**:
  - Document Header: 18pt bold, leading 21pt.
  - Section Titles / Column Headings: 9.5pt bold, leading 13pt.
  - Primary Ledger Rows: 8.5pt bold / regular, leading 12pt.
  - Metadata & Parameter Ribbons: 7.5pt bold / 6.5pt regular, leading 10pt.
  - Legal Disclaimer & Footnotes: 6.5pt bold uppercase.

### 3.3 Color Palette & Semantic Meaning
| Token | Hex | Semantic Role |
| :--- | :--- | :--- |
| `primary` | `#0F172A` / `#1E293B` | Structural grid, titles, primary financial figures |
| `accent` | `#2563EB` | Net availability, certified badges, key indicators |
| `alert` | `#DC2626` | Ineligible deductions, concentration breaches, legal disclaimers |
| `muted` | `#64748B` | Labels, metadata, captions, secondary notes |
| `subtle` | `#94A3B8` | Subdued borders, benchmarks, comparison bars |
| `bg_subtle` | `#F8FAFC` | Parameter strip background, alternating table row shading |

---

## 4. Landscape Layout Architecture

### 4.1 Page 1: The Certificate

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ [Vertiqal Studios Branding]    Borrowing Base Certificate        [ STATUS BADGE ]      │
│ As of: YYYY-MM-DD • Run: YYYY-MM-DD HH:MM UTC • Facility Limit: $X,XXX,XXX CAD         │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ PARAMETER STRIP: Invoice Window | Advance - Dilution - FX = Eff. | Cap | Covenants   │
├───────────────────────────────────────────┬────────────────────────────────────────────┤
│ COLUMN 1: AVAILABILITY LEDGER             │ COLUMN 2A: ORIGINAL CURRENCY BREAKDOWN     │
│ • Gross Accounts Receivable Balance       │ • CAD Collateral (Face, Rate, CAD Equiv)   │
│ • Less: Ineligibility Deductions          │ • USD Collateral (Face, Rate, CAD Equiv)   │
│   - Contra & Affiliates                   │ • Total Portfolio Distribution             │
│   - Aged Past Due Over Limit              │ FX note: spot, source, fixing date, vol    │
│                                           ├────────────────────────────────────────────┤
│   - Cross-Default Taint                   │ COLUMN 2B: DEBTOR CONCENTRATION SCHEDULE   │
│   - Debtor Concentration Excess           │ • Top 1 to 5 Individual Debtors            │
│ • Net Eligible Collateral                 │ • "Other Debtors (N)" Summary Rollup       │
│ • x Effective Advance Rate                │ • AR and eligible distribution bars        │
│ • Gross Borrowing Base                    │ • Ineligibility breakdown by reason        │
│ • Less: Existing Loan Drawn               │                                            │
│ • NET EXCESS AVAILABILITY                 │                                            │
│ [ Availability Waterfall Vector Bar ]     │                                            │
├───────────────────────────────────────────┴────────────────────────────────────────────┤
│ OFFICER COMPLIANCE CERTIFICATION & SIGNATURE BLOCK (for CERTIFIED status)              │
│ "I hereby certify that the above calculations are true, accurate, and computed in..."   │
│ Signature: _______________________   Title: CFO / Auth Officer   Date: _______________ │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ FOOTER: Status • Vertiqal Studios ABL Reporter • Page 1 of N                           │
│ RED SMALL-CAPS LEGAL DISCLAIMER                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Page 2 onward: Invoice Detail
Present whenever there is at least one open invoice. A header strip reconciles gross, ineligible and eligible to page 1, then every open invoice is listed in two columns grouped by debtor (A-Z, then issue date):
- Invoice id (the CSV `Invoice Key`), issue date (starred if issued this week), due date with days past due, face amount as booked, gross CAD, eligible CAD, and a status: `Eligible`, `Haircut n%`, `Ineligible` or `Credit note`.
- A line beneath each non-eligible row gives the reason with the numbers that tripped it.
- A grand total row, then **Settled This Week**: invoices paid in the window (as-of minus 7 days, as-of], at their settlement-date rate.
- The footer reads "Page n of N" with the status line and disclaimer. N is measured by a first rendering pass.

---

## 5. Technical Specifications

### 5.1 Programmatic Interface & Execution
The whole pack (certificate and charts) is driven by an immutable `ReportSettings` contract. The command line entry point builds its settings in `build_default_settings()` (`abl/__main__.py`); edit that function to change what `abl-report` does. See the README for the full option list.
```bash
uv run abl-report          # or: uv run python -m abl
```

Programmatic use:
```python
from abl import ReportSettings, CertificateParams, ReportStatus, generate_report_pack

# Certificate and all five charts
outputs = generate_report_pack(ReportSettings(
    input_source=Path("data/invoices.csv"),
    output_dir=Path("output"),
    as_of=date(2026, 2, 6),
    certificate=CertificateParams(
        facility_limit=Decimal("5000000.00"),
        existing_borrow=Decimal("1250000.00"),
        status=ReportStatus.CERTIFIED,
    ),
))

# Certificate only (no live rates unless `fx` or `fx_fetcher` is set)
from abl import generate_certificate
pdf_path = generate_certificate(ReportSettings(input_source=Path("data/invoices.csv")))
```
The as-of date lives on `ReportSettings` (default: the Friday of the current week), not on `CertificateParams`, so the certificate and charts always share it.

### 5.2 Key Data Structures
- `ReportSettings`: Unified root configuration: `criteria`, `palette`, `customer_palette`, `certificate`, `chart_weeks`, `top_n_customers`, `fx`, `fx_fetcher`, `input_source` (`Path | str | TextIO`), `output_dir` and `as_of`.
- `LendingCriteria`: Covenant definitions: `advance_rate`, `dilution_reserve_pct`, `fx_reserve_pct`, `max_client_concentration_pct`, `max_overdue_days`, `taint_past_due_days`, `taint_threshold_pct`, `cross_default_enabled`, `contra_clients`.
- `ReportPalette`: Visual tokens shared by the charts and the PDF. `CustomerPalette`: the seven-hue ranked customer colours used by the customer charts.
- `CertificateParams`: Report status (`PRO_FORMA`, `DRAFT`, `CERTIFIED`), facility limit, existing loan drawn, currency, disclaimer and run timestamp.
- `Invoice`: One receivable as imported. `InvoiceLine`: its audit row (gross, eligible, status, reason). `CollateralReport`: snapshot totals plus the lines.
- `FXProvider`: Protocol with `source`, `rate_date(as_of)` and a call returning the mid-market rate; implemented by `FixedFXProvider` and `SeriesFXProvider`.
- `CurrencyBreakdown`: Currency code, original face amount, exchange rate to CAD, converted CAD amount, and portfolio share.
- `DebtorConcentration`: Client name, CAD balance, portfolio share %, and whether the balance breaches the contractual concentration threshold.
