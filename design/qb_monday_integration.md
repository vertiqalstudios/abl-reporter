# Integration Plan: QuickBooks, Monday.com and an Audited Database

Planning notes, 2026-10-22. No integration code exists yet. Contains no credentials.
It does name business entities, so treat it as confidential like the rest of the repo.

## 1. Goal

Replace the hand-made invoice CSV with a daily, audited feed:

```
QuickBooks Online  --\
                      >-- extractors --> Postgres (raw, append-only, audited)
Monday.com boards  --/                       |
                                             v
                                  conformed views --> abl-reporter
```

- The reporter stops reading a CSV and reads a view of the database. The CSV loader stays
  for tests and offline runs.
- Extractors and the reporter are decoupled: the database is the only interface.
- Acceptance test: a report built from the database equals the report built from the CSV
  on the same as-of dates (we reconciled 69 historical snapshots with zero mismatches
  before, so the harness idea already works).

## 2. Status and next steps

| Item | Status |
| --- | --- |
| Monday.com board schema and coverage | Done (section 4) |
| QuickBooks Online production access | Blocked: waiting for the CFO (East Coast, about 10:00 ET) to re-grant access |
| Intuit sandbox baseline | Docs read (section 5); live sandbox needs a developer app and token from you (section 6) |
| Database schema design | Not started; waits for the QuickBooks sample |
| Monday `Invoice Number` process change | Idea agreed in principle; needs a workflow design (section 7) |

**Tomorrow:** (1) create the Intuit developer app and sandbox token (section 6); (2) pull
sandbox samples; (3) once the CFO restores access, pull about 20 real invoices with their
payments and credit memos and settle the open questions in section 8.

## 3. Decisions taken

- **Debtor:** the agency, or the client when the deal is direct (about 20% of deals).
  Concentration, Top N and contra rules use this derived debtor. The QuickBooks customer is
  probably the debtor already; confirm with real data.
- **Currency:** invoices are owed and paid in the invoice currency (USD or CAD), so the
  existing spot-at-as-of FX pricing stands. Booked in a Canadian home-currency company.
  Confirm against real invoices.
- **Companies:** several QuickBooks companies: Vertiqal Studios Corp (Canada), Gamelancer
  Inc (US), Omnia Media (US), possibly more. Columbia Westminster is not one of them.
- **Out-of-scope revenue:** some receipts are not invoiced receivables and must be
  filtered out of collateral: Gamelancer collects funds from Snap, and Omnia Media earns
  revenue from the GAM license. Neither is invoiced.
- **Audit meaning:** change data capture inside the database: every row change is recorded
  with who and when. Implemented as audit tables or triggers written by us, not Postgres
  logical replication.
- **Time:** all timestamps stored in UTC (`timestamptz`). Business dates (invoice, due,
  paid) stay calendar dates.
- **Schedule:** sync daily, about 21:00 UTC. Not yet decided whether it runs on AWS ECS
  scheduling or something more portable (deferred; the company is not all-in on AWS).
- **Access and isolation:** Postgres runs in a container on AWS ECS with its data on EBS
  (not RDS). The app gets a dedicated credential with read and write but no schema admin,
  even in the dev database. You apply the schema; we deliver versioned SQL migration files.
  There will be a dev database first, then production.
- **Backups:** the container's backups are infrequent today. We need a backup plan for
  this logical database before holding audit history.
- **Paid date:** keep both the customer payment date and the bank deposit date in the
  database. Proposed use: payment date for DSO, deposit date (cash received) for the
  borrowing base and loan paydown. The facility terms will settle which; none exist yet.
- **Unmatched invoices:** "cross that bridge when we come to it".
- **Security:** the data is confidential and may be material non-public information. A
  read-only Monday token was shared in the session; rotate it. Production secrets belong in
  a secret manager, never in the repo.

## 4. Monday.com findings

Read with a read-only token, schema first, then counts only. No names or amounts were
printed. The token was not saved.

### Sales Invoicing board (id 18432255883)
125 items in monthly groups from September 2025 to September 2026, with 436 subitems
(board 18432259873, "Subitems of Sales Invoicing"). It is a campaign and billing
worksheet, not an invoice register.

- **Invoice Number, Invoice Date and Payment Due Date are empty on all 125 items**, though
  123 are marked "Invoiced". There is no join key to QuickBooks today. None of the Monday
  items matched any of the 81 invoice keys in the current CSV.
