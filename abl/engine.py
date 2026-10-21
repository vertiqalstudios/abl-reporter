from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal

from .fx import FXProvider, convert_to_cad
from .models import (
    CollateralReport,
    IneligibilityReason,
    IneligibilitySummary,
    Invoice,
    InvoiceWithReason,
    LendingCriteria,
)
from .rules import (
    calculate_concentration_cap,
    calculate_net_availability,
    check_aged_overdue,
    check_contra,
    check_cross_default,
    find_tainted_clients,
    is_invoice_open,
    summarize_ineligibles,
)


def _screen_invoices(
    open_invoices: Sequence[Invoice],
    snapshot_date: date,
    criteria: LendingCriteria,
    tainted_clients: set[str],
) -> tuple[list[InvoiceWithReason], list[Invoice]]:
    """Splits invoices into covenant-disqualified records and eligible candidates."""
    ineligible_records: list[InvoiceWithReason] = []
    eligible_candidates: list[Invoice] = []

    for inv in open_invoices:
        reason = (
            check_contra(inv.client_name, criteria.contra_clients)
            or check_aged_overdue(
                inv.due_date, snapshot_date, criteria.max_overdue_days
            )
            or (
                check_cross_default(inv.client_name, tainted_clients)
                if criteria.cross_default_enabled
                else None
            )
        )
        if reason is not None:
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
        else:
            eligible_candidates.append(inv)

    return ineligible_records, eligible_candidates


def _apply_concentration_cap(
    eligible_candidates: Sequence[Invoice],
    max_debtor_cap: Decimal,
    criteria: LendingCriteria,
) -> tuple[list[Invoice], list[InvoiceWithReason]]:
    """Prorates debtors above the concentration cap into eligible and excess parts."""
    ineligible_records: list[InvoiceWithReason] = []
    by_client: dict[str, list[Invoice]] = defaultdict(list)
    for inv in eligible_candidates:
        by_client[inv.client_name].append(inv)

    final_eligible: list[Invoice] = []

    for client, client_invs in by_client.items():
        client_total = sum((inv.invoice_amount for inv in client_invs), Decimal("0.00"))
        if client_total <= max_debtor_cap or max_debtor_cap == Decimal("0.00"):
            final_eligible.extend(client_invs)
            continue

        coverage_ratio = max_debtor_cap / client_total
        for inv in client_invs:
            eligible_portion = (inv.invoice_amount * coverage_ratio).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            ineligible_portion = inv.invoice_amount - eligible_portion

            final_eligible.append(
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
            ineligible_records.append(
                InvoiceWithReason(
                    client_name=inv.client_name,
                    invoice_date=inv.invoice_date,
                    due_date=inv.due_date,
                    invoice_amount=inv.invoice_amount,
                    currency=inv.currency,
                    paid_date=inv.paid_date,
                    invoice_id=inv.invoice_id,
                    eligible_balance=eligible_portion,
                    ineligible_balance=ineligible_portion,
                    reason=IneligibilityReason.CONCENTRATION_EXCESS,
                    reason_detail=(
                        f"Concentration cap exceeded: debtor total {client_total} CAD "
                        f"exceeds {criteria.max_client_concentration_pct * 100:.0f}% "
                        f"limit ({max_debtor_cap} CAD)"
                    ),
                )
            )

    return final_eligible, ineligible_records


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
    """
    week_dt = datetime.combine(snapshot_date, time(23, 59, 59))
    open_invoices = [
        convert_to_cad(inv, snapshot_date, fx)
        for inv in invoices
        if is_invoice_open(inv, snapshot_date)
    ]

    if not open_invoices:
        return CollateralReport(
            week=week_dt,
            eligible_amount_cad=Decimal("0.00"),
            ineligible_amount_cad=Decimal("0.00"),
            net_available_borrow_cad=Decimal("0.00"),
            ineligible_summary=IneligibilitySummary(),
        )

    tainted_clients: set[str] = set()
    if criteria.cross_default_enabled:
        tainted_clients = find_tainted_clients(
            invoices=open_invoices,
            as_of=snapshot_date,
            taint_days=criteria.taint_past_due_days,
            threshold_pct=criteria.taint_threshold_pct,
            contra_clients=criteria.contra_clients,
        )

    ineligible_records, eligible_candidates = _screen_invoices(
        open_invoices, snapshot_date, criteria, tainted_clients
    )

    gross_eligible = sum(
        (inv.invoice_amount for inv in eligible_candidates), Decimal("0.00")
    )
    max_debtor_cap = calculate_concentration_cap(
        gross_eligible, criteria.max_client_concentration_pct
    )

    final_eligible, capped_records = _apply_concentration_cap(
        eligible_candidates, max_debtor_cap, criteria
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
    )
