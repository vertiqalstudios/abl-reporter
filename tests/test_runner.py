from datetime import date
from decimal import Decimal

import pandas as pd

from abl.models import Invoice, LendingCriteria
from abl.runner import run_collateral_report


def test_runner_dataframe_generation() -> None:
    invoices = [
        Invoice(
            f"Client {c}", date(2025, 1, 1), date(2025, 1, 31), Decimal("2500"), "CAD"
        )
        for c in ("A", "B", "C", "D")
    ]
    reports, df = run_collateral_report(
        invoices=invoices,
        start_date=date(2025, 2, 1),
        end_date=date(2025, 2, 28),
        criteria=LendingCriteria(fx_reserve_pct=Decimal("0.00")),
    )

    assert len(reports) == 4
    assert len(df) == 4
    assert list(df.columns) == [
        "week",
        "eligible_collateral",
        "ineligible_collateral",
        "net_available_borrow",
    ]
    assert pd.api.types.is_datetime64_any_dtype(df["week"])
    assert df["eligible_collateral"].iloc[0] == 10000.0
    assert df["ineligible_collateral"].iloc[0] == 0.0
    assert df["net_available_borrow"].iloc[0] == 8000.0