- Client Name is filled on 51 of 125 items (18 distinct), Agency on 114 (34 distinct,
  11 blank), Advertiser on 123 (39 distinct). They are free text. Agency never equals the
  client or the advertiser.
- Currency is on every item: 85 USD, 40 CAD. Payment Terms on 85: Net 30 (74), Net 45 (5),
  Net 60 (6). Order # / PO on 41.
- Billing Status: Invoiced 123, Ready to Invoice 2. Billing Method has 7 values, Program
  Type 3 (Direct 67, PG 19, PMP 23, blank 16).
- Subitems hold product lines: product, line start and end, entity, impressions sold,
  CPM, budget sold, impressions delivered, delivered value, billable (local currency),
  full delivery flag, notes.
- **Entity** (subitem text) values, by number of lines: Custom/Social 102, IION 71, PW 67,
  blank 65, S71 18, "?" 18, EG 12, LEGO AM 12, GAM 11, "IION, 360" 11, "GAM + MC" 10,
  Misfits 10, "IION, MC, GAM, AM" 9, "Airtory, IION" 6, "Airtory, IION, 360" 5, VS 3,
  Bent Pixels 2, SUZY 2, 360 2. These look like products, brands or inventory sources, not
  billing entities. Their meaning is still to be confirmed with you.

### Sales CRM board (id 18431584848)
729 items: lifecycle stage, type, deal values, won revenue, HubSpot ID, contacts. No
invoice fields. May later serve as a canonical customer name list. Out of scope for now.

## 5. QuickBooks Online API baseline (from public docs; verify in the sandbox)

Source: Intuit SDK documentation and published schemas. The live developer portal pages
did not render for us.

- **Invoice:** `Id`, `SyncToken`, `DocNumber` (up to 21 characters), `TxnDate` (issue
  date), `DueDate` (derived from the customer's sales terms if not entered),
  `TotalAmt`, `Balance` (unpaid; 0 means paid), `HomeTotalAmt`, `HomeBalance`,
  `CurrencyRef`, `ExchangeRate` (home units per one foreign unit; multicurrency only),
  `CustomerRef`, `SalesTermRef`, `CustomField` (up to 3), `PrivateNote`,
  `CustomerMemo`, `MetaData.CreateTime` and `LastUpdatedTime`, and `LinkedTxn[]` listing
  related Payments and CreditMemos by id and type. A PO number field may exist (reported up
  to 15 characters); verify.
- **Payment:** `TxnDate`, `TotalAmt`, `UnappliedAmt`, `CurrencyRef`, `ExchangeRate`,
  `CustomerRef`, `PaymentMethodRef`, `DepositToAccountRef` (defaults to Undeposited
  Funds), `LinkedTxn[]` and lines that link to the invoices it pays. **A Payment has no
  deposit date.** The deposit date should live on a separate Deposit record that groups
  payments; check how Deposit links back to Payment.
- **CreditMemo, Customer (including sub-customers or jobs), Deposit, Preferences
  (multicurrency settings), CompanyInfo:** all expected to be readable; field detail to
  confirm.
- **PDF:** I believe an invoice PDF can be downloaded, but the structured fields already
  hold everything we need. A PDF would only be an audit exhibit.
- **Change data capture:** covers the last 30 days and up to 1,000 objects per response.
  Deleted records show up in it. Older history needs a full re-pull.
- **Limits and cost:** throttled calls return HTTP 429 (wait 60 seconds). Per-minute
  numbers differ between sources; check Intuit's limits page. Intuit introduced
  App Partner Program tiers in July 2025; confirm whether an internal single-company app
  is affected.
- **Auth:** OAuth 2.0. Access tokens last about an hour. Refresh tokens rotate, so the
  newest one must always be stored. Sources disagree on the refresh lifetime (100 days or
  about five years). Each real company needs its own one-time consent by an admin.

Immediate conclusions: due date, currency and exchange rate should not need imputing;
the paid date needs Payment plus Deposit records; credit memos exist as their own record
type, which matches our credit-note policy.

### 5a. Sandbox findings (live, 2026-10-22)

Sandbox company: Canadian, home currency CAD, multicurrency **on**, with USD, EUR and HKD
active. Read-only calls only. It holds 16 invoices, 9 payments, 1 deposit, 0 credit memos
and 29 customers, all in CAD. Sandbox data is fake; the sample JSON stays in the
scratchpad and is not committed.

- **Auth:** the authorization-code exchange worked. Access tokens last 3,600 seconds; the
  refresh token lasts 8,726,400 seconds (about 101 days, rolling), which settles the
  conflicting figures in section 5. A refresh returns a new refresh token.
- **Invoice fields confirmed:** `Id`, `SyncToken`, `DocNumber`, `TxnDate`, `DueDate` (from
  `SalesTermRef`, Net 30 here), `TotalAmt`, `Balance`, `HomeTotalAmt`, `HomeBalance`,
  `CurrencyRef`, `ExchangeRate` (1 for CAD), `CustomerRef` (id and name), `LinkedTxn`,
  `CustomField` (empty here), `MetaData`. No `PONumber` field appeared.
- **Timestamps carry a local offset** (for example `-07:00`), so they must be converted to
  UTC on the way in. `TxnDate` and `DueDate` are plain dates.
- **Paid date:** an invoice's `LinkedTxn` lists `Payment` ids (also `Estimate` and
  `ReimburseCharge`). The paid date is the linked `Payment.TxnDate` of the payment that
  takes `Balance` to 0. A Payment has only `TxnDate` and `DepositToAccountRef`; no deposit
  date.
