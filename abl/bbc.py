"""Borrowing Base Certificate (BBC) report generation."""

from collections.abc import Sequence
from datetime import date

from .engine import evaluate_snapshot
from .fx import FXProvider, make_fixed_fx_provider
from .models import CollateralReport, Invoice, LendingCriteria


def generate_bbc(
    invoices: Sequence[Invoice],
    as_of: date,
    criteria: LendingCriteria | None = None,
    fx: FXProvider | None = None,
) -> CollateralReport:
    """Generates a Borrowing Base Certificate for collateral as of the given date.

    Pure functional pipeline delegating snapshot evaluation to the collateral engine.
    """
    resolved_criteria = criteria if criteria is not None else LendingCriteria()
    resolved_fx = fx if fx is not None else make_fixed_fx_provider()

    return evaluate_snapshot(
        invoices=invoices,
        snapshot_date=as_of,
        criteria=resolved_criteria,
        fx=resolved_fx,
    )
