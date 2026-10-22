# Borrowing Base Certificate Design

## 1. Purpose

The Borrowing Base Certificate (BBC) states the collateral a borrower has pledged and the
credit available against it at an as-of date. `abl/certificate.py` builds it as a landscape
US Letter PDF. Page 1 is the certificate; page 2 onward is an invoice-by-invoice audit log
(`abl/detail_page.py`). For how a run fits together (inputs, outputs, sequence) see
[system_flow.md](system_flow.md).

## 2. Borrowing base calculation

### 2.1 Availability

An invoice is open at the as-of date if it was issued on or before that date and is unpaid
(or was paid after it). Open invoices are converted to CAD (section 2.5), then:

$$\text{Gross AR} = \sum \text{open invoices in CAD}$$

$$\text{Total Ineligible} = \text{Contra} + \text{Aged} + \text{Cross-Default Taint} + \text{Concentration Excess}$$

$$\text{Net Eligible} = \max\left(0,\ \text{Gross AR} - \text{Total Ineligible}\right)$$

$$\text{Effective Advance Rate} = \text{Advance} - \text{Dilution Reserve} - \text{FX Reserve}$$

$$\text{Gross Borrowing Base} = \text{Net Eligible} \times \text{Effective Advance Rate}$$

$$\text{Net Excess Availability} = \max\left(0,\ \text{Gross Borrowing Base} - \text{Existing Loan Drawn}\right)$$

With the defaults the effective rate is 85.0% - 2.5% - 2.5% = 80.0%. The FX reserve is a flat
cushion against USD/CAD movement, applied to all collateral.

Credit notes (negative amounts) are not collateral. They come off Net Eligible at full
face, so they only ever reduce capacity, including credit notes against contra debtors.
They skip screening and the concentration cap, so they cannot change how other invoices
are treated.

### 2.2 Screening

Each invoice is tested in this order and shows only the first test it fails:

1. **Contra / affiliate:** the debtor is in `contra_clients` (case-insensitive).
2. **Aged:** more than `max_overdue_days` (default 90) past its due date.
3. **Cross-default taint:** the debtor has at least `taint_threshold_pct` (default 50%) of
   its open balance more than `taint_past_due_days` (default 90) past due. Every invoice
   from a tainted debtor is ineligible, including new ones. A new invoice large enough to
   bring the overdue share below the threshold avoids the taint.

### 2.3 Concentration cap

No debtor may exceed `max_client_concentration_pct` (default 30%) of the eligible
collateral left after screening. A debtor over the cap keeps the same fraction of each
invoice, and the remainder is Concentration Excess.

Page 1 lists the five largest debtors by open balance, with balance, share of AR, share of
eligible and a compliance status. All others are rolled into `Other Debtors (N)`.

### 2.4 Currency breakdown

Only CAD and USD are supported. Any other currency stops the run with an
`FXConversionError` naming the invoice. For each currency page 1 shows:

- **Face:** total in the original currency.
- **Rate:** the rate to CAD ($1.0000$ for CAD).
- **Collateral (CAD):** the converted amount.
- **Share:** its percentage of total collateral.

### 2.5 FX pricing

- An open invoice is priced at the **spot rate on the as-of date**. An invoice already paid
  by then is priced at its **settlement date** rate.
- Rates are the Bank of Canada `FXUSDCAD` daily average, fetched live. A date with no
  fixing takes the latest earlier one, up to 10 days old. If the fetch fails or does not
  cover the invoice history, the run uses a pinned 1.44 and the certificate says
  "Pinned 1.4400, live unavailable".
- Page 1 prints the spot rate, its source and fixing date, and the 30-day annualised
  realised volatility (sample standard deviation of daily log returns $\times \sqrt{252}$).
  It prints `N/A` when there are fewer than 10 daily returns.
- The bank spread never affects valuation. It applies only in
  `SeriesFXProvider.conversion_rate`, which the report does not use yet. It is held for
  loan payoff modelling.

## 3. Page layout

### 3.1 Page 1: the certificate

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ VERTIQAL STUDIOS      Borrowing Base Certificate           [ STATUS BADGE ]              │
│ As of: YYYY-MM-DD • Run: YYYY-MM-DD HH:MM UTC • Facility Limit • Base Currency           │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ PARAMETER STRIP: Invoice Window | Advance - Dilution - FX = Eff. | Cap | Covenants       │
├───────────────────────────────────────────┬──────────────────────────────────────────────┤
│ 1. COLLATERAL AVAILABILITY LEDGER         │ 2. ORIGINAL CURRENCY BREAKDOWN (USD / CAD)   │
│ Gross Accounts Receivable Balance         │ CAD and USD rows: face, rate, CAD, share     │
│ Less ineligible collateral:               │ FX note: spot, source, date, volatility      │
│   - Contra & affiliate accounts           │                                              │
│   - Aged past due over limit              │ 3. DEBTOR AR CONCENTRATION                   │
│   - Cross-default tainted debtors         │ Top 5 debtors, then Other Debtors (N)        │
│   - Debtor concentration excess           │ Columns: balance, % AR, % Elig., status      │
│ Total Ineligible Collateral               │ AR and eligible distribution bars            │
│ Net Eligible Collateral                   │ Ineligibility breakdown by reason            │
│   x Effective Advance Rate                │                                              │
│ Gross Borrowing Base                      │                                              │
│ Less: Current Outstanding Loan Drawn      │                                              │
│ NET EXCESS AVAILABILITY                   │                                              │
│ [ Availability waterfall bar ]            │                                              │
├───────────────────────────────────────────┴──────────────────────────────────────────────┤
│ BORROWER COMPLIANCE CERTIFICATION (signature block; CERTIFIED status only)               │
│ Authorized Officer: ______   Title: CFO / Treasurer   Signature Date: ______             │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ Definitions of % AR and % Elig. (footnotes)                                              │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ FOOTER: Status • Vertiqal Studios ABL Reporter • Page 1 of N                             │
│ LEGAL DISCLAIMER (red, uppercase)                                                        │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

