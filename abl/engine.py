from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .fx import FXProvider, convert_to_cad, valuation_date
from .models import (
    CollateralReport,
    IneligibilityReason,
    IneligibilitySummary,
    Invoice,
    InvoiceLine,
    InvoiceWithReason,
    LendingCriteria,
    LineStatus,
)
from .rules import (
    calculate_concentration_cap,
    calculate_net_availability,
    check_aged_overdue,
    check_contra,
    check_cross_default,
    client_taint_ratios,
    find_tainted_clients,
    is_invoice_open,
    summarize_ineligibles,
)

SNAPSHOT_WINDOW_DAYS = 7  # an invoice issued within this many days is "new"


@dataclass(frozen=True, slots=True)
class _Priced:
    """An open invoice as booked, its CAD conversion and the rate used."""

    source: Invoice
    cad: Invoice
    fx_rate: Decimal


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """Snapshot-wide facts needed to describe each invoice's outcome."""

    as_of: date
    criteria: LendingCriteria
    taint_ratios: Mapping[str, Decimal]


def _price(inv: Invoice, snapshot_date: date, fx: FXProvider) -> _Priced:
    """Converts an open invoice to CAD, keeping the booked invoice and the rate."""
    rate = fx(inv.currency, "CAD", valuation_date(inv, snapshot_date))
    return _Priced(inv, convert_to_cad(inv, snapshot_date, fx), rate)


def _status_of(eligible: Decimal, ineligible: Decimal) -> LineStatus:
    """Classifies by amounts, so rounding can't yield a zero-percent haircut."""
    if ineligible == Decimal("0.00"):
        return LineStatus.ELIGIBLE
    if eligible == Decimal("0.00"):
        return LineStatus.INELIGIBLE
    return LineStatus.HAIRCUT


def _make_line(
    priced: _Priced,
    snap: _Snapshot,
    eligible: Decimal,
    reason: IneligibilityReason | None,
    detail: str,
) -> InvoiceLine:
    """Builds the audit row for one invoice from its eligible CAD amount."""
    src = priced.source
    gross = priced.cad.invoice_amount
    ineligible = gross - eligible
    status = _status_of(eligible, ineligible)
    is_eligible = status is LineStatus.ELIGIBLE
    window_start = snap.as_of - timedelta(days=SNAPSHOT_WINDOW_DAYS)
    return InvoiceLine(
        client_name=src.client_name,
        invoice_id=src.invoice_id,
        invoice_date=src.invoice_date,
        due_date=src.due_date,
        days_past_due=max(0, (snap.as_of - src.due_date).days),
        currency=src.currency,
        face_amount=src.invoice_amount,
        fx_rate=priced.fx_rate,
        gross_cad=gross,
        eligible_cad=eligible,
        ineligible_cad=ineligible,
        status=status,
        reason=None if is_eligible else reason,
        detail="" if is_eligible else detail,
        is_new=src.invoice_date > window_start,
    )


def _screen_reason(
    inv: Invoice,
    snapshot_date: date,
    criteria: LendingCriteria,
    tainted_clients: set[str],
) -> IneligibilityReason | None:
    """Returns the first covenant an invoice fails: contra, aged, then taint."""
    return (
        check_contra(inv.client_name, criteria.contra_clients)
        or check_aged_overdue(inv.due_date, snapshot_date, criteria.max_overdue_days)
        or (
            check_cross_default(inv.client_name, tainted_clients)
            if criteria.cross_default_enabled
            else None
        )
    )


def _covenant_detail(
    reason: IneligibilityReason, priced: _Priced, snap: _Snapshot
) -> str:
    """Words the covenant an invoice failed, with the numbers that tripped it."""
    criteria = snap.criteria
    match reason:
        case IneligibilityReason.CONTRA_ACCOUNT:
            return "Contra / affiliate account"
        case IneligibilityReason.AGED_OVERDUE:
            days = (snap.as_of - priced.source.due_date).days
            return f"{days}d past due; limit {criteria.max_overdue_days}d"
        case _:
            ratio = snap.taint_ratios.get(priced.source.client_name, Decimal("0"))
            taint_days = criteria.taint_past_due_days
            return (
                f"Tainted debtor: {ratio:.0%} of balance >{taint_days}d"
                f" (limit {criteria.taint_threshold_pct:.0%})"
            )