- **Deposits:** here every payment goes straight to the Chequing account, and the one
  Deposit has two manual lines with **no link to any payment**. So in this sandbox payment
  date is the only paid date. Whether your real company uses Undeposited Funds plus
  batched Deposits, and whether Deposit lines link back to payments, must be checked in
  the real data.
- **Overpayments:** two payments exceed their invoice (`UnappliedAmt` 454.83 and 974.63).
  The invoice shows `Balance` 0 and the excess sits as an unapplied customer credit. The
  database should keep `UnappliedAmt`.
- **Exchange rates:** `GET exchangerate?sourcecurrencycode=USD&asofdate=...` returns a
  USD to CAD rate for a date (1.414157 on 2026-10-16). Useful as a cross-check on our Bank
  of Canada series; its source is not stated.
- **PDF:** `GET invoice/{id}/pdf` with `Accept: application/pdf` returned a valid PDF
  (18 KB). Confirmed; an audit exhibit only.
- **Change data capture:** the endpoint answers; nothing had changed in 30 days here.
### 5b. Scenarios created in the sandbox (all labelled `ABL-TEST`)

Written through the API to the sandbox only, then read back. Two test customers: a USD
agency (id 67) and a CAD agency (id 68). Invoices `ABL-T01` to `ABL-T07`:

| Case | What the API shows |
| --- | --- |
| USD invoice, unpaid (`T01`, USD 10,000) | `ExchangeRate` 1.422293 (the rate on the invoice date), `HomeTotalAmt` 14,222.93, `Balance` 10,000 in USD, `HomeBalance` 14,222.93 |
| USD part-paid (`T02`, USD 5,000, paid USD 2,000) | `Balance` 3,000 USD, `HomeBalance` 4,275.88, which is the balance at the **invoice** rate. The payment carries its own rate (1.42199) |
| USD fully paid, never deposited (`T04`) | `Balance` 0. The payment sits in Undeposited Funds and has **no link to any deposit** |
| CAD fully paid and deposited (`T03`) | The payment's `LinkedTxn` lists a `Deposit` (id 194, dated 2026-10-12), so payment date and deposit date are both recoverable |
| Credit memo applied (`T05` CAD 1,500, credit memo 400) | Applied by a payment with `TotalAmt` 0 whose lines link the invoice and the credit memo. Invoice `Balance` falls to 1,100 and its `LinkedTxn` shows that zero-dollar payment. The credit memo shows `Balance` 0 and **no link back** |
| Voided invoice (`T06`) | Still returned. `TotalAmt` and `Balance` become 0 and `PrivateNote` becomes "Voided - ..." |
| Deleted invoice (`T07`) | **Not returned** by a query. It appears only in change data capture with `status: Deleted` |

What this means for the extractor and the database:

1. **FX:** QuickBooks fixes the CAD value at the booking rate and keeps `Balance` in the
   invoice currency. The database should store the face amount, currency and exchange rate
   as booked. Our reporter still values open USD at spot on the as-of date and ignores
   QuickBooks' home amounts, though they are useful to reconcile against.
