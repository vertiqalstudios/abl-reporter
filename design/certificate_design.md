# Borrowing Base Certificate (BBC) Report Design Document

## 1. Executive Summary & Objective

The Borrowing Base Certificate (BBC) is a critical compliance instrument within Asset-Backed Lending (ABL). It calculates and certifies the pledged collateral base and corresponding credit availability as of a specified valuation date.

This document formalizes the architectural specifications, financial calculation rules, visual design language, currency breakdown logic, and typography standards for the single-page landscape PDF certificate generator implemented in `abl/certificate.py`.

---

## 2. Core Financial Mathematics & Valuation Logic

### 2.1 Collateral Availability Flow
The Borrowing Base calculation adheres to standard commercial credit facility agreements:

$$\text{Gross AR Balance} = \sum \text{Open Invoices Converted to CAD}$$

$$\text{Total Ineligible Collateral} = \text{Contra} + \text{Aged Overdue} + \text{Cross-Default Taint} + \text{Debtor Concentration Excess}$$

$$\text{Net Eligible Collateral} = \text{Gross AR Balance} - \text{Total Ineligible Collateral}$$

$$\text{Effective Advance Rate} = \text{Contractual Advance Rate} - \text{Dilution Reserve Rate}$$

$$\text{Gross Borrowing Base} = \text{Net Eligible Collateral} \times \text{Effective Advance Rate}$$

$$\text{Net Excess Availability} = \max(0, \text{Gross Borrowing Base} - \text{Existing Loan Drawn})$$

### 2.2 Original Currency Breakdown (USD / CAD)
ABL portfolios frequently issue invoices in both Canadian Dollars (CAD) and United States Dollars (USD). To provide total audit transparency:
- Each open invoice retains its original transaction currency (`CAD` or `USD`).
- For each currency, the generator aggregates:
  - **Original Face Amount**: Total unadjusted face value in original currency.
  - **FX Benchmark Rate**: Official exchange rate applied to convert to base CAD (e.g., $1.4000\text{ CAD/USD}$ for USD; $1.0000$ for CAD).
  - **CAD Equivalent Balance**: Converted valuation contributing to total collateral.
  - **Portfolio Share**: Percentage contribution of that currency to total gross collateral.

### 2.3 Debtor Concentration Caps & Rollup
- Maximum debtor concentration percentage (e.g., 30.0% of Gross Eligible Collateral).
- To preserve strict single-page PDF presentation without table clipping or page-breaks:
  - The Top 5 debtors by open balance are listed individually with their balance, portfolio percentage, and compliance/breach status.
  - All debtors beyond the Top 5 are consolidated into a summary row: `Other Debtors (N)` with aggregate balance and share percentage.

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

## 4. Single-Page Landscape Layout Architecture

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ [Vertiqal Studios Branding]    Borrowing Base Certificate        [ STATUS BADGE ]      │
│ As of: YYYY-MM-DD • Run: YYYY-MM-DD HH:MM UTC • Facility Limit: $X,XXX,XXX CAD         │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ PARAMETER STRIP: Advance Rate | Dilution | Effective % | Concentration Cap | FX Rates │
├───────────────────────────────────────────┬────────────────────────────────────────────┤
│ COLUMN 1: AVAILABILITY LEDGER             │ COLUMN 2A: ORIGINAL CURRENCY BREAKDOWN     │
│ • Gross Accounts Receivable Balance       │ • CAD Collateral (Face, Rate, CAD Equiv)   │
│ • Less: Ineligibility Deductions          │ • USD Collateral (Face, Rate, CAD Equiv)   │
│   - Contra & Affiliates                   │ • Total Portfolio Distribution             │
│   - Aged Overdue (>90d / >180d)           ├────────────────────────────────────────────┤
│   - Cross-Default Taint (>50%)            │ COLUMN 2B: DEBTOR CONCENTRATION SCHEDULE   │
│   - Debtor Concentration Excess           │ • Top 1 to 5 Individual Debtors            │
│ • Net Eligible Collateral                 │ • "Other Debtors (N)" Summary Rollup       │
│ • Gross Borrowing Base                    │ • Stacked Proportional Distribution Bar    │
│ • Less: Existing Loan Drawn               │                                            │
│ • NET EXCESS AVAILABILITY                 │                                            │
│ [ Availability Waterfall Vector Bar ]     │                                            │
├───────────────────────────────────────────┴────────────────────────────────────────────┤
│ OFFICER COMPLIANCE CERTIFICATION & SIGNATURE BLOCK (for CERTIFIED status)              │
│ "I hereby certify that the above calculations are true, accurate, and computed in..."   │
│ Signature: _______________________   Title: CFO / Auth Officer   Date: _______________ │
├────────────────────────────────────────────────────────────────────────────────────────┤
│ FOOTER: Verification Audit Trail • Confidential Bank Workpaper • Page 1 of 1           │
│ RED SMALL-CAPS LEGAL DISCLAIMER                                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Technical Specifications

### 5.1 CLI Interface
The certificate generator can be executed directly as a standalone Python module:
```bash
python3 -m abl.certificate \
  --input data/invoices.csv \
  --as-of 2026-02-01 \
  --facility-limit 5000000 \
  --existing-borrow 1250000 \
  --status CERTIFIED \
  --output output/bbc_certificate.pdf
```

### 5.2 Key Data Structures
- `CertificateParams`: Controls as-of date, run timestamp, facility limit, existing loan drawn, report status (`PRO_FORMA`, `DRAFT`, `CERTIFIED`), company name, and currency.
- `CurrencyBreakdown`: Holds currency code, original face amount, exchange rate to CAD, converted CAD amount, and portfolio share.
- `DebtorConcentration`: Holds client name, CAD balance, portfolio share %, and whether the balance breaches the contractual concentration threshold.