def _screen_invoices(
    open_invoices: Sequence[_Priced],
    snap: _Snapshot,
    tainted_clients: set[str],
) -> tuple[list[InvoiceWithReason], list[_Priced], list[InvoiceLine]]:
    """Splits invoices into covenant-disqualified records and eligible candidates."""
    ineligible_records: list[InvoiceWithReason] = []
    eligible_candidates: list[_Priced] = []
    lines: list[InvoiceLine] = []

    for priced in open_invoices:
        inv = priced.cad
        reason = _screen_reason(inv, snap.as_of, snap.criteria, tainted_clients)
        if reason is None:
            eligible_candidates.append(priced)
            continue
        ineligible_records.append(
            InvoiceWithReason(
                client_name=inv.client_name,
                invoice_date=inv.invoice_date,
                due_date=inv.due_date,
                invoice_amount=inv.invoice_amount,
                currency=inv.currency,
                paid_date=inv.paid_date,
                invoice_id=inv.invoice_id,
                eligible_balance=Decimal("0.00"),
                ineligible_balance=inv.invoice_amount,
                reason=reason,
                reason_detail=f"Disqualified by covenant: {reason.value}",
            )
        )
        detail = _covenant_detail(reason, priced, snap)
        lines.append(_make_line(priced, snap, Decimal("0.00"), reason, detail))

    return ineligible_records, eligible_candidates, lines


def _cap_client(
    client_invs: Sequence[_Priced],
    max_debtor_cap: Decimal,
    snap: _Snapshot,
    gross_eligible: Decimal,
) -> tuple[list[Invoice], list[InvoiceWithReason], list[InvoiceLine]]:
    """Prorates one debtor's invoices to the cap, or keeps them whole under it."""
    criteria = snap.criteria
    client_total = sum((p.cad.invoice_amount for p in client_invs), Decimal("0.00"))
    if client_total <= max_debtor_cap or max_debtor_cap == Decimal("0.00"):
        kept = [
            _make_line(p, snap, p.cad.invoice_amount, None, "") for p in client_invs
        ]
        return [p.cad for p in client_invs], [], kept

    coverage_ratio = max_debtor_cap / client_total
    share = client_total / gross_eligible
    detail = (
        f"Debtor {share:.1%} of eligible vs {criteria.max_client_concentration_pct:.0%}"
        f" cap; {coverage_ratio:.1%} of each invoice kept"
    )
    eligible_invoices: list[Invoice] = []
    records: list[InvoiceWithReason] = []
    lines: list[InvoiceLine] = []
    for priced in client_invs:
        inv = priced.cad
        eligible_portion = (inv.invoice_amount * coverage_ratio).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        eligible_invoices.append(
            Invoice(
                client_name=inv.client_name,
                invoice_date=inv.invoice_date,
                due_date=inv.due_date,
                invoice_amount=eligible_portion,
                currency=inv.currency,
                paid_date=inv.paid_date,
                invoice_id=inv.invoice_id,
            )
        )
        records.append(
            InvoiceWithReason(
                client_name=inv.client_name,
                invoice_date=inv.invoice_date,
                due_date=inv.due_date,
                invoice_amount=inv.invoice_amount,
                currency=inv.currency,
                paid_date=inv.paid_date,
                invoice_id=inv.invoice_id,
                eligible_balance=eligible_portion,
                ineligible_balance=inv.invoice_amount - eligible_portion,
                reason=IneligibilityReason.CONCENTRATION_EXCESS,
                reason_detail=(
                    f"Concentration cap exceeded: debtor total {client_total} CAD "
                    f"exceeds {criteria.max_client_concentration_pct * 100:.0f}% "
                    f"limit ({max_debtor_cap} CAD)"
                ),
            )
        )
        lines.append(
            _make_line(
                priced,
                snap,
                eligible_portion,
                IneligibilityReason.CONCENTRATION_EXCESS,
                detail,
            )
        )
    return eligible_invoices, records, lines