2. **Paid date and deposit date:** for each invoice, find the linked payments. The payment
   date is `Payment.TxnDate`. The deposit date is the `TxnDate` of the `Deposit` in that
   payment's `LinkedTxn`, if any; a payment still in Undeposited Funds has none yet.
3. **Credit memos:** read the zero-dollar payment's lines to see which credit memo was
   applied. A zero-dollar payment is not cash, so it must not set a payment date.
4. **Voids:** keep the row and flag it as void; exclude it from receivables.
5. **Deletions:** a deleted invoice cannot be found by querying. We must run change data
   capture at least every 30 days (daily is planned), and also treat any id present in our
   raw tables but missing from a full re-pull as deleted.

Still unconfirmed: whether real books use Undeposited Funds and batch deposits as in this
test, how customer hierarchies (sub-customers or jobs) look, and a purchase order field.

## 6. Intuit sandbox: what you need to do, then what I will do

You need your own Intuit developer login. The CFO is only needed for the real companies.

1. Sign in at developer.intuit.com and create an app with the Accounting scope.
2. Open the app's Keys and credentials (development tab). Add the OAuth 2.0 Playground
   redirect URI if the Playground asks for it.
3. Use or create a sandbox company. Prefer a Canadian one so the home currency is CAD, and
   enable multicurrency in its settings. Verify that both are possible; the home currency
   usually cannot be changed afterwards.
4. In the OAuth 2.0 Playground, authorise the app against the sandbox company and copy the
   **access token** and the **realm ID**. The token lasts about an hour.
5. Give me the token and realm ID (sandbox data is fake, so there is no confidentiality
   issue). I will not store them.

Then I would, read-only: read company info and preferences to confirm multicurrency; pull
invoices, payments, deposits, credit memos and customers; and try the change data capture
and PDF endpoints. To see how real situations are represented, create these in the
sandbox: a USD invoice in a CAD company, a part-paid and a fully paid invoice, a payment
that is later deposited on another date, a credit memo applied to an invoice, and a voided
and a deleted invoice. Sample JSON stays in the scratchpad and is not committed.

## 7. Making `Invoice Number` easy on the team

Monday is not where invoicing should live, but it is what the team uses. Options to
explore, cheapest first:

1. A Monday automation or form that prompts for the invoice number when an item is moved
   to "Invoiced".
2. Write the QuickBooks document number into Monday automatically, by matching a PO number
   or customer, amount and month, and flag unmatched ones for review.
3. A daily "invoices in QuickBooks with no Monday item" and "items marked Invoiced with no
   invoice number" exception list, so gaps are caught the next day.
4. Longer term, make QuickBooks the owner of invoicing and push invoice status to Monday.

## 8. Open questions

| # | Question | Who or what answers it |
| --- | --- | --- |
| 1 | Is the QuickBooks customer on an invoice the agency, the advertiser or either? | Real data |
| 2 | Are invoices owed in the invoice currency (so FX exposure is real), or owed in CAD? | Real data |
| 3 | How is "paid date" derived today (payment date or deposit date)? | You, and real data |
| 4 | How do we filter out-of-scope receipts (Snap, GAM license)? By account, item or company? | CFO |
| 5 | What do the Monday `Entity` values mean? "GAM" appears in several; the GAM license revenue is out of scope, so this may help the filter. | You |
| 6 | Which workflow populates the Monday invoice number (section 7)? | You and the team |
| 7 | Does Intuit's partner program or fee schedule affect an internal app? | Intuit docs |
| 8 | Where does the sync job run, and how are Postgres backups taken? | You |
| 9 | How many QuickBooks companies exist, and does each have multicurrency on? | CFO |

## 9. Proposed database shape (first thoughts)

- **Raw layer:** one table per source entity, append-only. Each row holds the source JSON,
  source id, `fetched_at` (UTC), a run id and a content hash. Rows are never updated or
  deleted; a changed or deleted source record is a new row.
- **Run log:** `extract_runs` records source, window, counts and status.
- **Audit:** triggers or audit tables record who or what changed any row, with UTC times.
- **Conformed layer:** views that give the reporter invoices as they were known at a
  chosen time (so old certificates can be reproduced), with payment and deposit dates,
  currency, exchange rate, company, and the derived debtor.
- **Debtor alias table:** maps free-text names (Monday) to one canonical debtor, with its
  own history.
- **Migrations:** plain versioned SQL files you apply as schema admin.