The footnotes define **% AR** (a debtor's balance as a share of gross AR) and **% Elig.**
(its share of gross eligible collateral, before the concentration cap).

### 3.2 Page 2 onward: invoice detail

Present whenever there is at least one open invoice. A header strip reconciles gross,
ineligible and eligible collateral to page 1. Every open invoice is then listed in two
columns, grouped by debtor (A-Z, then issue date):

- Invoice id (the CSV `Invoice Key`), issue date (starred if issued this week), due date
  with days past due, face amount as booked, gross CAD and eligible CAD.
- A status: `Eligible`, `Haircut n%`, `Ineligible` or `Credit note`.
- For any row that is not fully eligible, a line beneath gives the reason and the numbers
  behind it.
- A total row, then **Settled This Week**: invoices paid in the window (as-of minus 7 days,
  as-of], at their settlement-date rate.

The footer on every page reads "Page n of N" with the status line and disclaimer. A first
rendering pass measures N.

## 4. Visual identity

The design follows Tufte-style minimalism: no decorative fills or gradients, borders only
where they group, and currency figures and percentages right-aligned.

- **Page:** US Letter landscape; margins 36pt left and right, 20pt top, 16pt bottom.
- **Font:** Helvetica Neue, falling back to Helvetica when it is not installed.
- **Sizes:** title 17/20pt bold; section headings 8.5/11pt bold; body 8/10.5pt; sub-headings
  7/9pt italic; parameter strip values 7/9.5pt bold; footnotes, footer and disclaimer 6 to
  6.5pt.

| Token | Hex | Used for |
| ------- | ------- | ------------------------------------------ |
| `primary` | `#0F172A` | Titles, rules, primary figures |
| `secondary` | `#334155` | Chart axis labels and secondary series |
| `muted` | `#64748B` | Labels, captions, notes |
| `subtle` | `#94A3B8` | Quiet chart bars, such as the existing loan |
| `accent` | `#2563EB` | Net availability, key indicators |
| `alert` | `#DC2626` | Ineligible deductions, breaches, the disclaimer |
| `border` | `#E2E8F0` | Table rules and boxes |
| `bg_subtle` | `#F8FAFC` | Parameter strip, table header and signature backgrounds |

The same `ReportPalette` colours the charts. `CustomerPalette` adds seven ranked hues for
the customer charts.

## 5. Interface and data structures

### 5.1 Running it

`uv run abl-report` (or `uv run python -m abl`) reads its settings from
`build_default_settings()` in `abl/__main__.py`; edit that function to change what it does.
The README lists every option.

From Python:

```python
from datetime import date
from decimal import Decimal
from pathlib import Path

from abl import CertificateParams, ReportSettings, ReportStatus
from abl import generate_certificate, generate_report_pack

settings = ReportSettings(
    input_source=Path("data/invoices.csv"),
    output_dir=Path("output"),
    as_of=date(2026, 2, 6),
    certificate=CertificateParams(
        facility_limit=Decimal("5000000.00"),
        existing_borrow=Decimal("1250000.00"),
        status=ReportStatus.CERTIFIED,
    ),
)
outputs = generate_report_pack(settings)    # certificate and all five charts
pdf_path = generate_certificate(settings)   # certificate only
```

The as-of date lives on `ReportSettings` (default: the Friday of the current week), so the
certificate and charts always share it. Without `fx` or `fx_fetcher` the pinned rate is used.

### 5.2 Key data structures

- `ReportSettings`: the root configuration (`criteria`, `palette`, `customer_palette`,
  `certificate`, `chart_weeks`, `top_n_customers`, `fx`, `fx_fetcher`, `input_source`,
  `output_dir`, `as_of`).
- `LendingCriteria`: the covenants and reserves.
- `CertificateParams`: report status (`PRO_FORMA`, `DRAFT`, `CERTIFIED`), facility limit,
  existing loan drawn, currency, disclaimer and run timestamp.
- `Invoice`: one receivable as imported. `InvoiceLine`: its audit row (gross, eligible,
  status, reason). `CollateralReport`: the snapshot totals plus the lines.
- `FXProvider`: a protocol with `source`, `rate_date(as_of)` and a call that returns the
  mid-market rate. `FixedFXProvider` and `SeriesFXProvider` implement it.
- `CurrencyBreakdown` and `DebtorConcentration`: the rows behind the page 1 tables.