def _apply_concentration_cap(
    eligible_candidates: Sequence[_Priced],
    max_debtor_cap: Decimal,
    snap: _Snapshot,
    gross_eligible: Decimal,
) -> tuple[list[Invoice], list[InvoiceWithReason], list[InvoiceLine]]:
    """Prorates debtors above the concentration cap into eligible and excess parts."""
    by_client: dict[str, list[_Priced]] = defaultdict(list)
    for priced in eligible_candidates:
        by_client[priced.cad.client_name].append(priced)

    final_eligible: list[Invoice] = []
    records: list[InvoiceWithReason] = []
    lines: list[InvoiceLine] = []
    for client_invs in by_client.values():
        kept, capped, client_lines = _cap_client(
            client_invs, max_debtor_cap, snap, gross_eligible
        )
        final_eligible.extend(kept)
        records.extend(capped)
        lines.extend(client_lines)
    return final_eligible, records, lines


def _line_order(line: InvoiceLine) -> tuple[str, date, date, str]:
    """Orders lines by debtor (case-blind), then issue date, due date and id."""
    return (
        line.client_name.casefold(),
        line.invoice_date,
        line.due_date,
        line.invoice_id,
    )


def evaluate_snapshot(
    invoices: Sequence[Invoice],
    snapshot_date: date,
    criteria: LendingCriteria,
    fx: FXProvider,
) -> CollateralReport:
    """Evaluates collateral eligibility and net borrow availability for a snapshot date.

    Normalizes open invoices to CAD base currency, screens against covenants
    (contra accounts, maximum overdue aging, cross-default delinquency ratios),
    prorates debtor concentration excesses against the single-client threshold,
    and calculates effective borrowing capacity net of dilution and FX reserves.
    Each open invoice also gets an audit line saying how it fared.
    """
    week_dt = datetime.combine(snapshot_date, time(23, 59, 59))
    priced = [
        _price(inv, snapshot_date, fx)
        for inv in invoices
        if is_invoice_open(inv, snapshot_date)
    ]

    if not priced:
        return CollateralReport(
            week=week_dt,
            eligible_amount_cad=Decimal("0.00"),
            ineligible_amount_cad=Decimal("0.00"),
            net_available_borrow_cad=Decimal("0.00"),
            ineligible_summary=IneligibilitySummary(),
        )

    open_invoices = [p.cad for p in priced]
    ratios = client_taint_ratios(
        open_invoices,
        snapshot_date,
        criteria.taint_past_due_days,
        criteria.contra_clients,
    )
    snap = _Snapshot(snapshot_date, criteria, ratios)

    tainted_clients: set[str] = set()
    if criteria.cross_default_enabled:
        tainted_clients = find_tainted_clients(
            invoices=open_invoices,
            as_of=snapshot_date,
            taint_days=criteria.taint_past_due_days,
            threshold_pct=criteria.taint_threshold_pct,
            contra_clients=criteria.contra_clients,
        )

    ineligible_records, eligible_candidates, screened = _screen_invoices(
        priced, snap, tainted_clients
    )

    gross_eligible = sum(
        (p.cad.invoice_amount for p in eligible_candidates), Decimal("0.00")
    )
    max_debtor_cap = calculate_concentration_cap(
        gross_eligible, criteria.max_client_concentration_pct
    )

    final_eligible, capped_records, capped = _apply_concentration_cap(
        eligible_candidates, max_debtor_cap, snap, gross_eligible
    )
    ineligible_records.extend(capped_records)

    summary = summarize_ineligibles(ineligible_records)
    total_eligible = sum(
        (inv.invoice_amount for inv in final_eligible), Decimal("0.00")
    )
    total_ineligible = sum(
        (rec.ineligible_balance for rec in ineligible_records), Decimal("0.00")
    )
    net_borrow = calculate_net_availability(
        total_eligible,
        criteria.advance_rate,
        criteria.dilution_reserve_pct,
        criteria.fx_reserve_pct,
    )

    return CollateralReport(
        week=week_dt,
        eligible_amount_cad=total_eligible,
        ineligible_amount_cad=total_ineligible,
        net_available_borrow_cad=net_borrow,
        eligible_invoices=final_eligible,
        ineligible_invoices=ineligible_records,
        ineligible_summary=summary,
        lines=tuple(sorted([*screened, *capped], key=_line_order)),
    )
