from collections.abc import Sequence
from datetime import date, timedelta

import pandas as pd

from .engine import evaluate_snapshot
from .fx import FXProvider, make_fixed_fx_provider
from .models import CollateralReport, Invoice, LendingCriteria


def run_collateral_report(
    invoices: Sequence[Invoice],
    start_date: date,
    end_date: date,
    criteria: LendingCriteria | None = None,
    fx: FXProvider | None = None,
    snapshot_weekday: int = 4,
) -> tuple[list[CollateralReport], pd.DataFrame]:
    """Generates weekly snapshot reports and a summary pandas DataFrame."""
    if criteria is None:
        criteria = LendingCriteria()
    if fx is None:
        fx = make_fixed_fx_provider()

    days_ahead = (snapshot_weekday - start_date.weekday()) % 7
    curr = start_date + timedelta(days=days_ahead)

    reports: list[CollateralReport] = []
    rows: list[dict[str, object]] = []

    while curr <= end_date:
        rpt = evaluate_snapshot(invoices, curr, criteria, fx)
        reports.append(rpt)
        rows.append(
            {
                "week": pd.to_datetime(rpt.week),
                "eligible_collateral": float(rpt.eligible_amount_cad),
                "ineligible_collateral": float(rpt.ineligible_amount_cad),
                "net_available_borrow": float(rpt.net_available_borrow_cad),
            }
        )
        curr += timedelta(days=7)

    df = pd.DataFrame(rows)
    return reports, df
